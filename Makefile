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

.PHONY: run
run:
	gunicorn app:app \
		--bind $(HOST):$(PORT) \
		--workers $(WORKERS) \
		--reload \
		--max-requests 1000

.DEFAULT_GOAL := run
