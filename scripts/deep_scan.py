#!/usr/bin/env python3
"""Find features that look wired up and are not.

full_scan.py reads the backend for faults a parser can see - undefined names,
wrong model fields, silent excepts. It has said "0 critical" all week and it was
right every time, while the platform quietly told customers things that were not
true. Every fault that actually mattered on 5 October was invisible to it:

  the response schema dropped caller_country, carrier and is_qualified, so
  Qualified read 309 when it was 346 and Caller Profile showed "Unknown"

  the Advanced Settings switches wrote to a JSON blob while the code that acts
  on them read a database column

  "Add destination" was a toast saying "coming soon", under the words
  "No destinations routed to this campaign yet"

  the publisher invite wrote to localStorage and sent no email

  addDestination never sent the field the backend requires, so every call to it
  would have failed - and nothing called it

None of those is a syntax error. Each is a seam where two halves were built and
never joined. This looks for the seams.

    python scripts/deep_scan.py                  both repos
    python scripts/deep_scan.py --frontend-only
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
FRONTEND = Path(os.environ.get('AVORTYX_FRONTEND', Path.home() / 'Desktop' / 'Avortyx'))

SKIP_DIRS = {
    'node_modules', '.next', '.git', '__pycache__', 'migrations',
    'anchor-landing-page', 'vertyx-website-update', 'venv', '.venv', 'staticfiles',
}

findings: dict[str, list[tuple[str, str]]] = defaultdict(list)


def report(category: str, where: str, detail: str) -> None:
    findings[category].append((where, detail))


def walk(root: Path, suffixes: tuple[str, ...]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(suffixes):
                yield Path(dirpath) / fn


def rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Model fields that no schema carries.
#
# This is the fault that cost the most. A column exists, something writes it,
# and the schema the endpoint declares does not name it - so Ninja deletes it on
# the way out and the client sees nothing. Silent in every log.
# ─────────────────────────────────────────────────────────────────────────────
def scan_schema_coverage() -> None:
    model_fields: dict[str, set[str]] = {}
    for path in walk(BACKEND, ('models.py', 'destination.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            fields = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    src = ast.unparse(func) if hasattr(ast, 'unparse') else ''
                    if 'models.' in src and stmt.targets:
                        t = stmt.targets[0]
                        if isinstance(t, ast.Name):
                            fields.add(t.id)
            if fields:
                model_fields[node.name] = fields

    schema_fields: dict[str, set[str]] = {}
    for path in walk(BACKEND, ('schemas.py',)):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    names.add(stmt.target.id)
            if names:
                schema_fields[f'{rel(path, BACKEND)}::{node.name}'] = names

    # Only the pairings worth reporting: a schema named after a model.
    noise = {
        'id', 'created_at', 'updated_at', 'organization', 'created_by',
        'password', 'is_deleted',
    }
    for schema_key, s_fields in schema_fields.items():
        sname = schema_key.split('::')[1]
        base = sname.replace('Out', '').replace('Schema', '').replace('Update', '') \
                    .replace('Create', '').replace('List', '')
        if not base:
            continue
        for mname, m_fields in model_fields.items():
            if mname.lower() != base.lower():
                continue
            missing = {f for f in m_fields - s_fields if f not in noise and not f.startswith('_')}
            if missing:
                report(
                    'model fields a schema does not carry',
                    schema_key,
                    f'{mname} has {len(missing)} field(s) this schema never names: '
                    + ', '.join(sorted(missing)[:12])
                    + (' …' if len(missing) > 12 else ''),
                )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Buttons that only pretend.
# ─────────────────────────────────────────────────────────────────────────────
DEAD_HANDLER = re.compile(
    r'onClick=\{\(\)\s*=>\s*(toast\.(info|success|message)\([^)]*\)|\{\s*\}|undefined|null)\s*\}'
)
COMING_SOON = re.compile(r'(coming soon|not yet implemented|placeholder|todo:|wip)', re.I)


def scan_dead_controls() -> None:
    if not FRONTEND.exists():
        return
    for path in walk(FRONTEND, ('.tsx',)):
        text = path.read_text(encoding='utf-8', errors='replace')
        for i, line in enumerate(text.splitlines(), 1):
            # A comment describing a control that used to be dead is not a dead
            # control. Without this the three fixes made today reported
            # themselves as faults, which is a fast way to stop trusting a tool.
            stripped = line.strip()
            if stripped.startswith(('*', '//', '/*')):
                continue
            if DEAD_HANDLER.search(line):
                report('controls that do nothing', f'{rel(path, FRONTEND)}:{i}', stripped[:160])
            elif COMING_SOON.search(line) and ('onClick' in line or 'toast' in line):
                report('controls that do nothing', f'{rel(path, FRONTEND)}:{i}', stripped[:160])


# ─────────────────────────────────────────────────────────────────────────────
# 3. State that never leaves the browser.
#
# A store that persists to localStorage and imports no service keeps everything
# on one machine. The publisher invite did this: the row said "Invited" forever
# and no email was ever sent.
# ─────────────────────────────────────────────────────────────────────────────
def scan_local_only_stores() -> None:
    store_dir = FRONTEND / 'lib' / 'store'
    if not store_dir.exists():
        return
    for path in walk(store_dir, ('.ts',)):
        text = path.read_text(encoding='utf-8', errors='replace')
        if 'localStorage' not in text and 'persist(' not in text:
            continue
        calls_api = bool(re.search(r'from "@/lib/api/', text))
        if not calls_api:
            report(
                'state that never reaches the server',
                rel(path, FRONTEND),
                'persists to localStorage and imports no API service - '
                'nothing here is saved for anyone but this browser',
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4. API methods nobody calls.
#
# A service method with no caller has never run. addDestination was wrong in a
# way that would have failed on first use, and nothing had ever used it.
# ─────────────────────────────────────────────────────────────────────────────
def scan_uncalled_service_methods() -> None:
    svc_dir = FRONTEND / 'lib' / 'api' / 'services'
    if not svc_dir.exists():
        return
    all_text = []
    for path in walk(FRONTEND, ('.ts', '.tsx')):
        if 'lib/api/services' in str(path):
            continue
        all_text.append(path.read_text(encoding='utf-8', errors='replace'))
    corpus = '\n'.join(all_text)

    for path in walk(svc_dir, ('.ts',)):
        text = path.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'^\s{2}async (\w+)\(', text, re.M):
            name = m.group(1)
            if not re.search(rf'\.{re.escape(name)}\s*\(', corpus):
                line = text[: m.start()].count('\n') + 1
                report(
                    'API methods with no caller',
                    f'{rel(path, FRONTEND)}:{line}',
                    f'{name}() is never called anywhere - it has never run, '
                    f'so nothing proves it works',
                )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Values invented from an id.
# ─────────────────────────────────────────────────────────────────────────────
def scan_invented_values() -> None:
    if not FRONTEND.exists():
        return
    pat = re.compile(r'(hash\(|Math\.random\(\))')
    for path in walk(FRONTEND, ('.tsx', '.ts')):
        if 'lib/store' in str(path):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for i, line in enumerate(text.splitlines(), 1):
            if not pat.search(line) or line.strip().startswith(('*', '//', '/*')):
                continue
            # An id generator is fine; a value shown to a person is not.
            if re.search(r'(id:|uid|key=|_id|Math\.random\(\)\.toString)', line):
                continue
            report('values invented rather than read', f'{rel(path, FRONTEND)}:{i}', line.strip()[:160])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Errors swallowed whole.
# ─────────────────────────────────────────────────────────────────────────────
def scan_swallowed_errors() -> None:
    if not FRONTEND.exists():
        return
    pat = re.compile(r'\.catch\(\(\)\s*=>\s*\{\s*\}\)|catch\s*\{\s*\}|catch\s*\([^)]*\)\s*\{\s*\}')
    for path in walk(FRONTEND, ('.ts', '.tsx')):
        text = path.read_text(encoding='utf-8', errors='replace')
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                report('errors discarded silently', f'{rel(path, FRONTEND)}:{i}', line.strip()[:160])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--frontend-only', action='store_true')
    args = ap.parse_args()

    if not args.frontend_only:
        scan_schema_coverage()
    if FRONTEND.exists():
        scan_dead_controls()
        scan_local_only_stores()
        scan_uncalled_service_methods()
        scan_invented_values()
        scan_swallowed_errors()
    else:
        print(f'frontend not found at {FRONTEND}; set AVORTYX_FRONTEND', file=sys.stderr)

    total = sum(len(v) for v in findings.values())
    print('=' * 78)
    print('DEEP SCAN - things that look wired up and are not')
    print('=' * 78)
    for category in sorted(findings, key=lambda c: -len(findings[c])):
        rows = findings[category]
        print(f'\n{category.upper()}  ({len(rows)})')
        for where, detail in rows[:25]:
            print(f'  {where}')
            print(f'      {detail}')
        if len(rows) > 25:
            print(f'  … and {len(rows) - 25} more')
    print()
    print('=' * 78)
    print(f'TOTAL: {total}')
    print('=' * 78)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
