"""Make the request that is being served reachable from a signal handler.

The activity log needs two things at the same moment: what changed, and who
changed it. A model signal knows the first and a view knows the second, and
nothing joined them - which is why the workspace activity feed has only ever
contained logins. Those are written by hand in `accounts/services.py`, and
every business action (a buyer created, a campaign paused, a destination
deleted) went unrecorded because no endpoint wrote a log line.

Writing one by hand in each of the forty-odd create, update and delete
endpoints is the version of this that gets forgotten on the forty-first. A
context variable set here and read by `accounts/activity_signals.py` records
every one of them without any endpoint knowing about it.

A `ContextVar` rather than a thread local: this runs under daphne, where a sync
view is handed to a thread from a pool and a thread local can be read by the
next request that borrows the same thread. Context variables are per-request in
both the sync and async cases.

Deliberately does nothing but set and reset. Every request on the platform
passes through here, including the Asterisk callback that routes calls, so it
touches no database and cannot fail in a way that costs a call.
"""
import contextvars

_current_request = contextvars.ContextVar('avortyx_current_request', default=None)


def get_current_request():
    """The request being served, or None outside a request (Celery, a command)."""
    return _current_request.get()


def get_current_user():
    """Whoever is acting, or None.

    The API authenticates with a bearer token, so Django Ninja puts the user on
    `request.auth` and `request.user` stays anonymous. Django admin and the
    session views are the other way round. Both are checked.
    """
    request = _current_request.get()
    if request is None:
        return None

    user = getattr(request, 'auth', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user

    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user

    return None


class CurrentRequestMiddleware:
    """Holds the request in a context variable for the life of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            # Reset even when the view raised, so a failed request never leaves
            # the next one attributing its changes to the wrong person.
            _current_request.reset(token)
