"""Every route in the app, split by feature area. Still no blueprints —
each module below does `from app import app` and decorates its own routes
directly, exactly as a single flat pages.py used to; this package just
gives each feature area its own file once the flat version grew past a
size an editing pass could comfortably hold, while keeping the same "an
AI-editing tool needs to hold it all in context" idea at the grain of one
file per feature instead of one file per app.
"""

from __future__ import annotations

from bottle import HTTPError, request

from app import app
from utils import (
    PUBLIC_ROUTES,
    SESSION_INDEPENDENT_PATHS,
    any_team_members_exist,
    current_user,
    redirect,
    url_for,
)


@app.hook("before_request")
def _bootstrap_redirect() -> None:
    """Until the first owner account exists, every road leads to /register."""
    if request.path == "/register" or request.path.startswith("/static/"):
        return
    if not any_team_members_exist():
        redirect(url_for("register_owner"))


@app.hook("before_request")
def _require_login_hook() -> None:
    """Every route requires a logged-in user by default — the opposite of a
    per-route @require_login decorator, which is easy to forget on a new
    route and silently leave unprotected. PUBLIC_ROUTES (utils.py) lists
    the handful of routes a signed-out visitor genuinely needs to reach
    (register, login, accept-invite, forgot/reset password); everything
    else redirects to /login.

    Registered *after* _bootstrap_redirect above — before_request hooks run
    in registration order (see Bottle's add_hook) — so a fresh, team-less
    instance always lands on /register first, before this hook gets a
    chance to bounce it to /login instead.

    Resolves the route itself via app.match() rather than checking
    request.route: before_request hooks fire *before* Bottle's own routing
    (see utils.py's SESSION_INDEPENDENT_PATHS comment), so there's no route
    to inspect yet at this point otherwise. match() is a plain, read-only
    lookup (see Router.match) — cheap to do twice per request.
    """
    if request.path.startswith("/static/") or request.path in SESSION_INDEPENDENT_PATHS:
        return
    try:
        route, _ = app.match(request.environ)
    except HTTPError:
        return  # a 404/405 — let Bottle's own routing surface that normally
    if route.name in PUBLIC_ROUTES:
        return
    if current_user() is None:
        redirect(url_for("login"))


# Import each feature module for its route-registration side effects only.
from . import (  # noqa: E402,F401
    attachments,
    auth,
    chat,
    clients,
    comments,
    dashboard,
    notifications,
    settings,
    tasks,
)
