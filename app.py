"""Bottle app factory/bootstrap.

Single-tenant template app — no workspace/multi-tenancy concept anywhere.
Zero pip dependencies beyond peewee + bottle (see requirements.txt); the
session, CSRF, password hashing, mailer, and Ask-AI HTTP calls are all
hand-rolled or stdlib (see utils.py / ai.py).
"""

from __future__ import annotations

import json
import os
import sys

# Every module in pages/ does `from app import app` so every route can be
# declared as `@app.route(...)` without a blueprint indirection. If this
# file is ever launched directly (`python3 app.py`), Python runs it as
# `__main__` — and that `from app import app` would otherwise import a
# *second*, separate copy of this module under the name "app", with its
# own fresh Bottle() instance that never sees any of pages/'s routes (the
# one actually passed to run() below would then only have the routes
# registered above this point). Aliasing "app" to the already-running
# module up front makes the later self-import a no-op lookup instead of a
# second execution.
sys.modules.setdefault("app", sys.modules[__name__])

# peewee and bottle are vendored in vendor/ as plain .py files, not
# pip-installed — this app runs with `python3 app.py` and nothing else, no
# venv/pip step required. Both are MIT-licensed; see vendor/LICENSE.peewee
# and vendor/LICENSE.bottle.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from bottle import Bottle, HTTPError, debug as _bottle_debug, request, run, static_file, template

from models import db, ensure_schema, status_label
from utils import (
    csrf_protect,
    csrf_token,
    current_user,
    get_flashed_messages,
    notification_summary,
    open_session,
    save_session,
    url_for,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    """Read .env into os.environ. Dependency-free; existing shell vars win."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_dotenv()
# Schema is applied here — not just under `if __name__ == '__main__'` — so it
# runs no matter which entrypoint starts the process (dev server or
# gunicorn's `app:app`). Idempotent: safe to call on every process start.
ensure_schema()

# Same reasoning as ensure_schema() above: started here, not just under
# `if __name__ == '__main__'`, so the reminder-firing job (see jobs.py) also
# runs under `gunicorn app:app`. jobs.start() is idempotent and the thread is
# a daemon, so this is safe however many times/entrypoints import this module.
import jobs  # noqa: E402

jobs.start()

DEBUG = os.environ.get("DEBUG", "1") == "1"
# Set at module level (not just under `if __name__ == '__main__'`) so it
# also applies under `gunicorn app:app` — gunicorn's own --reload only
# re-execs the worker on *.py changes, but bottle's template() cache
# (app.py's render() -> bottle.template()) only skips its cache and
# re-reads a template file from disk when bottle.DEBUG is set, regardless of
# which process is serving requests. Without this, a template edit needs a
# full worker restart to show up even with --reload running.
_bottle_debug(DEBUG)

app = Bottle()

# bottle's SimpleTemplate looks in this list for template files (.html, per
# BaseTemplate's extensions list — no .tpl in this app). Also make our
# own url_for/csrf_token/current_user/flash helpers available to every
# template without each render() call having to pass them explicitly.
import bottle as _bottle_module  # noqa: E402

_bottle_module.TEMPLATE_PATH.insert(0, os.path.join(BASE_DIR, "templates"))


def asset_version(filepath: str) -> int:
    """A static asset's mtime, used as a `?v=` cache-busting query string in
    layout.html. Bumps itself automatically whenever the file changes — no
    version number to remember to increment — so a browser that already
    cached an old style.css (no Cache-Control is set on /static/, so this is
    otherwise left to each browser's own heuristics) fetches the new one
    instead of silently reusing a stale copy after a deploy/restart."""
    try:
        return int(os.path.getmtime(os.path.join(BASE_DIR, "static", filepath)))
    except OSError:
        return 0


def js_string(value: str) -> str:
    """json.dumps, with the characters that could break out of a <script>
    block (a message containing literal "</script>", say — some flashed
    messages interpolate user-supplied data like a filename or task title)
    escaped as \\uXXXX. Safe to inline unquoted inside a <script> tag."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


_TEMPLATE_DEFAULTS = {
    "url_for": url_for,
    "current_user": current_user,
    "csrf_token": csrf_token,
    "get_flashed_messages": get_flashed_messages,
    "asset_version": asset_version,
    "status_label": status_label,
    "notification_summary": notification_summary,
    # Exposed so layout.html can highlight the current section in the
    # sidebar nav (`request.path.startswith(...)`) without every route
    # having to pass its own "active nav" flag through render().
    "request": request,
    # For safely embedding a flashed message as a JS string literal in
    # _toasts.html's bootstrap <script>.
    "js_string": js_string,
}


def render(name: str, **kwargs) -> str:
    ctx = dict(_TEMPLATE_DEFAULTS)
    ctx.update(kwargs)
    return template(name, **ctx)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@app.hook("before_request")
def _open_db() -> None:
    db.connect(reuse_if_open=True)


@app.hook("before_request")
def _open_session_hook() -> None:
    open_session()


@app.hook("before_request")
def _csrf_protect_hook() -> None:
    # Static assets are served by Bottle's own static_file handler further
    # down and never mutate anything, so this only ever fires for routes in
    # pages/ — but it still runs before routing (see bottle's _handle), so it
    # applies uniformly to every non-GET request regardless of path.
    if request.path.startswith("/static/"):
        return
    csrf_protect()


@app.hook("after_request")
def _save_session_hook() -> None:
    save_session()


@app.hook("after_request")
def _close_db() -> None:
    if not db.is_closed():
        db.close()


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


@app.route("/static/<filepath:path>", name="static")
def _static(filepath: str):
    return static_file(filepath, root=os.path.join(BASE_DIR, "static"))


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


@app.error(400)
def _bad_request(error: HTTPError):
    # abort(400, "...") call sites (CSRF check, invalid status, etc.) pass a
    # specific, user-actionable message as the body — show that instead of
    # a generic one whenever it's there.
    return render("error.html", code=400, heading="Something's not right with that request",
                  message=error.body or "The request couldn't be processed. Please try again.")


@app.error(404)
def _not_found(_error: HTTPError):
    return render("error.html", code=404, heading="Not found",
                  message="That page doesn't exist, or was moved.")


@app.error(403)
def _forbidden(_error: HTTPError):
    return render("error.html", code=403, heading="You don't have access",
                  message="You're signed in, but you don't have permission to view this.")


@app.error(500)
def _server_error(_error: HTTPError):
    # A 500 can mean the request died mid-transaction; roll back before any
    # further query runs (the error page itself queries current_user/nav
    # data), and guard the render so a broken template can't cascade into a
    # second crash.
    try:
        if not db.is_closed():
            db.rollback()
    except Exception:
        pass
    try:
        return render("error.html", code=500, heading="Something went wrong",
                      message="An unexpected error occurred. Please try again.")
    except Exception:
        return (
            "<!doctype html><meta charset=utf-8><title>500</title>"
            "<h1>Something went wrong</h1><p>Please try again.</p>"
        )


# Route registration lives in the pages/ package, imported for its side
# effects only — every view there does `from app import app` and decorates
# directly (no blueprints; pages/ splits routes by feature area rather than
# introducing that indirection). Must be imported after `app`/`render`/hooks
# exist above.
import pages  # noqa: E402,F401


if __name__ == "__main__":
    run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 5000)),
        debug=DEBUG,
        reloader=DEBUG,
    )
