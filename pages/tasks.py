"""Task board/list/detail/create/update/archive, plus the drag-and-drop
reorder endpoint the /tasks board posts to.
"""

from __future__ import annotations

import datetime

from bottle import request, response

from app import app, render
from models import TASK_STATUSES, Client, Task, TeamMember, User
from pages._shared import _load_activity, _load_attachments, _load_comments
from utils import abort, current_user, flash, notify, record_activity, redirect, require_login, url_for


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
