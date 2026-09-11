"""Auth: owner registration, login/logout, teammate invites, password reset."""

from __future__ import annotations

import datetime
import secrets

from bottle import request

from app import app, render
from models import Invite, PasswordReset, TeamMember, User, seed_demo_data
from utils import (
    Mailer,
    MailerError,
    any_team_members_exist,
    current_user,
    flash,
    hash_password,
    login_user,
    logout_user,
    redirect,
    require_login,
    require_role,
    url_for,
    verify_password,
)


@app.route("/register", method="GET", name="register_owner")
def register_owner_form():
    if any_team_members_exist():
        redirect(url_for("login"))
    return render("register.html")


@app.route("/register", method="POST", name="register_owner_submit")
def register_owner_submit():
    if any_team_members_exist():
        redirect(url_for("login"))
    name = (request.forms.get("name") or "").strip()
    email = (request.forms.get("email") or "").strip().lower()
    password = request.forms.get("password") or ""
    if not name or not email or len(password) < 8:
        flash("Enter a name, email, and a password of at least 8 characters.", "error")
        redirect(url_for("register_owner"))
    if User.select().where(User.email == email).exists():
        flash("That email is already in use.", "error")
        redirect(url_for("register_owner"))
    user = User.create(name=name, email=email, password_hash=hash_password(password))
    TeamMember.create(user=user, role="owner")
    seed_demo_data(user)
    login_user(user)
    flash(f"Welcome, {name}! We've added a couple of sample clients and tasks to get you started.", "success")
    redirect(url_for("dashboard"))


@app.route("/login", method="GET", name="login")
def login_form():
    if current_user() is not None:
        redirect(url_for("dashboard"))
    return render("login.html")


@app.route("/login", method="POST", name="login_submit")
def login_submit():
    email = (request.forms.get("email") or "").strip().lower()
    password = request.forms.get("password") or ""
    try:
        user = User.get(User.email == email)
    except User.DoesNotExist:
        user = None
    if user is None or not verify_password(password, user.password_hash):
        flash("Incorrect email or password.", "error")
        redirect(url_for("login"))
    login_user(user)
    redirect(url_for("dashboard"))


@app.route("/logout", method="POST", name="logout")
@require_login
def logout():
    logout_user()
    redirect(url_for("login"))


@app.route("/invite", method="POST", name="invite_teammate")
@require_login
@require_role("admin")
def invite_teammate():
    email = (request.forms.get("email") or "").strip().lower()
    if not email:
        flash("Enter an email to invite.", "error")
        redirect(url_for("settings"))
    if User.select().where(User.email == email).exists():
        flash("That person already has an account.", "error")
        redirect(url_for("settings"))
    token = secrets.token_urlsafe(32)
    Invite.create(email=email, token=token, role="member", invited_by=current_user())
    invite_url = request.url.split("/invite", 1)[0] + url_for("accept_invite", token=token)
    try:
        Mailer.send_invite(email=email, invite_url=invite_url, inviter_name=current_user().name)
        flash(f"Invite sent to {email}.", "success")
    except MailerError as e:
        flash(f"Couldn't send the invite email ({e}). Share this link instead: {invite_url}", "error")
    redirect(url_for("settings"))


@app.route("/invite/<token>", method="GET", name="accept_invite")
def accept_invite_form(token: str):
    try:
        invite = Invite.get((Invite.token == token) & (Invite.accepted == False))  # noqa: E712
    except Invite.DoesNotExist:
        flash("That invite link is invalid or has already been used.", "error")
        redirect(url_for("login"))
    return render("accept_invite.html", invite=invite)


@app.route("/invite/<token>", method="POST", name="accept_invite_submit")
def accept_invite_submit(token: str):
    try:
        invite = Invite.get((Invite.token == token) & (Invite.accepted == False))  # noqa: E712
    except Invite.DoesNotExist:
        flash("That invite link is invalid or has already been used.", "error")
        redirect(url_for("login"))
    name = (request.forms.get("name") or "").strip()
    password = request.forms.get("password") or ""
    if not name or len(password) < 8:
        flash("Enter your name and a password of at least 8 characters.", "error")
        redirect(url_for("accept_invite", token=token))
    user = User.create(name=name, email=invite.email, password_hash=hash_password(password))
    TeamMember.create(user=user, role=invite.role)
    invite.accepted = True
    invite.save()
    login_user(user)
    flash("Welcome to the team!", "success")
    redirect(url_for("dashboard"))


@app.route("/forgot-password", method="GET", name="forgot_password")
def forgot_password_form():
    return render("forgot_password.html")


@app.route("/forgot-password", method="POST", name="forgot_password_submit")
def forgot_password_submit():
    email = (request.forms.get("email") or "").strip().lower()
    try:
        user = User.get(User.email == email)
    except User.DoesNotExist:
        user = None
    # Always show the same message, whether or not the address exists —
    # don't leak account existence.
    if user is not None:
        PasswordReset.update(used=True).where(
            (PasswordReset.user == user) & (PasswordReset.used == False)  # noqa: E712
        ).execute()
        token = secrets.token_urlsafe(32)
        expires_at = datetime.datetime.now() + datetime.timedelta(minutes=30)
        PasswordReset.create(user=user, token=token, expires_at=expires_at)
        reset_url = request.url.split("/forgot-password", 1)[0] + url_for("reset_password", token=token)
        try:
            Mailer.send_password_reset(email=email, reset_url=reset_url, ttl_minutes=30)
        except MailerError:
            pass  # still show the generic success message below
    flash("If that email has an account, a reset link is on its way.", "success")
    redirect(url_for("login"))


@app.route("/reset-password/<token>", method="GET", name="reset_password")
def reset_password_form(token: str):
    return render("reset_password.html", token=token)


@app.route("/reset-password/<token>", method="POST", name="reset_password_submit")
def reset_password_submit(token: str):
    password = request.forms.get("password") or ""
    try:
        reset = PasswordReset.get(
            (PasswordReset.token == token)
            & (PasswordReset.used == False)  # noqa: E712
            & (PasswordReset.expires_at > datetime.datetime.now())
        )
    except PasswordReset.DoesNotExist:
        flash("That reset link is invalid or has expired.", "error")
        redirect(url_for("forgot_password"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        redirect(url_for("reset_password", token=token))
    user = reset.user
    user.password_hash = hash_password(password)
    user.save()
    reset.used = True
    reset.save()
    flash("Password updated — you can sign in now.", "success")
    redirect(url_for("login"))
