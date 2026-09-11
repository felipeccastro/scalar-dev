"""Team settings: member list, pending invites, change password."""

from __future__ import annotations

from bottle import request

from app import app, render
from models import Invite, TeamMember, User
from utils import current_user, flash, hash_password, redirect, team_member, url_for, verify_password


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
