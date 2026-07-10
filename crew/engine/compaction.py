"""Summarize-and-continue when context fills (spec §5.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import PartUpdated, SessionUpdated
from .providers.base import Done, ErrorEv, ModelRequest, Msg, TextDelta
from .runner import history_to_messages
from .store import Session

if TYPE_CHECKING:
    from .facade import Engine

_SUMMARY_PROMPT = """\
Summarize this coding session so a fresh agent can continue seamlessly. Structure:

## Goal
What the user is trying to accomplish.

## State
What has been done so far: files created/modified (exact paths), key decisions, test results.

## In progress / next steps
What was underway and what should happen next.

## Constraints & context
Anything the agent must know: conventions, gotchas, user preferences stated in the session.

Be specific and complete; this summary replaces the full history."""


async def _small_model_text(engine: "Engine", messages: list[Msg], system: str = "You are a helpful assistant.") -> str:
    provider, model_id, _ = engine.providers.resolve(engine.config.small_model)
    req = ModelRequest(model_id=model_id, system=system, messages=messages, max_tokens=4096)
    text = ""
    async for event in provider.stream(req):
        if isinstance(event, TextDelta):
            text += event.text
        elif isinstance(event, ErrorEv):
            raise RuntimeError(event.message)
        elif isinstance(event, Done):
            break
    return text.strip()


async def compact_session(engine: "Engine", session: Session) -> str:
    history = await engine.store.session_parts(session.id)
    messages = history_to_messages(history)
    messages.append(Msg("user", [{"type": "text", "text": _SUMMARY_PROMPT}]))
    summary = await _small_model_text(engine, messages)

    message = await engine.store.add_message(session.id, "assistant", model=engine.config.small_model)
    part = await engine.store.add_part(message.id, "compaction", {"text": summary})
    engine.bus.emit(PartUpdated(
        session_id=session.id, message_id=message.id, part_id=part.id,
        part_type="compaction", data={"text": summary},
    ))
    engine.bus.emit(SessionUpdated(session_id=session.id))
    return summary


async def generate_title(engine: "Engine", session: Session, first_prompt: str) -> str:
    messages = [Msg("user", [{"type": "text", "text":
        f"Write a terse 3-8 word title for a coding session that starts with this request. "
        f"Return only the title, no quotes.\n\n{first_prompt[:2000]}"}])]
    try:
        title = await _small_model_text(engine, messages)
    except Exception:
        title = first_prompt.strip().split("\n")[0][:60]
    title = title.split("\n")[0][:80].strip()
    await engine.store.update_session(session.id, title=title)
    engine.bus.emit(SessionUpdated(session_id=session.id))
    return title
