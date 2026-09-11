"""Attachments — local disk only, path from UPLOAD_FOLDER (defaults to
./uploads next to app.py; gitignored, see .gitignore).
"""

from __future__ import annotations

import os
import uuid

from bottle import request

from app import BASE_DIR, app
from models import Attachment
from pages._shared import _redirect_to_subject
from utils import current_user, flash, record_activity, redirect, require_login, slugify, url_for

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


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
