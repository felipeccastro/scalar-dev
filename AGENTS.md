# AGENTS.md — working on this app

Guidance for whoever (human or agent) changes this project next.

## Keep DOCUMENTATION.md in sync

**Any change that adds, removes, or changes a user-visible feature, route,
model field, or config var must update [DOCUMENTATION.md](DOCUMENTATION.md)
in the same change.** That file is the single source of truth for "what
this app does" — treat a feature change without a matching doc update as
incomplete, the same way you'd treat it as incomplete without the code.

- Find (or add) the relevant section and update the **Contents** list.
- New model/field → update the **Data model reference** table.
- New env var → update the **Configuration reference** table (and
  `.env.example`).
- Skip it for pure refactors, bug fixes that just restore already-documented
  behavior, or internal-only helper changes with no user-visible effect.
- If you're unsure whether something is user-visible enough to document,
  document it — a stale README is worse than a slightly over-eager one.

## Keep Ask AI in sync with the app

**Any new record type, field, or mutation added to the UI should get a
matching tool in `ai.py`, in the same change** — a create/update(/archive)
tool mirroring the new `pages/` route's fields and validation exactly, a
`TOOLS_SCHEMA` entry, a `_DISPATCH` entry, and (if it mutates anything) a
`_MUTATING_TOOLS` entry plus a `_describe_tool_call` branch for the
confirmation banner. `SYSTEM_PROMPT` should mention it too. The point of the
chat assistant is that it's a second way to *use* the app, not a smaller
subset of it — a feature only reachable by clicking through `pages/` is a
feature Ask AI can't help with. A field that exists on the model and in the
form but not in `ai.py`'s tool signature is exactly the kind of gap that
makes the assistant tell someone "I can't do that" for something the app
plainly supports.

- Read tools (`list_*`, `get_*`, `search`) execute immediately; write tools
  always pause for human confirmation (see `_MUTATING_TOOLS` in `ai.py`) — a
  new mutation is never the exception to that.
- This is also where a new tool's `search.index_entity(...)` call goes, the
  same as every other write path — see `search.py`.

## Conventions specific to this codebase

- **Zero pip dependencies beyond gunicorn** (dev-only, for autoreload).
  `bottle`/`peewee` are vendored as plain `.py` files in `vendor/`. Don't add
  a pip dependency without a good reason; if you must, note it in
  `requirements.txt` the way the gunicorn line already does.
- **No app-authored JS beyond what's declaratively necessary.** Mobile nav
  uses the checkbox hack; New Client/Task modals open via
  `commandfor`/`command="show-modal"`, wired by the already-vendored
  `oat.min.js` (native browser support when present, its own click handler
  otherwise) — not custom JS. The Esc-navigation listener and the theme
  toggle are the two deliberate exceptions, because there's no declarative
  way to do either; both are small, single-purpose, and commented inline
  in `layout.html`/`settings.html`. Follow that same shape for anything
  else that genuinely needs script — don't reach for a framework or bundler.
- **Schema changes go through `ensure_schema()`** in `models.py`: add a new
  model to `ALL_MODELS`, or a new column via `_add_column_if_missing()`.
  There's no `migrations/` directory and no `peewee-migrate` — this
  idempotent-check-at-startup approach *is* the migration story. Keep it
  that way.
- **Bottle template gotcha:** any source line whose first non-whitespace
  character is `%` is parsed as Python — including inside an HTML comment.
  Don't write something like a `%rebase(...)` call as documentation text on
  its own line in a `.html` template; bottle will try to execute it as a
  statement and throw a `SyntaxError`. (Hit exactly this once — see the git
  history around the Esc-navigation feature.) If you need to reference
  template syntax in a comment, keep it mid-line, not line-initial.
- **Theming:** `style.css`'s `:root` tokens use CSS `light-dark()` keyed off
  the `color-scheme` property, not a `[data-theme="light"]` override block.
  Add new color tokens the same way — `--foo: light-dark(lightVal,
  darkVal);` — rather than introducing a second block to keep in sync. A
  value that can't be a plain `<color>` (e.g. a multi-part `box-shadow`)
  needs decomposing into `light-dark()`-driven intermediate custom
  properties first — see `--shadow-card` for the pattern.
- **Flash messages** (`utils.py: flash()`) always render as `oat.toast()`
  popups via `templates/_toasts.html`, never an inline banner. Route any new
  user-facing confirmation/error through `flash()`, not a hand-rolled
  message element.
- **Generic-over-subject models** (`Comment`/`Attachment`/`Activity`) key
  off `subject_type` + `subject_id` rather than a table per model. A new
  commentable/attachable thing reuses these as-is; don't add a parallel
  `TaskComment` table.

## Verifying a change

- **Templates**: bottle's `SimpleTemplate` can compile a template directly
  (no server needed) to catch syntax errors — `python3 -c "from bottle
  import SimpleTemplate; SimpleTemplate(open('templates/x.html').read())"`
  (run with `vendor/` on `sys.path`).
- **Visual/behavioral changes**: run against a scratch database —
  `SQLITE_PATH=/tmp/scratch.db PORT=8123 python3 app.py` — rather than the
  real `app.db`, so local data doesn't need resetting afterward.
