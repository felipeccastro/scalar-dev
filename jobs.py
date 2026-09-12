"""Simple in-process scheduled jobs — stdlib only (threading + time), no
APScheduler/Celery/cron dependency, per the zero-pip-dependency constraint.

A single daemon thread wakes up every CHECK_INTERVAL seconds and runs
whichever registered jobs are due, tracking each job's next-run time in
memory (nothing persisted — a restart just resumes polling on the same
schedule, which is fine for jobs that check "is anything due?" against the
DB rather than relying on the scheduler itself to remember what it did).
Good enough for a handful of low-frequency jobs in a single-worker,
single-tenant app (see Makefile: WORKERS=1); this is not a distributed job
queue, and running with WORKERS>1 would run one of these threads per
worker process — harmless for something idempotent, but a reminder could
theoretically be picked up by two workers in the same poll window and
emailed twice. Not a concern at the default WORKERS=1.

The one job registered today is `_run_due_reminders`, which fires Reminders
created via Ask AI's create_reminder tool (see ai.py) once their remind_at
has passed: it creates an in-app Notification and emails the reminder's
owner, then marks it sent so the next poll skips it.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 30  # seconds between polls — reminders don't need sub-minute precision

_jobs: list[tuple[str, Callable[[], None], int]] = []  # (name, fn, interval_seconds)
_next_run: dict[str, float] = {}
_started = False
_lock = threading.Lock()


def register(name: str, fn: Callable[[], None], *, interval_seconds: int) -> None:
    """Register a job to run every `interval_seconds` once start() has been
    called. Call this at import time (see the bottom of this file) — the
    scheduler loop just walks whatever's registered here."""
    _jobs.append((name, fn, interval_seconds))


def _run_due(now: float) -> None:
    from models import db

    for name, fn, interval in _jobs:
        if now < _next_run.get(name, 0):
            continue
        _next_run[name] = now + interval
        # Each run gets its own DB connection/close, the same shape as
        # app.py's before_request/after_request hooks — there's no request
        # here to hang that off of, so the job loop does it directly.
        db.connect(reuse_if_open=True)
        try:
            fn()
        except Exception:
            logger.exception("Scheduled job %r failed", name)
        finally:
            if not db.is_closed():
                db.close()


def _loop() -> None:
    while True:
        _run_due(time.monotonic())
        time.sleep(CHECK_INTERVAL)


def start() -> None:
    """Start the background scheduler thread. Idempotent, so it's safe to
    call unconditionally at app startup even if this module ever ends up
    imported more than once in the same process. Daemon thread: it never
    blocks process exit."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="jobs-scheduler", daemon=True).start()


# ---------------------------------------------------------------------------
# The one job: fire due reminders.
# ---------------------------------------------------------------------------


def _run_due_reminders() -> None:
    from models import Reminder
    from utils import Mailer, MailerError, notify, record_activity

    due = list(
        Reminder.select().where(
            Reminder.sent_at.is_null(True) & (Reminder.remind_at <= datetime.datetime.now())
        )
    )
    for reminder in due:
        try:
            notify(reminder.user, "reminder", message=reminder.message,
                   subject_type=reminder.subject_type, subject_id=reminder.subject_id)
            if reminder.subject_type and reminder.subject_id:
                # Client/Task are only ever soft-deleted (archived_at), never
                # hard-deleted, so the subject row is always still there to
                # log against.
                record_activity(reminder.subject_type, reminder.subject_id, reminder.user,
                                 "reminder_fired", message=reminder.message)
            try:
                Mailer.send_reminder(email=reminder.user.email, message=reminder.message)
            except MailerError:
                # Same "best-effort, don't block the feature" stance as
                # invite/reset emails elsewhere — the in-app notification
                # above already happened, so the reminder isn't silently
                # lost just because email isn't configured or Postmark is
                # down.
                logger.exception("Couldn't email reminder #%s", reminder.id)
        except Exception:
            # One bad reminder shouldn't stop the rest of this poll's batch
            # from firing.
            logger.exception("Failed to fire reminder #%s", reminder.id)
        finally:
            # Mark sent even if the block above raised or the email failed —
            # an error already logged is enough; retrying the same reminder
            # forever isn't the goal.
            reminder.sent_at = datetime.datetime.now()
            reminder.save()


register("run_due_reminders", _run_due_reminders, interval_seconds=CHECK_INTERVAL)
