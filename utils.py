"""Session/CSRF, auth helpers, mailer, activity/notification recording, slugify.

Hand-rolled throughout — the zero-pip-dependency constraint (peewee + bottle
only) rules out Flask-style session cookies, werkzeug password hashing, and
any HTTP client library for outbound mail/API calls.

Session model: the whole session is a small JSON object, HMAC-signed and
base64-encoded into a single cookie (the same shape Flask's own signed-cookie
session uses, minus Flask — no server-side session store/table). A request's
session dict lives in `request.environ` for the duration of that request;
`open_session`/`save_session` (wired up as Bottle hooks in app.py) decode it
from the incoming cookie and re-encode it onto the outgoing response.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable

from bottle import HTTPError, request, response
from bottle import abort as _bottle_abort
from bottle import redirect as _bottle_redirect

# ---------------------------------------------------------------------------
# Session (hand-rolled, hmac-signed cookie — no session table, no werkzeug)
# ---------------------------------------------------------------------------

SESSION_COOKIE = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_SESSION_ENVIRON_KEY = "templateapp.session"


def _secret_key() -> str:
    # Re-read on each call (cheap) so a freshly-edited .env is picked up
    # under dev-server reload without restarting the process.
    return os.environ.get("SECRET_KEY", "binders-core-dev-secret-change-in-production")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return hmac.new(_secret_key().encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()


def _encode_session(data: dict) -> str:
    payload = _b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def _decode_session(cookie_value: str | None) -> dict:
    if not cookie_value or "." not in cookie_value:
        return {}
    payload, _, signature = cookie_value.rpartition(".")
    if not hmac.compare_digest(_sign(payload), signature):
        return {}
    try:
        data = json.loads(_b64decode(payload))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def open_session() -> None:
    """Registered as a before_request hook: decode the incoming cookie."""
    request.environ[_SESSION_ENVIRON_KEY] = _decode_session(request.get_cookie(SESSION_COOKIE))


def get_session() -> dict:
    """The current request's session dict — mutate it directly."""
    return request.environ.setdefault(_SESSION_ENVIRON_KEY, {})


def save_session() -> None:
    """Set/refresh the session cookie on the current response.

    Always resaves rather than tracking a dirty flag — the cookie is small
    and this is a single-tenant app with modest traffic, so the extra write
    isn't worth the bookkeeping. Also registered as an after_request hook
    (see app.py) for the common case of a route returning rendered HTML
    directly — but that alone is NOT enough for redirect()/abort(), see the
    wrappers below.
    """
    data = request.environ.get(_SESSION_ENVIRON_KEY, {})
    response.set_cookie(
        SESSION_COOKIE,
        _encode_session(data),
        max_age=SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="Lax",
    )


def redirect(url: str, code: int | None = None) -> None:
    """Wraps bottle.redirect so a session cookie set during this request
    (e.g. login, flash()) survives.

    Bottle's redirect()/abort() raise an HTTPResponse that short-circuits
    the rest of _handle() — and when that response is eventually cast to
    WSGI output, Bottle re-applies THAT object's (pre-hook) headers/cookies
    onto the ambient `response` a second time, which clobbers anything an
    after_request hook added in between. Saving the session into `response`
    right before redirect() builds its copy of the current response is what
    makes the cookie survive that second apply.
    """
    save_session()
    _bottle_redirect(url, code)


def abort(code: int = 500, text: str = "Unknown Error.") -> None:
    """Wraps bottle.abort — same reasoning as redirect() above, except
    HTTPError starts from a blank response (it doesn't copy the current one
    the way redirect() does), so the cookie has to be attached explicitly."""
    save_session()
    err = HTTPError(code, text)
    err._cookies = response._cookies
    raise err


# ---------------------------------------------------------------------------
# CSRF (hmac-compared token, seeded into the session — same idea as admin's
# app.py::_csrf_token/_csrf_protect, ported to Bottle's hook API)
# ---------------------------------------------------------------------------


def csrf_token() -> str:
    sess = get_session()
    token = sess.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        sess["_csrf_token"] = token
    return token


# Routes authenticated independently of the session (see
# require_internal_secret) have no session-seeded CSRF token to present, so
# csrf_protect() skips them by path. Checked by path rather than a
# decorator-set marker on the route's callback because Bottle's
# before_request hooks fire *before* routing — request.route isn't
# resolved yet at this point, so there's no callback to inspect here.
_CSRF_EXEMPT_PATHS = frozenset({"/internal/ai-command"})


def csrf_protect() -> None:
    """Registered as a before_request hook. Every mutating request is
    checked uniformly, except the paths in _CSRF_EXEMPT_PATHS — today just
    the admin app's internal Ask-AI proxy endpoint, which authenticates
    itself via a shared secret instead of a session."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.path in _CSRF_EXEMPT_PATHS:
        return
    expected = csrf_token()
    supplied = request.forms.get("_csrf_token") or request.headers.get("X-CSRF-Token") or ""
    if not hmac.compare_digest(expected, supplied):
        abort(400, "Your session expired or the form was out of date — please try again.")


def require_internal_secret(view: Callable) -> Callable:
    """Decorator for internal-only routes authenticated by a shared secret
    (this instance's own SECRET_KEY, set in its .env at provision time)
    instead of a session or login. The only caller is the admin app's own
    Ask AI, reaching this over localhost to run a natural-language instruction
    through this app's own chat tools — see pages.py's /internal/ai-command."""

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        expected = os.environ.get("SECRET_KEY", "")
        supplied = request.headers.get("X-Internal-Secret") or ""
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(403, "Not authorized.")
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Password hashing — stdlib hashlib.pbkdf2_hmac + secrets, not werkzeug.
# ---------------------------------------------------------------------------

_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_s, salt, digest_hex = (stored or "").split("$")
        iterations = int(iterations_s)
    except (ValueError, AttributeError):
        return False
    if algo != _PBKDF2_ALGO:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------------------
# Auth / current user
# ---------------------------------------------------------------------------


def login_user(user) -> None:
    """Establish an authenticated session. Clears any prior session state
    first (avoids fixation) and re-seeds the CSRF token in the same request
    so the freshly cleared session already carries one."""
    sess = get_session()
    sess.clear()
    sess["user_id"] = user.id
    sess["_csrf_token"] = secrets.token_urlsafe(32)


def logout_user() -> None:
    get_session().clear()


def current_user():
    """Cached per-request so repeated calls don't re-hit the DB."""
    from models import User

    cache_key = "_current_user_cache"
    sess_env = request.environ
    if cache_key in sess_env:
        return sess_env[cache_key]
    uid = get_session().get("user_id")
    user = None
    if uid is not None:
        try:
            user = User.get_by_id(uid)
        except User.DoesNotExist:
            user = None
    sess_env[cache_key] = user
    return user


def team_member(user=None):
    from models import TeamMember

    user = user or current_user()
    if user is None:
        return None
    try:
        return TeamMember.get(TeamMember.user == user)
    except TeamMember.DoesNotExist:
        return None


_ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}


def role_at_least(role: str | None, minimum: str) -> bool:
    return _ROLE_RANK.get(role or "", 0) >= _ROLE_RANK.get(minimum, 99)


def require_login(view: Callable) -> Callable:
    """Decorator for routes in pages.py that need an authenticated user."""

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        if current_user() is None:
            redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


def require_role(minimum_role: str) -> Callable[[Callable], Callable]:
    """Decorator: require the current user's team role >= minimum_role.
    Assumes require_login already ran (or is stacked directly above this)."""

    def decorator(view: Callable) -> Callable:
        @functools.wraps(view)
        def wrapper(*args: Any, **kwargs: Any):
            if current_user() is None:
                redirect(url_for("login"))
            member = team_member()
            if member is None or not role_at_least(member.role, minimum_role):
                abort(403, "You don't have access to that.")
            return view(*args, **kwargs)

        return wrapper

    return decorator


def any_team_members_exist() -> bool:
    from models import TeamMember

    return TeamMember.select().limit(1).count() > 0


# ---------------------------------------------------------------------------
# url_for — thin wrapper over Bottle's named-route lookup so templates and
# pages.py have a stable, framework-shaped API.
# ---------------------------------------------------------------------------


def url_for(name: str, **kwargs: Any) -> str:
    from app import app

    query = {}
    path_kwargs = {}
    for k, v in kwargs.items():
        if k.startswith("_"):
            continue
        path_kwargs[k] = v
    try:
        path = app.get_url(name, **path_kwargs)
    except Exception:
        # Fall back to a best-effort path so a typo'd route name degrades to
        # a 404 rather than a 500 while a template is being edited.
        path = "/" + name.strip("/")
    qs = kwargs.get("_query")
    if qs:
        from urllib.parse import urlencode

        path = f"{path}?{urlencode(qs)}"
    return path


# ---------------------------------------------------------------------------
# Flash messages (session-based, one-shot)
# ---------------------------------------------------------------------------


def flash(message: str, category: str = "info") -> None:
    sess = get_session()
    flashes = sess.setdefault("_flashes", [])
    flashes.append({"message": message, "category": category})


def get_flashed_messages() -> list[dict]:
    """Pop-and-return: each flash is shown exactly once."""
    sess = get_session()
    flashes = sess.get("_flashes", [])
    sess["_flashes"] = []
    return flashes


# ---------------------------------------------------------------------------
# Mailer — stdlib urllib + ssl only (Postmark's HTTP API), no certifi: plain
# ssl.create_default_context() works fine against api.postmarkapp.com on
# Linux via the system CA bundle. Shape ported from admin/services/mailer.py.
# ---------------------------------------------------------------------------

_SSL_CTX = ssl.create_default_context()


class MailerError(RuntimeError):
    """Raised when an email can't be sent. Routes surface the message."""


class Mailer:
    APP_NAME = os.environ.get("APP_NAME", "Your App")
    API_URL = "https://api.postmarkapp.com/email"
    TIMEOUT = 30

    @staticmethod
    def _token() -> str | None:
        return os.environ.get("POSTMARK_API_KEY") or None

    @staticmethod
    def _from_address() -> str:
        return os.environ.get("POSTMARK_FROM", "no-reply@example.com")

    @classmethod
    def send(cls, *, to: str, subject: str, html_body: str, text_body: str | None = None) -> dict:
        token = cls._token()
        if not token:
            raise MailerError("Email isn't configured — set POSTMARK_API_KEY in your .env.")
        payload = {
            "From": cls._from_address(),
            "To": to,
            "Subject": subject,
            "HtmlBody": html_body,
            "TextBody": text_body or cls._html_to_text(html_body),
            "MessageStream": "outbound",
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            cls.API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Postmark-Server-Token": token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=cls.TIMEOUT, context=_SSL_CTX) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", errors="replace")).get("Message", "")
            except Exception:
                pass
            raise MailerError(detail or f"Postmark returned HTTP {e.code}.")
        except urllib.error.URLError as e:
            raise MailerError(f"Couldn't reach Postmark: {e.reason}")
        except (OSError, json.JSONDecodeError) as e:
            raise MailerError(f"Email error: {e}")

    @classmethod
    def send_invite(cls, *, email: str, invite_url: str, inviter_name: str) -> dict:
        subject = f"{inviter_name} invited you to join {cls.APP_NAME}"
        html_body = cls._wrap(
            f"<p>{_esc(inviter_name)} invited you to join their team on {_esc(cls.APP_NAME)}.</p>"
            f'<p><a href="{_esc(invite_url)}">Accept the invite</a></p>'
        )
        return cls.send(to=email, subject=subject, html_body=html_body)

    @classmethod
    def send_password_reset(cls, *, email: str, reset_url: str, ttl_minutes: int) -> dict:
        subject = f"Reset your {cls.APP_NAME} password"
        html_body = cls._wrap(
            f"<p>Click the link below to reset your password. It expires in {ttl_minutes} minutes.</p>"
            f'<p><a href="{_esc(reset_url)}">Reset your password</a></p>'
            "<p>If you didn't request this, you can ignore this email.</p>"
        )
        return cls.send(to=email, subject=subject, html_body=html_body)

    @staticmethod
    def _wrap(inner_html: str) -> str:
        return (
            '<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px">'
            f"{inner_html}</div>"
        )

    @staticmethod
    def _html_to_text(html: str) -> str:
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        return text.strip()


def _esc(s: str) -> str:
    import html as _html

    return _html.escape(s or "")


# ---------------------------------------------------------------------------
# Activity / notification recording
# ---------------------------------------------------------------------------


def record_activity(subject_type: str, subject_id: int, actor, verb: str, **payload: Any) -> None:
    from models import Activity

    Activity.create(
        subject_type=subject_type,
        subject_id=subject_id,
        actor=actor,
        verb=verb,
        payload_json=json.dumps(payload, default=str),
    )


def notify(user, kind: str, **payload: Any):
    from models import Notification

    if user is None:
        return None
    return Notification.create(user=user, kind=kind, payload_json=json.dumps(payload, default=str))


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return slug or "item"
