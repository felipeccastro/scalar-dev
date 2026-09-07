# Binders Core

A small single-tenant CRM template — Clients, Tasks, comments, attachments,
notifications, and a read/write **Ask AI** chat assistant — meant as a
starting point to fork and build on, not as a product of its own.

Server-rendered [Bottle](https://bottlepy.org) + [peewee](http://docs.peewee-orm.com)
over SQLite. No build step, no Node, and no `pip install` needed for the app
itself — `bottle` and `peewee` are vendored as plain `.py` files under
`vendor/` (MIT-licensed; see `vendor/LICENSE.*`). Styling comes from the
[oat.css](https://oat.style) design-system library (also vendored),
retthemed in `static/style.css`.

- **What's built:** [DOCUMENTATION.md](DOCUMENTATION.md)
- **Working on this app?** Read [AGENTS.md](AGENTS.md) first — in particular,
  DOCUMENTATION.md needs updating alongside most changes.

## Requirements

- Python 3.10+ (the code uses `X | None` type syntax).
- No external services required to run it. Optional integrations (AI chat,
  outbound email) degrade gracefully when unconfigured — see
  [Configuration](#configuration) below.

## Quick start

```bash
cp .env.example .env   # optional — sensible defaults work without it
python3 app.py
```

Then open http://localhost:8000 and register the first account (it becomes
the team's owner, and gets a couple of sample clients/tasks seeded in).

### Dev server with autoreload

`python3 app.py` alone doesn't reload on code changes. For that, run it
under gunicorn instead:

```bash
pip install gunicorn   # the one thing here that isn't vendored
make                   # same as: make run
```

`HOST`/`PORT`/`WORKERS` are overridable, e.g. `make PORT=8080`. Template and
static file edits show up on the next request either way, without a
restart (`app.py`'s `DEBUG` flag) — only `.py` changes need the
gunicorn `--reload` (or a manual restart, if running `python3 app.py`
directly).

## Configuration

Everything is optional — copy `.env.example` to `.env` and fill in only
what you need. Full reference in
[DOCUMENTATION.md § Configuration reference](DOCUMENTATION.md#configuration-reference).
Highlights:

- **Ask AI** works out of the box against a local [Ollama](https://ollama.com)
  install (`OLLAMA_HOST`/`OLLAMA_MODEL`); set `OPENAI_API_KEY` to use an
  OpenAI-compatible cloud API instead.
- **Invite/password-reset emails** need `POSTMARK_API_KEY` — without it, the
  app shows the invite/reset link directly in the UI instead of emailing it,
  so the flow still works for local dev.
- **`SECRET_KEY`** has a dev-only default; set a real value before deploying
  anywhere real (it signs the session cookie).

## Project layout

```
app.py        # Bottle app factory, hooks, template rendering, entrypoint
pages.py      # every route (no blueprints — one flat file)
models.py     # peewee models + ensure_schema() (the whole "migrations" story)
utils.py      # session/CSRF/password hashing/email/flash — hand-rolled, stdlib only
ai.py         # Ask AI: tool-calling agent loop + Markdown renderer
templates/    # Bottle SimpleTemplate (.html) views
static/       # style.css (app-specific) + vendor/ (oat.css/js, bottle, peewee)
uploads/      # attachment storage (gitignored; see UPLOAD_FOLDER)
```

See [DOCUMENTATION.md](DOCUMENTATION.md) for what each page/feature actually
does, and [AGENTS.md](AGENTS.md) for conventions to follow when changing any
of this.
