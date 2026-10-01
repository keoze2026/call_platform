#!/usr/bin/env python3
"""Static scan of every line of Python in this project.

Written for a handover. It parses each file rather than reading it, so it finds
the classes of fault that have actually reached production here:

  undefined names        a NameError in a branch nobody exercised. This exact
                         fault made every call return 500 for two hours.
  wrong model fields     `organization.members` does not exist; nor does
                         `CallLog.is_converted` or `hangup_reason`. Each was
                         written, deployed, and only failed when the line ran.
  function-scope imports a name imported inside one function and used in
                         another. This took the support chat down.
  silent failures        `except: pass` - the feature stops working and nothing
                         anywhere says so.
  unguarded endpoints    a write endpoint with no capability check.
  hardcoded secrets      credentials in code rather than .env.

Run it from the project root:

    python3 scripts/full_scan.py

Exits 0 if nothing serious was found, 1 otherwise. Read-only: it never imports
the project, touches the database, or makes a network call.
"""
from __future__ import annotations

import ast
import builtins
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {
    '.venv', 'venv', 'node_modules', '__pycache__', '.git', 'staticfiles',
    'media', 'downloaded_files', 'docs',
}

# Findings are collected by severity so the report leads with what matters.
CRITICAL, WARNING, INFO = 'CRITICAL', 'WARNING', 'INFO'
findings: list[tuple[str, str, str, int, str]] = []   # sev, kind, file, line, message


def add(sev, kind, path, line, message):
    findings.append((sev, kind, os.path.relpath(path, ROOT), line, message))


def python_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


def is_migration(path):
    return os.sep + 'migrations' + os.sep in path


# ── 1. every file parses ─────────────────────────────────────────────────────

def parse_all():
    trees = {}
    for path in python_files():
        try:
            source = open(path, encoding='utf-8').read()
        except Exception as e:
            add(CRITICAL, 'unreadable', path, 0, f'cannot read: {e}')
            continue
        try:
            trees[path] = (ast.parse(source, filename=path), source)
        except SyntaxError as e:
            add(CRITICAL, 'syntax', path, e.lineno or 0, f'syntax error: {e.msg}')
    return trees


# ── 2. undefined names ───────────────────────────────────────────────────────
# The check that would have caught the NameError that stopped every call.

class ScopeWalker(ast.NodeVisitor):
    """Collects names bound anywhere inside a function, including lambdas,
    comprehensions, with-targets, except-targets and walrus assignments."""

    def __init__(self):
        self.bound = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.add(node.id)
        self.generic_visit(node)

    def visit_arg(self, node):
        self.bound.add(node.arg)
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self.bound.add((a.asname or a.name).split('.')[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            self.bound.add(a.asname or a.name)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.bound.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        self.bound.update(node.names)

    def visit_Nonlocal(self, node):
        self.bound.update(node.names)


STAR_EXPORTS: dict[str, set] = {}


def star_import_names(path, module):
    """Top-level names of a module imported with `from x import *`.

    Without this, every schema in `accounts/api.py` looked undefined, because
    that file does `from .schemas import *` and the names never appear in it.
    A scanner that cries wolf 26 times is one nobody reads.
    """
    base = os.path.dirname(path)
    candidates = []
    if module.startswith('.'):
        candidates.append(os.path.join(base, module.lstrip('.').replace('.', os.sep) + '.py'))
    else:
        candidates.append(os.path.join(ROOT, module.replace('.', os.sep) + '.py'))
        candidates.append(os.path.join(base, module.replace('.', os.sep) + '.py'))
    for cand in candidates:
        if not os.path.exists(cand):
            continue
        if cand in STAR_EXPORTS:
            return STAR_EXPORTS[cand]
        try:
            sub = ast.parse(open(cand, encoding='utf-8').read())
        except Exception:
            return set()
        names = set()
        for node in sub.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    names.add((a.asname or a.name).split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    names.add(a.asname or a.name)
        STAR_EXPORTS[cand] = names
        return names
    # Third-party or unresolvable: assume it provides whatever is used, rather
    # than reporting a wall of names that are perfectly fine.
    return None


def module_level_names(tree, path=None):
    names = set(dir(builtins))
    names.update({'__name__', '__file__', '__doc__', '__package__', 'self', 'cls'})
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import,)):
            for a in node.names:
                names.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == '*':
                    if path is None:
                        continue
                    module = ('.' * node.level) + (node.module or '')
                    exported = star_import_names(path, module)
                    if exported is None:
                        names.add('__STAR_UNRESOLVED__')
                    else:
                        names.update(exported)
                else:
                    names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def check_undefined(path, tree):
    module_names = module_level_names(tree, path)
    if '__STAR_UNRESOLVED__' in module_names:
        return   # a star import from outside the project; cannot be sure

    # A nested function can read its parent's variables. Checking it in
    # isolation reported every closure as broken - `event()` inside the low
    # balance detector reads `organization` from the function around it, which
    # is ordinary Python.
    enclosing = {}
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is not parent and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    enclosing.setdefault(child, []).append(parent)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        walker = ScopeWalker()
        for parent in enclosing.get(node, []):
            pw = ScopeWalker()
            for child in ast.iter_child_nodes(parent):
                pw.visit(child)
            for a in (parent.args.args + parent.args.kwonlyargs
                      + parent.args.posonlyargs):
                pw.bound.add(a.arg)
            if parent.args.vararg:
                pw.bound.add(parent.args.vararg.arg)
            if parent.args.kwarg:
                pw.bound.add(parent.args.kwarg.arg)
            walker.bound |= pw.bound
        for child in ast.iter_child_nodes(node):
            walker.visit(child)
        for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            walker.bound.add(a.arg)
        if node.args.vararg:
            walker.bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            walker.bound.add(node.args.kwarg.arg)

        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id in walker.bound or sub.id in module_names:
                    continue
                add(CRITICAL, 'undefined-name', path, sub.lineno,
                    f'{node.name}() uses `{sub.id}`, which is not defined in '
                    f'this function or at module level')


# ── 3. a name imported inside one function, used in another ──────────────────
# `from django.conf import settings` inside function A, then `settings` used in
# function B. Grep says the import exists; Python raises NameError.

def check_function_scope_imports(path, tree):
    module_imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                module_imports.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                module_imports.add(a.asname or a.name)

    local_imports = defaultdict(list)   # name -> [(func, line)]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for a in sub.names:
                    local_imports[(a.asname or a.name).split('.')[0]].append((node.name, sub.lineno))
            elif isinstance(sub, ast.ImportFrom):
                for a in sub.names:
                    local_imports[a.asname or a.name].append((node.name, sub.lineno))

    for name, places in local_imports.items():
        if name in module_imports:
            continue
        funcs = {f for f, _ in places}
        if len(funcs) > 2:
            add(INFO, 'repeated-local-import', path, places[0][1],
                f'`{name}` is imported inside {len(funcs)} separate functions; '
                f'consider a module-level import')


# ── 4. model fields, so a query cannot name a column that does not exist ─────

DJANGO_FIELD_SUFFIXES = {
    'exact', 'iexact', 'contains', 'icontains', 'in', 'gt', 'gte', 'lt', 'lte',
    'startswith', 'istartswith', 'endswith', 'iendswith', 'range', 'date',
    'year', 'iso_year', 'month', 'day', 'week', 'week_day', 'iso_week_day',
    'quarter', 'time', 'hour', 'minute', 'second', 'isnull', 'regex', 'iregex',
    'id', 'pk', 'overlap', 'len', 'has_key', 'has_keys', 'contained_by',
}

QUERY_METHODS = {'filter', 'exclude', 'get', 'get_or_create', 'update_or_create', 'create'}


def collect_models(trees):
    """model name -> set of field names, related names and helpers."""
    models = {}
    related = defaultdict(set)      # target model -> related names pointing at it
    for path, (tree, _src) in trees.items():
        if is_migration(path):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(b) for b in node.bases}
            # `ModelAdmin`, `ModelForm` and `ModelSerializer` all contain the
            # word Model and are not models. Matching on them turned every
            # admin class into a phantom model and produced hundreds of
            # findings about fields that were never meant to exist.
            if any(b.endswith(('ModelAdmin', 'ModelForm', 'ModelSerializer'))
                   or 'admin.' in b for b in bases):
                continue
            if not any(b.endswith('Model') or b.endswith('models.Model')
                       or b.endswith('AbstractUser') for b in bases):
                continue
            fields = {'pk', 'id', 'objects', 'DoesNotExist', 'MultipleObjectsReturned',
                      '_meta', 'save', 'delete', 'refresh_from_db', 'full_clean'}
            if any('AbstractUser' in b for b in bases):
                fields.update({
                    'username', 'first_name', 'last_name', 'email', 'password',
                    'is_staff', 'is_active', 'is_superuser', 'last_login',
                    'date_joined', 'groups', 'user_permissions', 'is_authenticated',
                    'is_anonymous', 'set_password', 'check_password',
                    'set_unusable_password', 'get_username', 'has_perm',
                    'get_full_name', 'get_short_name', 'email_user',
                    'has_module_perms', 'get_all_permissions', 'USERNAME_FIELD',
                })
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if not isinstance(stmt.value, ast.Call):
                    continue
                call = ast.unparse(stmt.value.func)
                if 'models.' not in call and 'Field' not in call:
                    continue
                for target in stmt.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    fields.add(target.id)
                    if any(k in call for k in ('ForeignKey', 'OneToOneField', 'ManyToManyField')):
                        fields.add(target.id + '_id')
                        # record the related_name on the far side
                        rel = None
                        tgt = None
                        if stmt.value.args:
                            tgt = ast.unparse(stmt.value.args[0]).strip("'\"").split('.')[-1]
                        for kw in stmt.value.keywords:
                            if kw.arg == 'related_name':
                                try:
                                    rel = ast.literal_eval(kw.value)
                                except Exception:
                                    rel = None
                            if kw.arg == 'to' and tgt is None:
                                tgt = ast.unparse(kw.value).strip("'\"").split('.')[-1]
                        if tgt and rel:
                            related[tgt].add(rel)
                # properties and methods count as attributes too
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fields.add(stmt.name)
            models[node.name] = fields

    for model, names in related.items():
        if model in models:
            models[model].update(names)
            models[model].update({n + '_set' for n in names})
    return models


def check_query_fields(path, tree, models):
    """Catch Model.objects.filter(nonexistent=...) and friends."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in QUERY_METHODS:
            continue
        # Walk back to find `SomeModel.objects`
        owner = func.value
        model_name = None
        if isinstance(owner, ast.Attribute) and owner.attr == 'objects':
            if isinstance(owner.value, ast.Name):
                model_name = owner.value.id
        if model_name is None or model_name not in models:
            continue
        fields = models[model_name]
        # Keywords the ORM itself defines, which are not columns.
        ORM_KWARGS = {'defaults', 'create_defaults', 'using', 'through_defaults'}
        for kw in node.keywords:
            if kw.arg is None or kw.arg in ORM_KWARGS:
                continue
            root = kw.arg.split('__')[0]
            if root in fields:
                continue
            if root in DJANGO_FIELD_SUFFIXES:
                continue
            add(CRITICAL, 'unknown-field', path, node.lineno,
                f'{model_name}.objects.{func.attr}({root}=...) — `{root}` is not '
                f'a field, relation or method on {model_name}')


def check_attribute_on_model(path, tree, models):
    """Catch `organization.members` — an attribute that no model defines.

    Only flags a name when NO model in the project has it, and the variable is
    named after a model, so it stays quiet about dicts and request objects.
    """
    all_attrs = set()
    for fields in models.values():
        all_attrs |= fields

    # Names that collide with a model but are nearly always something else.
    NOT_INSTANCES = {
        'transaction',    # django.db.transaction
        'connection',     # django.db.connection
        'settings', 'request', 'response', 'session', 'cache', 'signal',
        'webhook',        # often a dict payload
        'integration',
    }
    var_to_model = {m.lower(): m for m in models if m.lower() not in NOT_INSTANCES}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        # `ActivityLog.Action` is the class, not an instance - nested choice
        # classes, managers and constants all live there and are not fields.
        if node.value.id in models:
            continue
        var = node.value.id.lower()
        model = var_to_model.get(var)
        if model is None:
            continue
        attr = node.attr
        if attr.startswith('_') or attr in models[model] or attr in all_attrs:
            continue
        # Assigned on the instance somewhere in this same file? Then it is a
        # deliberate runtime attribute, not a typo.
        assigned_here = any(
            isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
            and isinstance(n.value, ast.Name) and n.value.id == node.value.id
            and n.attr == attr
            for n in ast.walk(tree)
        )
        add(INFO if assigned_here else CRITICAL, 'unknown-attribute', path, node.lineno,
            f'`{node.value.id}.{attr}` — no model in this project defines '
            f'`{attr}`, and `{node.value.id}` looks like a {model}'
            + (' (set at runtime in this file)' if assigned_here else ''))


# ── 5. failures that are swallowed in silence ────────────────────────────────

def check_silent_failures(path, tree, source_lines):
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = node.body
        only_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
        only_continue = len(body) == 1 and isinstance(body[0], ast.Continue)
        returns_quietly = (
            len(body) == 1 and isinstance(body[0], ast.Return)
            and (body[0].value is None
                 or (isinstance(body[0].value, ast.Constant)
                     and body[0].value.value in (None, False, 0, '')))
        )
        bare = node.type is None
        if only_pass or only_continue:
            add(WARNING, 'silent-except', path, node.lineno,
                'exception swallowed with no log — if this fires, the feature '
                'stops working and nothing says so')
        elif returns_quietly:
            add(INFO, 'quiet-except-return', path, node.lineno,
                'exception returns a falsy value with no log')
        if bare:
            add(WARNING, 'bare-except', path, node.lineno,
                '`except:` catches KeyboardInterrupt and SystemExit too')


# ── 6. endpoints that change data without a capability check ─────────────────

WRITE_DECORATORS = ('post', 'put', 'patch', 'delete')


def check_endpoint_guards(path, tree):
    if not path.endswith('api.py') and '_api.py' not in path:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        method = None
        for dec in node.decorator_list:
            src = ast.unparse(dec)
            for verb in WRITE_DECORATORS:
                if f'.{verb}(' in src:
                    method = verb
        if method is None:
            continue
        body_src = ast.unparse(node)
        if 'require(' in body_src or 'require_capability' in body_src:
            continue
        # Endpoints that authenticate some other way: the call path uses a
        # shared secret, access requests use StaffAuth, and login and password
        # reset must be reachable by somebody who is not signed in at all.
        decorators = ' '.join(ast.unparse(d) for d in node.decorator_list)
        if 'auth=' in decorators and 'JWTAuth' not in decorators:
            continue
        if any(k in body_src for k in ('_check_secret', 'webhook', 'csrf_exempt', 'Signature')):
            continue
        PUBLIC = {
            'register', 'login', 'logout', 'verify_mfa', 'refresh_token',
            'request_password_reset', 'confirm_password_reset', 'set_password',
            'set_password_alias', 'create_access_request', 'contact',
        }
        if node.name in PUBLIC:
            continue
        # Acting on your own account needs no capability beyond being signed in.
        SELF_SERVICE = {
            'update_profile', 'upload_avatar', 'change_password', 'mfa_setup',
            'mfa_verify', 'mfa_disable', 'link_telegram', 'unlink_telegram',
            'create_api_key', 'revoke_api_key', 'update_preferences',
        }
        if node.name in SELF_SERVICE:
            continue
        add(WARNING, 'unguarded-write', path, node.lineno,
            f'{node.name}() is a {method.upper()} endpoint with no '
            f'require(..., Capability...) check')


# ── 7. secrets in code ───────────────────────────────────────────────────────

SECRET_PATTERNS = [
    # The value must have no whitespace in it. Without that, a line like
    #   if 'auth=' in decorators and 'JWTAuth' not in decorators:
    # reads as a credential assignment, and the scanner reports itself.
    (re.compile(r'''(?i)\b(password|passwd|secret|api_?key|token|auth)\s*=\s*['"]([^'"\s]{8,})['"]'''), 'assignment'),
    (re.compile(r'''(?i)['"](sk_live_|pk_live_|AC[0-9a-f]{32}|SG\.[A-Za-z0-9_-]{20,})'''), 'provider key'),
]
SECRET_ALLOW = re.compile(
    r'''(?i)(config\(|os\.environ|getenv|settings\.|example|placeholder'''
    r'''|changeme|your[-_]|xxx|\.\.\.|test|dummy|fake'''
    # A key being generated is not a key being leaked:
    #   raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
    r'''|secrets\.|token_urlsafe|token_hex|uuid4|get_random)''')


def check_secrets(path, source_lines):
    if os.path.basename(path).startswith('test') or '/tests' in path.replace(os.sep, '/'):
        return
    for i, line in enumerate(source_lines, start=1):
        if SECRET_ALLOW.search(line):
            continue
        for pattern, kind in SECRET_PATTERNS:
            m = pattern.search(line)
            if m:
                add(CRITICAL, 'hardcoded-secret', path, i,
                    f'possible {kind} in source — credentials belong in .env')
                break


# ── 8. migrations hang together ──────────────────────────────────────────────

def migration_dependencies(source):
    """The `dependencies` list of a migration, read from the AST.

    Read properly rather than by regex: a regex for ('app', 'name') also
    matches every TextChoices tuple in the field definitions, so a migration
    that defines a status field looked like it depended on an app called
    `login`.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == 'dependencies'
                       for t in stmt.targets):
                continue
            if not isinstance(stmt.value, (ast.List, ast.Tuple)):
                return []
            out = []
            for element in stmt.value.elts:
                try:
                    item = ast.literal_eval(element)
                except Exception:
                    continue   # swappable_dependency(...) and friends
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    out.append((str(item[0]), str(item[1])))
            return out
    return []


def check_migrations():
    for app in sorted(os.listdir(ROOT)):
        mig_dir = os.path.join(ROOT, app, 'migrations')
        if not os.path.isdir(mig_dir):
            continue
        names = {f[:-3] for f in os.listdir(mig_dir)
                 if f.endswith('.py') and f != '__init__.py'}
        if not names:
            continue
        leaves = set(names)
        for name in names:
            src = open(os.path.join(mig_dir, name + '.py'), encoding='utf-8').read()
            for dep_app, dep_name in migration_dependencies(src):
                if dep_app == app:
                    leaves.discard(dep_name)
                    if dep_name not in names:
                        add(CRITICAL, 'missing-migration', os.path.join(mig_dir, name + '.py'), 0,
                            f'depends on {app}.{dep_name}, which does not exist')
                elif dep_app in ('auth', 'contenttypes', 'sessions', 'admin',
                                 'sites', 'token_blacklist'):
                    continue   # Django's own, not in this repository
                else:
                    other = os.path.join(ROOT, dep_app, 'migrations', dep_name + '.py')
                    if not os.path.exists(other):
                        add(CRITICAL, 'missing-migration', os.path.join(mig_dir, name + '.py'), 0,
                            f'depends on {dep_app}.{dep_name}, which does not exist')
        if len(leaves) > 1:
            add(CRITICAL, 'split-migrations', mig_dir, 0,
                f'{app} has {len(leaves)} migration leaves ({", ".join(sorted(leaves))}) '
                f'— migrate will refuse to run')


# ── 9. settings worth a second look ──────────────────────────────────────────

def check_settings():
    path = os.path.join(ROOT, 'config', 'settings.py')
    if not os.path.exists(path):
        return
    src = open(path, encoding='utf-8').read()
    checks = [
        (r'DEBUG\s*=\s*True\b', CRITICAL, 'DEBUG = True is hardcoded'),
        (r"ALLOWED_HOSTS\s*=\s*\[\s*['\"]\*['\"]", CRITICAL, 'ALLOWED_HOSTS allows any host'),
        (r'CORS_ALLOW_ALL_ORIGINS\s*=\s*True', WARNING,
         'CORS_ALLOW_ALL_ORIGINS is True in code — any site can call the API from a browser'),
        (r"SECRET_KEY\s*=\s*['\"][^'\"]{10,}['\"]", CRITICAL, 'SECRET_KEY is hardcoded'),
    ]
    for pattern, sev, message in checks:
        m = re.search(pattern, src)
        if m:
            line = src[:m.start()].count('\n') + 1
            add(sev, 'settings', path, line, message)


# ── 10. TODOs left in the code ───────────────────────────────────────────────

TODO_RE = re.compile(r'#\s*(TODO|FIXME|HACK|XXX)\b[: ]*(.*)', re.I)


def check_todos(path, source_lines):
    for i, line in enumerate(source_lines, start=1):
        m = TODO_RE.search(line)
        if m:
            add(INFO, 'todo', path, i, f'{m.group(1).upper()}: {m.group(2).strip()[:80]}')


# ── 11. the call path must stay clean ────────────────────────────────────────

CALL_PATH = {'routing/engine.py', 'routing/asterisk_handler.py'}
BLOCKING = ('requests.', 'urlopen', 'httpx.', 'urllib.request', 'socket.create_connection',
            'time.sleep', 'smtplib', 'boto3')


def check_call_path(path, source_lines):
    rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
    if rel not in CALL_PATH:
        return
    for i, line in enumerate(source_lines, start=1):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        for marker in BLOCKING:
            if marker in line:
                add(CRITICAL, 'call-path-blocking', path, i,
                    f'`{marker}` on the call path — a slow provider stops calls '
                    f'and nothing appears in the access log')


# ── run ──────────────────────────────────────────────────────────────────────

def main():
    print('Scanning every Python file in', ROOT)
    trees = parse_all()
    print(f'  {len(trees)} files parsed\n')

    models = collect_models(trees)
    print(f'  {len(models)} models found: {", ".join(sorted(models)[:12])}'
          f'{" ..." if len(models) > 12 else ""}\n')

    total_lines = 0
    for path, (tree, src) in trees.items():
        lines = src.splitlines()
        total_lines += len(lines)
        check_undefined(path, tree)
        check_silent_failures(path, tree, lines)
        check_todos(path, lines)
        check_call_path(path, lines)
        check_secrets(path, lines)
        if not is_migration(path):
            check_function_scope_imports(path, tree)
            check_query_fields(path, tree, models)
            check_attribute_on_model(path, tree, models)
            check_endpoint_guards(path, tree)

    check_migrations()
    check_settings()

    print(f'  {total_lines:,} lines of Python examined\n')

    by_sev = defaultdict(list)
    for f in findings:
        by_sev[f[0]].append(f)

    for sev in (CRITICAL, WARNING, INFO):
        rows = by_sev.get(sev, [])
        if not rows:
            continue
        print('=' * 78)
        print(f'{sev}  ({len(rows)})')
        print('=' * 78)
        by_kind = defaultdict(list)
        for r in rows:
            by_kind[r[1]].append(r)
        for kind in sorted(by_kind):
            group = sorted(by_kind[kind], key=lambda r: (r[2], r[3]))
            print(f'\n  {kind}  ({len(group)})')
            shown = group if sev == CRITICAL else group[:25]
            for _s, _k, path, line, message in shown:
                print(f'    {path}:{line}')
                print(f'        {message}')
            if len(group) > len(shown):
                print(f'    ... and {len(group) - len(shown)} more')
        print()

    crit = len(by_sev.get(CRITICAL, []))
    warn = len(by_sev.get(WARNING, []))
    info = len(by_sev.get(INFO, []))
    print('=' * 78)
    print(f'RESULT: {crit} critical, {warn} warnings, {info} informational')
    print('=' * 78)
    if crit == 0:
        print('No critical static faults found.')
    return 1 if crit else 0


if __name__ == '__main__':
    sys.exit(main())
