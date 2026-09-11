# Dev server. `make` (or `make run`) starts gunicorn with autoreload (picks
# up .py file changes; template/static edits already show up on the next
# request without a restart, via app.py's DEBUG flag — see app.py) and a
# worker restart every 1000 requests processed, to shake off any slow memory
# growth in a long-lived dev session. Same shape of command a provisioned
# instance runs under (see ../admin/launcher/provisioner.py), just invoked
# locally instead of by the launcher.
HOST ?= 0.0.0.0
PORT ?= 8000
WORKERS ?= 1

.PHONY: run dist
run:
	gunicorn app:app \
		--bind $(HOST):$(PORT) \
		--workers $(WORKERS) \
		--reload \
		--max-requests 1000

# Package a ready-to-run copy of this app for the landing page's
# "Download & Self-Host" button (../admin/routes/downloads.py serves the
# result) — the whole directory, seeded app.db included, so unzip-and-run
# shows the demo company immediately. .env is excluded: it's gitignored and
# per-install already (see .env.example) — without one, utils.py falls back
# to its documented dev SECRET_KEY, same as a fresh git clone. WAL sidecar
# files are excluded too: they're SQLite's in-flight journal, not data, and
# get rebuilt from app.db the moment anything reopens it.
dist:
	mkdir -p dist
	rm -f dist/core.zip
	cd .. && zip -rq core/dist/core.zip core \
		-x 'core/.env' \
		-x 'core/__pycache__/*' -x 'core/*/__pycache__/*' -x '*.pyc' \
		-x 'core/app.db-shm' -x 'core/app.db-wal' \
		-x 'core/dist/*'
	@echo "Built dist/core.zip"

.DEFAULT_GOAL := run
