"""Query/redirect helpers used by more than one route module — kept here
instead of duplicated or hung off whichever module happened to need one
first.
"""

from __future__ import annotations

from models import Activity, Attachment, Comment
from utils import redirect, url_for


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


# Comments/attachments are generic over Client/Task via subject_type/subject_id
# (see models.py) — this is where a create/delete redirects back to.
_DETAIL_ROUTE = {"client": "client_detail", "task": "task_detail"}
_DETAIL_KWARG = {"client": "client_id", "task": "task_id"}


def _redirect_to_subject(subject_type: str, subject_id: int):
    kwarg = _DETAIL_KWARG.get(subject_type, "client_id")
    redirect(url_for(_DETAIL_ROUTE.get(subject_type, "clients_list"), **{kwarg: subject_id}))
