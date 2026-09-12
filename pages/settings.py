"""Team settings: member list, pending invites, change password, .env editor."""

from __future__ import annotations

import html
import os
import re

from bottle import request

from app import BASE_DIR, app, render
from models import Invite, TeamMember, User
from utils import current_user, flash, hash_password, redirect, require_role, team_member, url_for, verify_password


def _team_members() -> list[dict]:
    rows = TeamMember.select().join(User)
    return [{"member": m, "user": m.user} for m in rows]


@app.route("/settings", method="GET", name="settings")
def settings_page():
    member = team_member()
    return render(
        "settings.html",
        member=member,
        team=_team_members(),
        pending_invites=list(Invite.select().where(Invite.accepted == False)),  # noqa: E712
    )


@app.route("/settings/password", method="POST", name="settings_password")
def settings_password():
    # Redirects target the #change-password <details> fragment — browsers
    # auto-expand a <details> containing the :target element, so the form
    # (hidden behind a summary the rest of the time) stays open/visible
    # across the redirect instead of swallowing its own error message.
    user = current_user()
    current_password = request.forms.get("current_password") or ""
    new_password = request.forms.get("new_password") or ""
    if not verify_password(current_password, user.password_hash):
        flash("Current password is incorrect.", "error")
        redirect(url_for("settings") + "#change-password")
    if len(new_password) < 8:
        flash("New password must be at least 8 characters.", "error")
        redirect(url_for("settings") + "#change-password")
    user.password_hash = hash_password(new_password)
    user.save()
    flash("Password changed.", "success")
    redirect(url_for("settings") + "#change-password")


# ---------------------------------------------------------------------------
# .env editor — reads .env.example as the schema (section headers + one
# commented-out "# KEY=example" line per setting) so the form always matches
# whatever that file documents, with no separate list of fields to keep in
# sync by hand. Current values come from os.environ (what the running
# process actually has loaded), not a re-read of the file, so the form
# reflects reality even if .env was hand-edited since the last restart.
# ---------------------------------------------------------------------------

_ENV_PATH = os.path.join(BASE_DIR, ".env")
_ENV_EXAMPLE_PATH = os.path.join(BASE_DIR, ".env.example")
_SECTION_RE = re.compile(r"^---\s*(.+?)\s*---$")
_SECRET_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD)$")


def _field_key(line: str) -> str | None:
    """The KEY of a "KEY=..." or "# KEY=..." template/env line, else None."""
    body = line.strip()
    if body.startswith("#"):
        body = body[1:].strip()
    m = re.match(r"^([A-Z_][A-Z0-9_]*)=", body)
    return m.group(1) if m else None


def _env_sections() -> list[dict]:
    """Parse .env.example into sections of fields, in file order."""
    sections: list[dict] = []
    current = None
    try:
        with open(_ENV_EXAMPLE_PATH) as f:
            lines = [line.rstrip("\n") for line in f]
    except OSError:
        return sections
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        body = stripped[1:].strip() if stripped.startswith("#") else stripped
        section_match = _SECTION_RE.match(body)
        if section_match:
            current = {"title": section_match.group(1), "description": [], "fields": []}
            sections.append(current)
            continue
        key = _field_key(stripped)
        if key:
            if current is None:
                current = {"title": "Other", "description": [], "fields": []}
                sections.append(current)
            example = body.partition("=")[2]
            current["fields"].append({
                "key": key,
                "example": example,
                "secret": bool(_SECRET_RE.search(key)),
            })
        elif current is not None and stripped.startswith("#"):
            current["description"].append(body)
    # .env.example's descriptions are multi-line comment blocks (occasionally
    # with a "- " bullet or two); a plain space-join reads as a run-on
    # sentence, so keep the line breaks as <br>s instead. Source is our own
    # checked-in file, not user input, but still escape it — it's the right
    # default for anything landing in a template with `{{!...}}`.
    for section in sections:
        section["description_html"] = "<br>".join(html.escape(line) for line in section["description"])
    return sections


def _read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(_ENV_PATH):
        return values
    with open(_ENV_PATH) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
    return values


def _write_env(submitted: dict[str, str]) -> None:
    """Rewrite .env from the .env.example template, filling in submitted
    values and re-commenting any field left blank. Keys already in .env that
    aren't in the template (hand-added, unrelated to this form) are kept
    as-is under a trailing "Custom" section instead of being dropped."""
    try:
        with open(_ENV_EXAMPLE_PATH) as f:
            template_lines = [line.rstrip("\n") for line in f]
    except OSError:
        template_lines = []

    known_keys: set[str] = set()
    out: list[str] = []
    for line in template_lines:
        key = _field_key(line)
        if key is None:
            out.append(line)
            continue
        known_keys.add(key)
        value = (submitted.get(key) or "").strip()
        out.append(f"{key}={value}" if value else line)

    existing = _read_env_file()
    custom = {k: v for k, v in existing.items() if k not in known_keys}
    custom.update({k: (v or "").strip() for k, v in submitted.items() if k not in known_keys and (v or "").strip()})
    if custom:
        out.append("")
        out.append("# --- Custom ---")
        out.extend(f"{k}={v}" for k, v in custom.items())

    with open(_ENV_PATH, "w") as f:
        f.write("\n".join(out).strip("\n") + "\n")

    # Reflect the change in the running process immediately for the handful
    # of settings that are re-read from os.environ on every use (SECRET_KEY,
    # the Resend/OpenAI keys); everything read once at import time (ports,
    # model names, file paths, ...) still needs a restart to take effect.
    for key in known_keys:
        value = (submitted.get(key) or "").strip()
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


@app.route("/settings/env", method="GET", name="settings_env")
@require_role("owner")
def settings_env_page():
    sections = _env_sections()
    current = {f["key"]: os.environ.get(f["key"], "") for s in sections for f in s["fields"]}
    return render(
        "settings_env.html",
        sections=sections,
        current=current,
        onboarding=request.query.get("onboarding") == "1",
    )


@app.route("/settings/env", method="POST", name="settings_env_save")
@require_role("owner")
def settings_env_save():
    sections = _env_sections()
    all_keys = [f["key"] for s in sections for f in s["fields"]]
    submitted = {key: request.forms.get(key) or "" for key in all_keys}
    _write_env(submitted)
    flash(
        "Saved. A few of these (ports, model names, file paths) only take effect after restarting the app.",
        "success",
    )
    if request.forms.get("onboarding") == "1":
        redirect(url_for("dashboard"))
    redirect(url_for("settings_env"))
