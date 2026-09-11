"""Ask-AI chat: the user-facing thread plus the internal proxy endpoint the
admin app's own Ask AI calls into (see ai_command below).
"""

from __future__ import annotations

from bottle import request, response

from app import app, render
from models import ChatMessage, ChatThread, TeamMember
from utils import current_user, flash, redirect, require_internal_secret, url_for
import ai


@app.route("/chat", method="GET", name="chat")
def chat_page():
    thread = ChatThread.select().where(ChatThread.user == current_user()).first()
    messages = list(ChatMessage.select().where(ChatMessage.thread == thread).order_by(ChatMessage.id)) if thread else []
    rendered = [
        {"role": m.role, "html": ai.render_markdown(m.content) if m.role == "assistant" else m.content}
        for m in messages
    ]
    pending = ai.pending_state(thread) if thread else None
    return render("chat.html", messages=rendered, backend=ai.backend(), pending=pending)


@app.route("/chat", method="POST", name="chat_send")
def chat_send():
    text = (request.forms.get("message") or "").strip()
    if text:
        try:
            ai.send_message(current_user(), text)
        except ai.PendingActionError:
            flash("Please confirm or cancel the pending action first.", "error")
        except ai.LLMError as e:
            flash(f"The assistant couldn't answer that: {e}", "error")
        except ValueError:
            pass
    redirect(url_for("chat"))


@app.route("/chat/confirm", method="POST", name="chat_confirm")
def chat_confirm():
    thread, _ = ChatThread.get_or_create(user=current_user())
    try:
        ai.resolve_pending(thread, approved=True)
    except ValueError:
        pass  # nothing pending (stale double-submit) — ignore
    except ai.LLMError as e:
        flash(f"The action ran, but the assistant couldn't reply: {e}", "error")
    redirect(url_for("chat"))


@app.route("/chat/cancel", method="POST", name="chat_cancel")
def chat_cancel():
    thread, _ = ChatThread.get_or_create(user=current_user())
    try:
        ai.resolve_pending(thread, approved=False)
    except ValueError:
        pass
    except ai.LLMError as e:
        flash(f"The assistant couldn't reply: {e}", "error")
    redirect(url_for("chat"))


@app.route("/internal/ai-command", method="POST", name="ai_command")
@require_internal_secret
def ai_command():
    """The admin app's own Ask AI proxies a natural-language instruction
    here rather than reaching into this app's database directly — this app
    already has the right tools, validation, and confirmation flow for its
    own records (see ai.py), so admin's assistant reuses them instead of
    duplicating them. Authenticated by X-Internal-Secret (this instance's
    own SECRET_KEY), not a session — see require_internal_secret.

    Unlike the normal chat, a write here is applied immediately rather than
    paused for a human to confirm in this app's own UI: the admin operator
    who sent the instruction *is* the confirmation, the same way admin's own
    Apps write tools (edit_app_code etc.) apply immediately with no separate
    pause.
    """
    body = request.json or {}
    text = (body.get("instruction") or "").strip()
    if not text:
        response.status = 400
        return {"error": "instruction is required."}

    owner_membership = TeamMember.select().where(TeamMember.role == "owner").first()
    if owner_membership is None:
        response.status = 500
        return {"error": "No owner account found to act as."}
    actor = owner_membership.user

    try:
        _, assistant_msg = ai.send_message(actor, text)
    except ai.PendingActionError:
        response.status = 409
        return {"error": "This app's chat already has an action awaiting confirmation — resolve that first."}
    except ai.LLMError as e:
        response.status = 502
        return {"error": str(e)}
    except ValueError:
        response.status = 400
        return {"error": "instruction is required."}

    if assistant_msg is None:
        # send_message() paused for confirmation — auto-apply it (see the
        # docstring above) instead of leaving it stuck in this app's own
        # pending-action slot, where nothing would ever resolve it.
        thread, _ = ChatThread.get_or_create(user=actor)
        try:
            assistant_msg = ai.resolve_pending(thread, approved=True)
        except ai.LLMError as e:
            return {"reply": f"The change was applied, but I couldn't get a follow-up reply: {e}"}

    return {"reply": assistant_msg.content if assistant_msg else "(no reply)"}
