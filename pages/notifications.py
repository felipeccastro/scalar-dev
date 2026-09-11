"""Notifications: list, mark-one-read, mark-all-read."""

from __future__ import annotations

import datetime

from app import app, render
from models import Notification
from utils import current_user, redirect, require_login, url_for


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
