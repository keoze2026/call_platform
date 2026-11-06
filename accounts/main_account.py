"""Customer accounts point 3: what only our own (main) account may see.

Sir, 5 Nov: "Customers never see our carrier costs, buyer rates, publisher
payouts, profit, or other customers' prices. Customers see only their own
pricing. Owner-only sections (Owner, Carrier Costs, Company Profit, Trust
Engine, White Label admin, internal AI audit) never appear in customer
portals." ... "(server-side check, not just hidden menus)".

The main account is the workspace MAIN_ORGANIZATION_SLUG names (`interns`
on 5 Nov). Every other workspace is a customer account. Its logins get 403
from these sections whatever their role, and the portal does not show them
(the profile carries `is_main_account`). A login with no workspace is not the
main account either.

Not the same as accounts/main_user.py (CH-243): that is the FIRST ADMIN of
any workspace, for its API keys.
"""
from ninja.errors import HttpError

REFUSED = 'Not available for this account.'


def is_main_account(user) -> bool:
    """True for a login of our main account. A server with no main account
    (MAIN_ORGANIZATION_SLUG names no workspace: a new server, the tests) has
    no customer accounts to keep out, so every login counts as main there -
    exactly as before point 3."""
    if user is None:
        return False
    cached = getattr(user, '_avx_main_account', None)    # once per request's login
    if cached is not None:
        return cached
    from accounts.tenancy import main_organization_id
    main = main_organization_id()
    answer = True if main is None else getattr(user, 'organization_id', None) == main
    try:
        user._avx_main_account = answer
    except AttributeError:
        pass
    return answer


def is_main_org(organization_id) -> bool:
    """The same answer for a workspace id (CH-633: messages the platform sends
    on its own, with no login behind them). No main account on the server:
    True, as above."""
    from accounts.tenancy import main_organization_id
    main = main_organization_id()
    return True if main is None else str(organization_id) == str(main)


def require_main_account(user) -> None:
    if not is_main_account(user):
        raise HttpError(403, REFUSED)
