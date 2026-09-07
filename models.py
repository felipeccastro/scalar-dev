"""Peewee models for the template app.

Single-tenant: there is no Workspace concept at all — one instance == one
customer. Schema changes ship as idempotent "does this column exist yet"
checks run from ensure_schema() at startup (see the bottom of this file) —
there is no migrations/ directory and no peewee-migrate dependency, per the
zero-pip-dependency constraint (peewee + bottle only).
"""

from __future__ import annotations

import datetime
import os

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DatabaseProxy,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

# The concrete database is chosen at startup (see make_database/init_database)
# so the choice can be deferred until after .env is loaded (app.py does this).
db = DatabaseProxy()


def make_database() -> SqliteDatabase:
    """Build the concrete database. Local SQLite file, path overridable via
    SQLITE_PATH (defaults to app.db next to this file)."""
    sqlite_path = os.environ.get(
        "SQLITE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")
    )
    return SqliteDatabase(
        sqlite_path, pragmas={"journal_mode": "wal", "foreign_keys": 1}
    )


def init_database() -> SqliteDatabase:
    """Bind the proxy to the concrete backend. Idempotent."""
    if db.obj is None:
        db.initialize(make_database())
    return db.obj


class BaseModel(Model):
    class Meta:
        database = db


# ---------------------------------------------------------------------------
# Identity — no workspace, no "client" access tier. Whoever has an account
# has full access; TeamMember.role is informational (owner|admin|member).
# ---------------------------------------------------------------------------


class User(BaseModel):
    id = AutoField()
    email = CharField(unique=True)
    password_hash = CharField()
    name = CharField()
    created_at = DateTimeField(default=datetime.datetime.now)


class TeamMember(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="membership", unique=True, on_delete="CASCADE")
    role = CharField(default="member")  # owner | admin | member
    created_at = DateTimeField(default=datetime.datetime.now)


class Invite(BaseModel):
    """A pending "join the team" invite, emailed as a tokenized accept link."""

    id = AutoField()
    email = CharField()
    token = CharField(unique=True)
    role = CharField(default="member")
    invited_by = ForeignKeyField(User, backref="sent_invites", on_delete="CASCADE")
    accepted = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)


class PasswordReset(BaseModel):
    """Single-use, time-limited token for resetting a user's password."""

    id = AutoField()
    user = ForeignKeyField(User, backref="password_resets", on_delete="CASCADE")
    token = CharField(unique=True)
    expires_at = DateTimeField()
    used = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)


# ---------------------------------------------------------------------------
# Core typed models — real columns, no generic FieldDef/FieldValue.
# ---------------------------------------------------------------------------


class Client(BaseModel):
    id = AutoField()
    name = CharField()
    email = CharField(default="")
    phone = CharField(default="")
    company = CharField(default="")
    status = CharField(default="lead")  # lead | active | inactive
    notes = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    created_by = ForeignKeyField(User, backref="created_clients", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = ((("status",), False),)


class Task(BaseModel):
    id = AutoField()
    title = CharField()
    description = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    status = CharField(default="todo")  # todo | in_progress | done
    assignee = ForeignKeyField(User, backref="assigned_tasks", null=True, on_delete="SET NULL")
    client = ForeignKeyField(Client, backref="tasks", null=True, on_delete="SET NULL")
    position = IntegerField(default=0)
    created_by = ForeignKeyField(User, backref="created_tasks", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = (
            (("status", "position"), False),
            (("client",), False),
        )


# ---------------------------------------------------------------------------
# Generic-over-subject helpers (Comment/Attachment/Activity apply to either a
# Client or a Task, keyed by subject_type + subject_id rather than a link
# table per model — one implementation instead of two).
# ---------------------------------------------------------------------------

SUBJECT_TYPES = ("client", "task")

# Shared with pages.py (form choices) and ai.py (tool-schema enums / write-tool
# validation) — defined once here so ai.py can import them without importing
# pages.py back (pages.py does `import ai`, so that direction would cycle).
CLIENT_STATUSES = ("lead", "active", "inactive")
TASK_STATUSES = ("todo", "in_progress", "done")

_STATUS_LABELS = {
    "lead": "Lead", "active": "Active", "inactive": "Inactive",
    "todo": "To Do", "in_progress": "In Progress", "done": "Done",
}


def status_label(status: str) -> str:
    """Human-readable form of a Client/Task status value for display —
    e.g. "in_progress" -> "In Progress". The raw value (still what's
    stored, matched in queries, and submitted by forms/tool calls) is
    untouched; only what's shown to a person goes through this. Falls back
    to a generic underscore->title-case conversion for any status not in
    the table above, so a future status doesn't need this list updated."""
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


class Comment(BaseModel):
    id = AutoField()
    subject_type = CharField()  # "client" | "task"
    subject_id = IntegerField()
    body = TextField()  # plain text — rendered with white-space: pre-wrap
    author = ForeignKeyField(User, backref="comments", on_delete="CASCADE")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id"), False),)


class Attachment(BaseModel):
    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    filename = CharField()
    stored_path = CharField()  # relative path under UPLOAD_FOLDER
    mime = CharField(default="application/octet-stream")
    size = IntegerField(default=0)
    uploaded_by = ForeignKeyField(User, backref="uploads", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id"), False),)


class Activity(BaseModel):
    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    actor = ForeignKeyField(User, backref="activity", null=True, on_delete="SET NULL")
    verb = CharField()  # created | updated | status_changed | commented | archived | ...
    payload_json = TextField(default="{}")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id", "created_at"), False),)


class Notification(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="notifications", on_delete="CASCADE")
    kind = CharField()  # assignment | comment | ...
    payload_json = TextField(default="{}")
    read_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = (
            (("user", "read_at"), False),
            (("user", "created_at"), False),
        )


# ---------------------------------------------------------------------------
# Ask-AI chat — one running thread per user, kept simple (no multi-thread UI).
# ---------------------------------------------------------------------------


class ChatThread(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="chat_threads", on_delete="CASCADE")
    created_at = DateTimeField(default=datetime.datetime.now)
    # Set together when the AI proposes a write and it's paused for human
    # confirmation (see ai.py: _save_pending/_clear_pending/resolve_pending);
    # null the rest of the time.
    pending_tool_calls = TextField(null=True)  # JSON: raw tool_call dicts awaiting confirmation
    pending_convo = TextField(null=True)  # JSON: full in-flight message list, incl. the proposing assistant turn
    pending_round_idx = IntegerField(null=True)  # _agent_loop's round counter, so resume doesn't reset the budget


class ChatMessage(BaseModel):
    id = AutoField()
    thread = ForeignKeyField(ChatThread, backref="messages", on_delete="CASCADE")
    role = CharField()  # user | assistant
    content = TextField()
    created_at = DateTimeField(default=datetime.datetime.now)


# ---------------------------------------------------------------------------
# Schema — idempotent create + column checks, run at startup (no migrations/
# directory, no peewee-migrate; see the Context note in the project plan).
# ---------------------------------------------------------------------------

ALL_MODELS = [
    User,
    TeamMember,
    Invite,
    PasswordReset,
    Client,
    Task,
    Comment,
    Attachment,
    Activity,
    Notification,
    ChatThread,
    ChatMessage,
]


def seed_demo_data(owner: User) -> None:
    """A couple of realistic Clients/Tasks so a freshly-provisioned instance
    isn't an empty screen. Called once, right after the first owner registers
    (see pages.py:register_owner_submit) — not from ensure_schema(), since it
    needs a real User to attribute the rows to."""
    acme = Client.create(
        name="Acme Corp", email="hello@acme.example", company="Acme Corp",
        status="active", notes="Long-time client, monthly retainer.",
        created_by=owner,
    )
    northwind = Client.create(
        name="Northwind Traders", email="hi@northwind.example", company="Northwind Traders",
        status="lead", notes="Introduced last week, still evaluating.",
        created_by=owner,
    )
    Task.create(
        title="Send onboarding email", status="done",
        client=acme, assignee=owner, position=0, created_by=owner,
    )
    Task.create(
        title="Prepare proposal", description="Cover scope, timeline, and pricing.",
        status="in_progress", client=northwind, assignee=owner, position=1, created_by=owner,
    )
    Task.create(
        title="Quarterly check-in call", status="todo",
        client=acme, assignee=owner, position=2, created_by=owner,
    )


def _column_exists(table: str, column: str) -> bool:
    cur = db.execute_sql(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    """`ddl` is the full column definition, e.g. "VARCHAR(255) DEFAULT ''".
    Called from ensure_schema() for every future non-destructive column add —
    same pattern an AI edit would append here (one model field + one line)."""
    if not _column_exists(table, column):
        db.execute_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema() -> None:
    """Create any missing tables/columns/indexes. Safe to call on every
    startup. This is the entire "migrations" story for this app."""
    init_database()
    db.create_tables(ALL_MODELS, safe=True)
    # Columns added after the initial schema, one line per field added above:
    _add_column_if_missing("chatthread", "pending_tool_calls", "TEXT")
    _add_column_if_missing("chatthread", "pending_convo", "TEXT")
    _add_column_if_missing("chatthread", "pending_round_idx", "INTEGER")
