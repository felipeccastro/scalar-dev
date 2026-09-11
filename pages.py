"""Every route in the app. No blueprints — each view imports `app` directly
and decorates itself, per the flat-file layout this template app is built to
(an AI-editing tool needs to hold the whole app in context).
"""

from __future__ import annotations

import datetime
import os
import secrets
import uuid

from bottle import request, response

from app import app, render
from models import (
    Activity,
    Attachment,
    ChatMessage,
    ChatThread,
    Client,
    CLIENT_STATUSES,
    Comment,
    Invite,
    Notification,
    PasswordReset,
    Task,
    TASK_STATUSES,
    TeamMember,
    User,
    seed_demo_data,
)
import ai
from utils import (
    Mailer,
    MailerError,
    abort,
    any_team_members_exist,
    current_user,
    flash,
    get_flashed_messages,
    hash_password,
    login_user,
    logout_user,
    notify,
    record_activity,
    redirect,
    require_internal_secret,
    require_login,
    require_role,
    slugify,
    team_member,
    url_for,
    verify_password,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


@app.hook("before_request")
def _bootstrap_redirect() -> None:
    """Until the first owner account exists, every road leads to /register."""
    if request.path == "/register" or request.path.startswith("/static/"):
        return
    if not any_team_members_exist():
        redirect(url_for("register_owner"))


def _load_comments(subject_type: str, subject_id: int) -> list[Comment]:
    return list(
        Comment.select()
        .where((Comment.subject_type == subject_type) & (Comment.subject_id == subject_id))
        .order_by(Comment.created_at)
    )


def _load_attachments(subject_type: str, subject_id: int) -> list[Attachment]:
    return list(
        Attachment.select()
        .where((Attachment.subject_type == subject_type) & (Attachment.subject_id == subject_id))
        .order_by(Attachment.created_at.desc())
    )


def _load_activity(subject_type: str, subject_id: int, limit: int = 20) -> list[Activity]:
    return list(
        Activity.select()
        .where((Activity.subject_type == subject_type) & (Activity.subject_id == subject_id))
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )


def _team_members() -> list[dict]:
    rows = TeamMember.select().join(User)
    return [{"member": m, "user": m.user} for m in rows]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.route("/", method="GET", name="dashboard")
@require_login
def dashboard():
    open_clients = Client.select().where(Client.archived_at.is_null(True)).count()
    open_tasks = Task.select().where(Task.archived_at.is_null(True) & (Task.status != "done")).count()
    done_tasks = Task.select().where(Task.archived_at.is_null(True) & (Task.status == "done")).count()
    recent_clients = list(
        Client.select().where(Client.archived_at.is_null(True)).order_by(Client.created_at.desc()).limit(5)
    )
    recent_tasks = list(
        Task.select().where(Task.archived_at.is_null(True)).order_by(Task.created_at.desc()).limit(5)
    )
    return render(
        "dashboard.html",
        open_clients=open_clients,
        open_tasks=open_tasks,
        done_tasks=done_tasks,
        recent_clients=recent_clients,
        recent_tasks=recent_tasks,
    )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


@app.route("/clients", method="GET", name="clients_list")
@require_login
def clients_list():
    q = (request.query.get("q") or "").strip()
    query = Client.select().where(Client.archived_at.is_null(True))
    if q:
        query = query.where(
            Client.name.contains(q) | Client.email.contains(q) | Client.company.contains(q)
        )
    clients = list(query.order_by(Client.created_at.desc()))
    return render("clients_list.html", clients=clients, statuses=CLIENT_STATUSES, q=q)


@app.route("/clients", method="POST", name="clients_create")
@require_login
def clients_create():
    name = (request.forms.get("name") or "").strip()
    if not name:
        flash("A client needs a name.", "error")
        redirect(url_for("clients_list"))
    client = Client.create(
        name=name,
        email=(request.forms.get("email") or "").strip(),
        phone=(request.forms.get("phone") or "").strip(),
        company=(request.forms.get("company") or "").strip(),
        status=request.forms.get("status") or "lead",
        notes=(request.forms.get("notes") or "").strip(),
        created_by=current_user(),
    )
    record_activity("client", client.id, current_user(), "created")
    flash(f"Added {client.name}.", "success")
    redirect(url_for("client_detail", client_id=client.id))


@app.route("/clients/<client_id:int>", method="GET", name="client_detail")
@require_login
def client_detail(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is None:
        flash("That client doesn't exist.", "error")
        redirect(url_for("clients_list"))
    tasks = list(
        Task.select().where((Task.client == client) & (Task.archived_at.is_null(True))).order_by(Task.position)
    )
    return render(
        "client_detail.html",
        client=client,
        tasks=tasks,
        statuses=CLIENT_STATUSES,
        comments=_load_comments("client", client.id),
        attachments=_load_attachments("client", client.id),
        activity=_load_activity("client", client.id),
    )


@app.route("/clients/<client_id:int>", method="POST", name="client_update")
@require_login
def client_update(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is None:
        flash("That client doesn't exist.", "error")
        redirect(url_for("clients_list"))
    client.name = (request.forms.get("name") or client.name).strip()
    client.email = (request.forms.get("email") or "").strip()
    client.phone = (request.forms.get("phone") or "").strip()
    client.company = (request.forms.get("company") or "").strip()
    client.status = request.forms.get("status") or client.status
    client.notes = (request.forms.get("notes") or "").strip()
    client.updated_at = datetime.datetime.now()
    client.save()
    record_activity("client", client.id, current_user(), "updated")
    flash("Client updated.", "success")
    redirect(url_for("client_detail", client_id=client.id))


@app.route("/clients/<client_id:int>/archive", method="POST", name="client_archive")
@require_login
def client_archive(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is not None:
        client.archived_at = datetime.datetime.now()
        client.save()
        record_activity("client", client.id, current_user(), "archived")
        flash(f"Archived {client.name}.", "success")
    redirect(url_for("clients_list"))


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _active_clients() -> list[Client]:
    return list(Client.select().where(Client.archived_at.is_null(True)).order_by(Client.name))


def _team_users() -> list[User]:
    return [m.user for m in TeamMember.select().join(User).order_by(User.name)]


@app.route("/tasks", method="GET", name="tasks_list")
@require_login
def tasks_list():
    q = (request.query.get("q") or "").strip()
    query = Task.select().where(Task.archived_at.is_null(True))
    if q:
        query = query.where(Task.title.contains(q) | Task.description.contains(q))
    tasks = list(query.order_by(Task.position, Task.id))
    grouped = {s: [t for t in tasks if t.status == s] for s in TASK_STATUSES}
    return render(
        "tasks_list.html",
        grouped=grouped,
        statuses=TASK_STATUSES,
        clients=_active_clients(),
        team_users=_team_users(),
        q=q,
    )


@app.route("/tasks", method="POST", name="tasks_create")
@require_login
def tasks_create():
    title = (request.forms.get("title") or "").strip()
    if not title:
        flash("A task needs a title.", "error")
        redirect(url_for("tasks_list"))
    client_id = request.forms.get("client_id") or ""
    assignee_id = request.forms.get("assignee_id") or ""
    last = Task.select().order_by(Task.position.desc()).first()
    task = Task.create(
        title=title,
        description=(request.forms.get("description") or "").strip(),
        status=request.forms.get("status") or "todo",
        client=int(client_id) if client_id else None,
        assignee=int(assignee_id) if assignee_id else None,
        position=(last.position + 1) if last else 0,
        created_by=current_user(),
    )
    record_activity("task", task.id, current_user(), "created")
    if task.assignee_id and task.assignee_id != current_user().id:
        notify(task.assignee, "assignment", task_id=task.id, task_title=task.title)
    flash(f"Added “{task.title}”.", "success")
    redirect(url_for("task_detail", task_id=task.id))


@app.route("/tasks/<task_id:int>", method="GET", name="task_detail")
@require_login
def task_detail(task_id: int):
    task = Task.select().where(Task.id == task_id).first()
    if task is None:
        flash("That task doesn't exist.", "error")
        redirect(url_for("tasks_list"))
    return render(
        "task_detail.html",
        task=task,
        statuses=TASK_STATUSES,
        clients=_active_clients(),
        team_users=_team_users(),
        comments=_load_comments("task", task.id),
        attachments=_load_attachments("task", task.id),
        activity=_load_activity("task", task.id),
    )


@app.route("/tasks/<task_id:int>", method="POST", name="task_update")
@require_login
def task_update(task_id: int):
    task = Task.select().where(Task.id == task_id).first()
    if task is None:
        flash("That task doesn't exist.", "error")
        redirect(url_for("tasks_list"))
    old_status = task.status
    task.title = (request.forms.get("title") or task.title).strip()
    task.description = (request.forms.get("description") or "").strip()
    task.status = request.forms.get("status") or task.status
    client_id = request.forms.get("client_id") or ""
    assignee_id = request.forms.get("assignee_id") or ""
    task.client = int(client_id) if client_id else None
    new_assignee_id = int(assignee_id) if assignee_id else None
    reassigned = new_assignee_id and new_assignee_id != task.assignee_id
    task.assignee = new_assignee_id
    task.updated_at = datetime.datetime.now()
    task.save()
    if task.status != old_status:
        record_activity("task", task.id, current_user(), "status_changed", old=old_status, new=task.status)
    else:
        record_activity("task", task.id, current_user(), "updated")
    if reassigned and task.assignee_id != current_user().id:
        notify(task.assignee, "assignment", task_id=task.id, task_title=task.title)
    flash("Task updated.", "success")
    redirect(url_for("task_detail", task_id=task.id))


@app.route("/tasks/reorder", method="POST", name="tasks_reorder")
@require_login
def tasks_reorder():
    """Drag-and-drop endpoint for the /tasks board (see the script at the
    bottom of tasks_list.html). The board sends the *entire*, freshly
    dropped ordering of one column: every listed task gets that column's
    status and a position matching its index in the list. Only ever
    touches status/position — never title/description/etc. — so a drop
    can't clobber anything else about a task.
    """
    status = request.forms.get("status") or ""
    if status not in TASK_STATUSES:
        abort(400, "That's not a valid status.")
    task_ids = [int(v) for v in request.forms.getall("task_id") if v.isdigit()]
    tasks_by_id = {t.id: t for t in Task.select().where(Task.id.in_(task_ids))}
    for position, task_id in enumerate(task_ids):
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        old_status = task.status
        task.status = status
        task.position = position
        task.updated_at = datetime.datetime.now()
        task.save()
        if old_status != status:
            record_activity("task", task.id, current_user(), "status_changed", old=old_status, new=status)
    response.status = 204
    return ""


@app.route("/tasks/<task_id:int>/archive", method="POST", name="task_archive")
@require_login
def task_archive(task_id: int):
    task = Task.select().where(Task.id == task_id).first()
    if task is not None:
        task.archived_at = datetime.datetime.now()
        task.save()
        record_activity("task", task.id, current_user(), "archived")
        flash(f"Archived “{task.title}”.", "success")
    redirect(url_for("tasks_list"))


# ---------------------------------------------------------------------------
# Comments — generic over Client/Task via subject_type/subject_id.
# ---------------------------------------------------------------------------

_DETAIL_ROUTE = {"client": "client_detail", "task": "task_detail"}
_DETAIL_KWARG = {"client": "client_id", "task": "task_id"}


def _redirect_to_subject(subject_type: str, subject_id: int):
    kwarg = _DETAIL_KWARG.get(subject_type, "client_id")
    redirect(url_for(_DETAIL_ROUTE.get(subject_type, "clients_list"), **{kwarg: subject_id}))


@app.route("/comments", method="POST", name="comment_create")
@require_login
def comment_create():
    subject_type = request.forms.get("subject_type") or ""
    subject_id = int(request.forms.get("subject_id") or 0)
    body = (request.forms.get("body") or "").strip()
    if subject_type not in ("client", "task") or not subject_id or not body:
        flash("Couldn't add that comment.", "error")
        redirect(url_for("dashboard"))
    Comment.create(subject_type=subject_type, subject_id=subject_id, body=body, author=current_user())
    record_activity(subject_type, subject_id, current_user(), "commented")
    if subject_type == "task":
        task = Task.select().where(Task.id == subject_id).first()
        if task is not None and task.assignee_id and task.assignee_id != current_user().id:
            notify(task.assignee, "comment", task_id=task.id, task_title=task.title)
    _redirect_to_subject(subject_type, subject_id)


@app.route("/comments/<comment_id:int>/delete", method="POST", name="comment_delete")
@require_login
def comment_delete(comment_id: int):
    comment = Comment.select().where(Comment.id == comment_id).first()
    if comment is not None:
        subject_type, subject_id = comment.subject_type, comment.subject_id
        if comment.author_id == current_user().id:
            comment.delete_instance()
            flash("Comment deleted.", "success")
        else:
            flash("You can only delete your own comments.", "error")
        _redirect_to_subject(subject_type, subject_id)
    redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Attachments — local disk only, path from UPLOAD_FOLDER (defaults to
# ./uploads next to this file; gitignored, see .gitignore).
# ---------------------------------------------------------------------------


@app.route("/attachments", method="POST", name="attachment_upload")
@require_login
def attachment_upload():
    subject_type = request.forms.get("subject_type") or ""
    subject_id = int(request.forms.get("subject_id") or 0)
    upload = request.files.get("file")
    if subject_type not in ("client", "task") or not subject_id or upload is None or not upload.filename:
        flash("Choose a file to upload.", "error")
        _redirect_to_subject(subject_type or "client", subject_id or 0)
    safe_name = f"{uuid.uuid4().hex}_{slugify(os.path.splitext(upload.filename)[0])}{os.path.splitext(upload.filename)[1]}"
    rel_dir = os.path.join(subject_type, str(subject_id))
    abs_dir = os.path.join(UPLOAD_FOLDER, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(abs_dir, safe_name)
    upload.save(abs_path)
    size = os.path.getsize(abs_path)
    if size > MAX_UPLOAD_SIZE:
        os.remove(abs_path)
        flash("That file is too large (10 MB max).", "error")
        _redirect_to_subject(subject_type, subject_id)
    Attachment.create(
        subject_type=subject_type,
        subject_id=subject_id,
        filename=upload.filename,
        stored_path=os.path.join(rel_dir, safe_name),
        mime=upload.content_type or "application/octet-stream",
        size=size,
        uploaded_by=current_user(),
    )
    record_activity(subject_type, subject_id, current_user(), "attached", filename=upload.filename)
    flash(f"Uploaded {upload.filename}.", "success")
    _redirect_to_subject(subject_type, subject_id)


@app.route("/attachments/<attachment_id:int>", method="GET", name="attachment_download")
@require_login
def attachment_download(attachment_id: int):
    from bottle import static_file

    attachment = Attachment.select().where(Attachment.id == attachment_id).first()
    if attachment is None:
        redirect(url_for("dashboard"))
    return static_file(attachment.stored_path, root=UPLOAD_FOLDER, download=attachment.filename)


@app.route("/attachments/<attachment_id:int>/delete", method="POST", name="attachment_delete")
@require_login
def attachment_delete(attachment_id: int):
    attachment = Attachment.select().where(Attachment.id == attachment_id).first()
    if attachment is not None:
        subject_type, subject_id = attachment.subject_type, attachment.subject_id
        abs_path = os.path.join(UPLOAD_FOLDER, attachment.stored_path)
        if os.path.exists(abs_path):
            os.remove(abs_path)
        attachment.delete_instance()
        flash("Attachment removed.", "success")
        _redirect_to_subject(subject_type, subject_id)
    redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@app.route("/notifications", method="GET", name="notifications_list")
@require_login
def notifications_list():
    notifications = list(
        Notification.select().where(Notification.user == current_user()).order_by(Notification.created_at.desc())
    )
    return render("notifications.html", notifications=notifications)


@app.route("/notifications/<notification_id:int>/read", method="POST", name="notification_read")
@require_login
def notification_read(notification_id: int):
    n = Notification.select().where(
        (Notification.id == notification_id) & (Notification.user == current_user())
    ).first()
    if n is not None and n.read_at is None:
        n.read_at = datetime.datetime.now()
        n.save()
    redirect(url_for("notifications_list"))


@app.route("/notifications/read-all", method="POST", name="notifications_read_all")
@require_login
def notifications_read_all():
    Notification.update(read_at=datetime.datetime.now()).where(
        (Notification.user == current_user()) & (Notification.read_at.is_null(True))
    ).execute()
    redirect(url_for("notifications_list"))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.route("/settings", method="GET", name="settings")
@require_login
def settings_page():
    member = team_member()
    return render(
        "settings.html",
        member=member,
        team=_team_members(),
        pending_invites=list(Invite.select().where(Invite.accepted == False)),  # noqa: E712
    )


@app.route("/settings/password", method="POST", name="settings_password")
@require_login
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
# Ask-AI chat
# ---------------------------------------------------------------------------


@app.route("/chat", method="GET", name="chat")
@require_login
def chat_page():
    thread = ChatThread.select().where(ChatThread.user == current_user()).first()
    messages = list(ChatMessage.select().where(ChatMessage.thread == thread).order_by(ChatMessage.id)) if thread else []
    rendered = [
        {"role": m.role, "html": ai.render_markdown(m.content) if m.role == "assistant" else m.content}
        for m in messages
    ]
    pending = ai.pending_state(thread) if thread else None
    return render("chat.html", messages=rendered, backend=ai.backend(), pending=pending)


@app.route("/chat", method="POST", name="chat_send")
@require_login
def chat_send():
    text = (request.forms.get("message") or "").strip()
    if text:
        try:
            ai.send_message(current_user(), text)
        except ai.PendingActionError:
            flash("Please confirm or cancel the pending action first.", "error")
        except ai.LLMError as e:
            flash(f"The assistant couldn't answer that: {e}", "error")
        except ValueError:
            pass
    redirect(url_for("chat"))


@app.route("/chat/confirm", method="POST", name="chat_confirm")
@require_login
def chat_confirm():
    thread, _ = ChatThread.get_or_create(user=current_user())
    try:
        ai.resolve_pending(thread, approved=True)
    except ValueError:
        pass  # nothing pending (stale double-submit) — ignore
    except ai.LLMError as e:
        flash(f"The action ran, but the assistant couldn't reply: {e}", "error")
    redirect(url_for("chat"))


@app.route("/chat/cancel", method="POST", name="chat_cancel")
@require_login
def chat_cancel():
    thread, _ = ChatThread.get_or_create(user=current_user())
    try:
        ai.resolve_pending(thread, approved=False)
    except ValueError:
        pass
    except ai.LLMError as e:
        flash(f"The assistant couldn't reply: {e}", "error")
    redirect(url_for("chat"))


@app.route("/internal/ai-command", method="POST", name="ai_command")
@require_internal_secret
def ai_command():
    """The admin app's own Ask AI proxies a natural-language instruction
    here rather than reaching into this app's database directly — this app
    already has the right tools, validation, and confirmation flow for its
    own records (see ai.py), so admin's assistant reuses them instead of
    duplicating them. Authenticated by X-Internal-Secret (this instance's
    own SECRET_KEY), not a session — see require_internal_secret.

    Unlike the normal chat, a write here is applied immediately rather than
    paused for a human to confirm in this app's own UI: the admin operator
    who sent the instruction *is* the confirmation, the same way admin's own
    Apps write tools (edit_app_code etc.) apply immediately with no separate
    pause.
    """
    body = request.json or {}
    text = (body.get("instruction") or "").strip()
    if not text:
        response.status = 400
        return {"error": "instruction is required."}

    owner_membership = TeamMember.select().where(TeamMember.role == "owner").first()
    if owner_membership is None:
        response.status = 500
        return {"error": "No owner account found to act as."}
    actor = owner_membership.user

    try:
        _, assistant_msg = ai.send_message(actor, text)
    except ai.PendingActionError:
        response.status = 409
        return {"error": "This app's chat already has an action awaiting confirmation — resolve that first."}
    except ai.LLMError as e:
        response.status = 502
        return {"error": str(e)}
    except ValueError:
        response.status = 400
        return {"error": "instruction is required."}

    if assistant_msg is None:
        # send_message() paused for confirmation — auto-apply it (see the
        # docstring above) instead of leaving it stuck in this app's own
        # pending-action slot, where nothing would ever resolve it.
        thread, _ = ChatThread.get_or_create(user=actor)
        try:
            assistant_msg = ai.resolve_pending(thread, approved=True)
        except ai.LLMError as e:
            return {"reply": f"The change was applied, but I couldn't get a follow-up reply: {e}"}

    return {"reply": assistant_msg.content if assistant_msg else "(no reply)"}
