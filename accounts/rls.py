"""Customer accounts, point 1c: database row-level security for the portal.

Sir, 5 Nov: "Enforce it at database level (row-level security), not only in
the code. Every query and API call is scoped to the logged-in account."

How it works
------------
The portal process (`web`) connects to PostgreSQL as its own restricted login
(`avx_portal`: not a superuser, no BYPASSRLS). Every table that carries an
account has a policy (migration `accounts/0017_rls_policies`): a row is
visible, and may be written, only when its account is the one in the
connection setting `avx.org`. That setting is:

  ''       no account: nothing is visible, nothing can be written
  '*'      system: everything (see "System" below)
  <uuid>   that account only

This module keeps the right value in that setting for whatever code is
running, from a context variable:

  - a portal request starts with NO account (middleware, around the view);
  - once the login check passes, the request takes its login's account
    (a buyer or publisher login: its workspace's account, then the code's own
    partner rules narrow it further, as before);
  - endpoints that work before anyone logs in (login, password reset, invite,
    website chat ...) and the call-routing callbacks run as system - what they
    may do is decided by their own checks, as before;
  - owner pages (`require_owner`: Owner, Carrier Costs, Company Profit, carrier
    statements) look across accounts by design and run as system for the
    platform owner only.

Outside a request (Celery, management commands, startup) the value is system:
those never serve a customer, and on Server B they keep the owner login.

The setting is applied per database connection by an execute wrapper, before
the first query that needs a different value. Outside a transaction it is set
for the session (it cannot be undone by a rollback); inside a transaction it
is set for that transaction only (`set_config(..., true)`), on every query, so
a rollback can never leave a connection with another request's account.

Switched on only where `TENANT_GUARD` is True (the `web` service with
`PORTAL_DATABASE_URL`); everywhere else this module does nothing and the code
runs exactly as before.
"""
import contextlib
import contextvars
import re

from django.conf import settings

SYSTEM = '*'
NONE = ''
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

_account = contextvars.ContextVar('avortyx_rls_account', default=SYSTEM)


def current() -> str:
    return _account.get()


def _value(org) -> str:
    if org is None or org == NONE:
        return NONE
    if org == SYSTEM:
        return SYSTEM
    text = str(org).lower()
    if not _UUID.match(text):
        raise ValueError(f'not an account id: {org!r}')
    return text


@contextlib.contextmanager
def scope(org):
    """Run the block as this account ('' = none, '*' = system)."""
    token = _account.set(_value(org))
    try:
        yield
    finally:
        _account.reset(token)


def system():
    """Run the block across all accounts. Only for code that must, and says why."""
    return scope(SYSTEM)


def set_account(org) -> None:
    """For the rest of the current context (the login check of a request)."""
    _account.set(_value(org))


def account_of(request):
    """The account a logged-in request acts for."""
    org = getattr(request, 'auth_org', None)            # an API key
    if org is not None:
        return getattr(org, 'id', org)
    user = getattr(request, 'auth', None)
    return getattr(user, 'organization_id', None)


# ── the connection side ────────────────────────────────────────────────────

SET_SQL = "select set_config('avx.org', %s, %s)"
# Rehearsal B, 10 Nov: transaction control is never preceded by the setting.
# After an error inside a savepoint the transaction is aborted until
# "ROLLBACK TO SAVEPOINT"; sending the setting first failed ("current
# transaction is aborted"), so the rollback never ran and the whole request -
# or test - stayed broken. These statements read and write no rows.
_CONTROL = ('SAVEPOINT', 'RELEASE', 'ROLLBACK', 'COMMIT', 'BEGIN', 'START TRANSACTION')


def _guard(execute, sql, params, many, context):
    if isinstance(sql, str) and sql.lstrip()[:20].upper().startswith(_CONTROL):
        return execute(sql, params, many, context)
    conn = context['connection']
    want = _account.get()
    in_txn = conn.in_atomic_block
    if in_txn or getattr(conn, '_avx_org', None) != want:
        with conn.connection.cursor() as c:          # the driver's own cursor: no wrappers, no recursion
            c.execute(SET_SQL, [want, in_txn])
        if not in_txn:
            conn._avx_org = want
    return execute(sql, params, many, context)


def _on_connect(sender, connection, **kwargs):
    if connection.vendor != 'postgresql':
        return
    connection._avx_org = None                       # a new session starts with no setting
    if _guard not in connection.execute_wrappers:
        connection.execute_wrappers.append(_guard)


def connect() -> None:
    """Called from AccountsConfig.ready(). The request side always runs (it
    only sets a context variable, so the tests exercise it); the database
    side only where TENANT_GUARD is on."""
    _patch_ninja()
    if enabled():
        from django.db.backends.signals import connection_created
        connection_created.connect(_on_connect, dispatch_uid='rls-connection')


# ── requests ───────────────────────────────────────────────────────────────

def _streaming(response, org):
    """A streamed file (the CSV export) is produced after the view returns:
    each piece is made as the request's account."""
    inner = iter(response.streaming_content)

    def pieces():
        while True:
            with scope(org):
                try:
                    piece = next(inner)
                except StopIteration:
                    return
            yield piece
    response.streaming_content = pieces()
    return response


class TenantMiddleware:
    """Last in MIDDLEWARE: the view of every request starts with no account.

    The call-routing callbacks (`/api/twilio/...`: Asterisk, behind the shared
    secret) run as system. On Server B they are served by `web_calls`, which
    keeps the owner login; this only matters if one ever reaches `web`.
    """

    # Rehearsal B, 10 Nov: and nginx's recording-link check (CH-634, a plain
    # Django view, no login): the signed link is its permission, and it must
    # find the call, login or buyer of any account to judge it.
    MACHINE = ('/api/twilio/', '/api/recordings/check')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = SYSTEM if request.path.startswith(self.MACHINE) else NONE
        with scope(start):
            response = self.get_response(request)
        if getattr(response, 'streaming', False) and getattr(request, '_avx_org', None) is not None:
            response = _streaming(response, request._avx_org)
        return response


def _patch_ninja():
    """Django Ninja: an endpoint without a login check runs as system; one
    with a login check runs the check as system (it has to find the login)
    and then takes the login's account."""
    from ninja.operation import Operation
    if getattr(Operation, '_avx_rls', False):
        return
    run, authenticate = Operation.run, Operation._run_authentication

    def _run(self, request, **kw):
        if not self.auth_callbacks:
            with system():
                return run(self, request, **kw)
        return run(self, request, **kw)

    def _authenticate(self, request):
        with system():
            error = authenticate(self, request)
        if error is None:
            org = _value(account_of(request))
            request._avx_org = org
            set_account(org)
        return error

    Operation.run, Operation._run_authentication = _run, _authenticate
    Operation._avx_rls = True


def owner_view(request) -> None:
    """The platform owner's pages look across accounts (Owner, Carrier Costs,
    Company Profit, carrier statements). Called by `require_owner` once the
    login is known to be the owner."""
    request._avx_owner_view = True
    request._avx_org = SYSTEM
    set_account(SYSTEM)


def enabled() -> bool:
    return bool(getattr(settings, 'TENANT_GUARD', False))
