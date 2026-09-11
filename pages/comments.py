"""Comments — generic over Client/Task via subject_type/subject_id."""

from __future__ import annotations

from bottle import request

from app import app
from models import Comment, Task
from pages._shared import _redirect_to_subject
from utils import current_user, flash, notify, record_activity, redirect, require_login, url_for


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
