# Binders Core — Feature Documentation

This describes what's actually built, for a person (or an agent) picking up
this project without prior context. Code comments cover *why* a given line
is written the way it is; this file covers *what the product does*, page by
page. Keep it in sync with the code — see [AGENTS.md](AGENTS.md).

## Contents

- [Overview](#overview)
- [Accounts, team & access](#accounts-team--access)
- [Dashboard](#dashboard)
- [Clients](#clients)
- [Tasks](#tasks)
- [Comments, attachments & activity](#comments-attachments--activity)
- [Notifications](#notifications)
- [Ask AI (chat assistant)](#ask-ai-chat-assistant)
- [Settings & appearance](#settings--appearance)
- [Error pages](#error-pages)
- [Navigation & keyboard](#navigation--keyboard)
- [Email](#email)
- [Interface & behavior](#interface--behavior)
- [Data model reference](#data-model-reference)
- [Configuration reference](#configuration-reference)

## Overview

A small single-tenant CRM: **Clients** and **Tasks**, with comments,
file attachments, an activity log, in-app notifications, and a read/write
**Ask AI** chat assistant. "Single-tenant" means one running instance serves
one team — there's no workspace/org switcher; whoever signs up first is the
owner, and everyone else joins by invite.

Server-rendered [Bottle](https://bottlepy.org) + [peewee](http://docs.peewee-orm.com)
over SQLite, styled with the [oat.css](https://oat.style) design-system
library. No build step, no Node, no pip install for the app itself — bottle
and peewee are vendored as plain `.py` files in `vendor/`. See the
[README](README.md) for how to run it.

## Accounts, team & access

- **First run**: `/register` creates the first user, who becomes **owner**,
  and seeds two sample clients and three sample tasks so the app isn't an
  empty screen. Once any team member exists, `/register` redirects to
  `/login` — there's no open sign-up after that.
- **Roles**: `owner`, `admin`, `member` (`TeamMember.role`). The only
  role-gated action is **sending an invite** (owner/admin — `@require_role`
  in `pages/auth.py`). Everything else (Clients/Tasks CRUD, comments,
  attachments) is open to any signed-in team member; roles are otherwise
  informational (shown in the Settings team table).
- **Inviting**: an owner/admin enters an email on the Settings page, which
  emails a tokenized `/invite/<token>` accept link (see [Email](#email) for
  what happens if email isn't configured). The invite's role defaults to
  `member`. Accepting sets a name + password and logs the new user in.
- **Password reset**: `/forgot-password` emails a time-limited
  `/reset-password/<token>` link. Always shows the same "if that email has
  an account…" message either way, so the form can't be used to enumerate
  registered emails.
- **Sessions**: a single signed, HMAC'd cookie (see
  [Interface & behavior](#interface--behavior)) — no server-side session
  store.

## Dashboard

`/` — three stat tiles (open clients, open tasks, done tasks) and two
"recent" lists (5 most recently created clients / tasks, each with its
status badge), linking into the respective detail pages.

## Clients

`/clients` — a searchable table (name/email/company, via `?q=`) of
non-archived clients. **New client** opens a modal dialog (title bar, ✕
close, Cancel/Add buttons) with name (required), email, phone, company,
status (lead/active/inactive), and freeform notes. Clicking a row opens
`/clients/<id>`:

- Edit form for every field above, **Archive** (soft delete —
  `archived_at` is set, not a real row delete, so activity/comments/
  attachments referencing it survive), a file-upload **Attachments**
  section, and the shared **Comments**/**Activity** sidebar (see below).
- Lists that client's own tasks isn't shown on this page directly — tasks
  reference their client from the Tasks side (`Task.client`).

## Tasks

`/tasks` — a kanban board grouped by status (**To Do** / **In Progress** /
**Done**), each column headed by a dot + count. **New task** opens the same
kind of modal dialog: title (required), client (optional, from a select of
non-archived clients), assignee (optional, from the team), status,
description. Cards can be dragged between columns (plain HTML5
drag-and-drop, no library) to change status without opening the task; on
drop, the whole column's new order is POSTed to `/tasks/reorder`, which
only ever touches `status`/`position`, so it can't clobber anything else
about a task. Clicking a card opens `/tasks/<id>`:

- Edit form for every field above, **Archive**, **Attachments**, and the
  shared **Comments**/**Activity** sidebar.
- Reassigning a task to someone other than yourself sends them an
  **assignment** notification (see [Notifications](#notifications)).
- Saving records an `updated` (or `status_changed`, if the status field
  changed) activity entry and shows a **"Task updated."** toast.

Search (`?q=`) matches title or description.

## Comments, attachments & activity

Shared across Clients and Tasks (`Comment`/`Attachment`/`Activity` are keyed
by `subject_type` + `subject_id` rather than having a separate table per
model):

- **Comments**: plain text (rendered with `white-space: pre-wrap`, not
  Markdown — only Ask AI's replies get Markdown rendering). Any signed-in
  user can comment; only the comment's own author can delete it.
- **Attachments**: uploaded to local disk under `UPLOAD_FOLDER`
  (default `./uploads/<subject_type>/<subject_id>/`), 10 MB max per file.
  Download streams the file back with its original filename; delete removes
  both the DB row and the file.
- **Activity**: an append-only log (`created` / `updated` / `status_changed`
  / `archived` / …) shown oldest-first-hidden, newest-first-shown in the
  detail page's sidebar. Written by `record_activity()` — see
  [AGENTS.md](AGENTS.md) if you're adding a new mutation that should log one.

## Notifications

`/notifications` — a flat, newest-first list. Two kinds today:
**assignment** (you were made a task's assignee) and **comment** (someone
commented on something — not currently wired to any route, but the
`notify()` helper and template already handle the kind). Unread rows get a
dot marker; "Mark all read" and per-row "Mark read" are both one POST.
There's no unread-count badge in the sidebar nav yet.

## Ask AI (chat assistant)

`/chat` — a single running thread per user (no multi-thread UI; `ChatThread`
is 1:1 with `User`). Two interchangeable backends, picked automatically:

- **OpenAI-compatible cloud API** if `OPENAI_API_KEY` is set
  (`OPENAI_MODEL`, `OPENAI_BASE_URL` configurable).
- **Local Ollama** otherwise (`OLLAMA_HOST`, `OLLAMA_MODEL` configurable) —
  the page shows a "Backend: local Ollama" notice with the env vars to check
  if nothing answers.

It's a tool-calling agent (`ai.py`) over both **read** tools (`list_clients`,
`get_client`, `list_tasks`, `get_task`, `search`) and **write** tools
(`create_client`, `update_client`, `archive_client`, `create_task`,
`update_task`, `archive_task`) — up to `MAX_TOOL_ROUNDTRIPS` (6) per message.
Read tools execute immediately; a write tool call **pauses the turn** and
shows a confirmation banner ("The assistant wants to: …") with Confirm/
Cancel buttons before anything is actually written — `ChatThread.pending_*`
persists the paused state across requests so refreshing the page doesn't
lose it. Confirming re-enters the same tool loop with the write executed
(and its own activity/notification side effects, same as doing it by hand);
cancelling drops it and tells the model so.

Model replies are rendered through a small hand-rolled Markdown-to-HTML
renderer (`ai.py: render_markdown`) — the only place in the app that
happens; everything else (comments, descriptions, notes) is plain text.

## Settings & appearance

`/settings`:

- **Appearance** — a Dark/Light toggle. See
  [Interface & behavior](#interface--behavior) for how it's implemented.
- **Change password** (or **Set a password**, if the account somehow has
  none — the form always asks for the current password since there's no
  alternate sign-in method here to fall back on) — collapsed behind a
  `<details>`/`<summary>` disclosure. A failed or successful submit
  redirects to `#change-password` so the disclosure re-opens instead of
  hiding its own result.
- **Team** — every member's name/email/role, plus (owner/admin only) an
  invite form and a list of pending invite emails, laid out side by side
  (same `.detail-cols` two-column grid as the client/task detail pages;
  stacks on narrow screens).

## Error pages

`app.py`'s 404/403/500 handlers all render the same `error.html`, styled as
a small card (icon + monospace `ERR_<code>` tag, heading, message, a
divider, then actions) instead of a bare heading and link. The icon and its
tinted badge vary by code — a compass for 404, a padlock for 403, an alert
triangle for 500 — reusing this app's existing status-color tokens
(muted/warning/danger) rather than introducing new colors. A **Go home**
button is always shown; 500 also gets a **Try again** button that reloads
the same URL. Deliberately dependency-free (no icon library, no JS): the
500 handler already rolls back the DB and guards the render itself against
a second crash (see its comment in `app.py`), so the page most likely to
render right after something broke shouldn't lean on anything else that
could fail with it.

## Navigation & keyboard

- **Esc** goes "back" a level: a detail page (`/clients/<id>`,
  `/tasks/<id>`) goes to its list; a list page (`/clients`, `/tasks`) goes
  to the dashboard. Opt-in per page via `<body data-esc-back>`; pages that
  don't set it (dashboard, chat, settings, notifications) aren't affected.
  Suppressed while a modal dialog is open or while typing in a field.
- **Mobile nav**: below 860px, the sidebar becomes an off-canvas drawer
  (hamburger button, backdrop, slide-in) — pure CSS (`:checked` on a hidden
  checkbox), no JS.

## Email

Postmark's HTTP API (`utils.py: Mailer`), used for invite and
password-reset emails. Without `POSTMARK_API_KEY` set, sending raises
`MailerError`; both call sites catch that and fall back to putting the
link directly in a flash message ("Share this link instead: …") so the
flow still works without email configured — handy for local dev.

## Interface & behavior

- **Theme**: dark by default; the Settings toggle writes
  `localStorage['binders-core:theme']` and sets `document.documentElement
  .style.colorScheme`. `style.css`'s `:root` tokens are all defined with CSS
  `light-dark()`, which resolves off that `color-scheme` value — flipping it
  is the entire retheme, no second `[data-theme]` override block to keep in
  sync. A tiny inline script in `layout.html`'s `<head>` applies the saved
  preference before the stylesheets load, so there's no flash of the wrong
  theme. Light mode isn't a flat inversion of dark: the brand indigo
  (`--primary`) stays constant, but the pale accent colors
  (`--danger`/`--success`/`--warning`) get deepened for legibility as text
  on a white background, and each one's `-foreground` pairing (used for
  solid-fill buttons like **Archive**) flips alongside it.
- **Toasts**: every flashed message (`utils.py: flash()`) renders as an
  `oat.toast()` popup (top-right, colored by category) instead of an inline
  banner — see `templates/_toasts.html`. Embeds the message as a JS string
  literal via `app.js_string()`, which escapes `</script>`-breakout
  characters, since some messages interpolate user-supplied text (a
  filename, a task title).
- **Modals**: New Client/New Task open a native `<dialog>` (oat.css themes
  it as a centered card with a dimmed backdrop) via the declarative
  `commandfor`/`command="show-modal"` attributes — `oat.min.js` wires that
  up (using the browser's native support when present, its own click
  handler otherwise), so there's still no app-authored interaction JS for
  it.
- **Sidebar**: sticky + viewport-height, so the user name/logout footer at
  its bottom stays in view regardless of how tall the current page's
  content is.
- **View transitions**: `@view-transition { navigation: auto }` opts every
  same-origin navigation into the browser's cross-document transition
  (Chromium only; a no-op elsewhere). The default root crossfade is
  disabled (`::view-transition-old/new(root) { animation: none }`) — its
  `mix-blend-mode: plus-lighter` blend flashes white on a dark theme
  whenever the two page snapshots don't match exactly.
- **Chat layout**: the Ask AI page is a `flex-direction: column` page
  (`.chat-page`) sized to the viewport minus the shell's own padding, with
  the message log as the one `flex: 1` scrolling region — not a fixed `vh`
  guess, so it adapts to whatever else is on the page (the Ollama notice,
  the pending-confirmation banner) instead of clipping or leaving dead
  space.

## Data model reference

All models in `models.py`; schema changes are applied idempotently at
startup by `ensure_schema()` (see [AGENTS.md](AGENTS.md) — there's no
migrations directory).

| Model | Purpose |
|---|---|
| `User` | Account: email, password hash, name. |
| `TeamMember` | 1:1 with `User`. `role`: owner / admin / member. |
| `Invite` | Pending join invite: email, token, role, accepted flag. |
| `PasswordReset` | Single-use, time-limited reset token. |
| `Client` | name, email, phone, company, status, notes, soft-delete via `archived_at`. |
| `Task` | title, description, status, `assignee`/`client` (both optional FKs), `position` (board ordering), soft-delete via `archived_at`. |
| `Comment` | Generic over `subject_type`+`subject_id` (client/task). Plain text. |
| `Attachment` | Generic over subject. Local-disk file, metadata in DB. |
| `Activity` | Generic over subject. Append-only log: verb + JSON payload. |
| `Notification` | Per-user. `kind` + JSON payload, `read_at`. |
| `ChatThread` | 1:1 with `User`. Holds `pending_*` fields for an in-flight write confirmation. |
| `ChatMessage` | user/assistant turns in a thread. |

## Configuration reference

See `.env.example` for the full list with defaults; the short version:

| Var | Purpose |
|---|---|
| `SECRET_KEY` | Signs the session cookie. Set a real value in production. |
| `SQLITE_PATH` | DB file location (default `app.db` next to `models.py`). |
| `UPLOAD_FOLDER` | Attachment storage root (default `./uploads`). |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` | Ask AI, cloud backend. |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Ask AI, local backend (used when `OPENAI_API_KEY` is unset). |
| `POSTMARK_API_KEY` / `POSTMARK_FROM` | Invite/reset emails. Unset = flash a shareable link instead. |
| `HOST` / `PORT` / `DEBUG` | Dev server (see `Makefile`/`app.py`). |
