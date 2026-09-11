"""Every route in the app, split by feature area. Still no blueprints —
each module below does `from app import app` and decorates its own routes
directly, exactly as a single flat pages.py used to; this package just
gives each feature area its own file once the flat version grew past a
size an editing pass could comfortably hold, while keeping the same "an
AI-editing tool needs to hold it all in context" idea at the grain of one
file per feature instead of one file per app.
"""

from __future__ import annotations

from bottle import request

from app import app
from utils import any_team_members_exist, redirect, url_for


@app.hook("before_request")
def _bootstrap_redirect() -> None:
    """Until the first owner account exists, every road leads to /register."""
    if request.path == "/register" or request.path.startswith("/static/"):
        return
    if not any_team_members_exist():
        redirect(url_for("register_owner"))


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
