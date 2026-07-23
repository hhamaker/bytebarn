"""Minimal terminal harness for the engine (spec §9.1) — no Qt.

Usage:
    python -m crew.cli "your prompt"          # run one prompt in ./
    python -m crew.cli --agent plan "prompt"
    python -m crew.cli --project /path "prompt"

Permissions default to ask -> answered on stdin.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .engine.events import PartUpdated, PermissionAsked, QuestionAsked, RunFinished, TaskStarted
from .engine.facade import Engine


async def _consume_events(engine: Engine) -> None:
    seen_text: dict[str, int] = {}
    async for event in engine.bus.subscribe():
        if isinstance(event, PartUpdated):
            if event.part_type == "text" and event.delta:
                sys.stdout.write(event.delta)
                sys.stdout.flush()
            elif event.part_type in ("tool", "task") and event.data.get("status") in ("running",):
                tool = event.data.get("tool", "")
                print(f"\n[{tool}] {str(event.data.get('input', ''))[:120]}")
            elif event.part_type in ("tool", "task") and event.data.get("status") in ("done", "error"):
                out = event.data.get("output", "")
                print(f"  -> {out[:400]}{'...' if len(out) > 400 else ''}")
        elif isinstance(event, PermissionAsked):
            print(f"\npermission: {event.tool} {event.arg!r} [y=allow / a=always / n=deny] ", end="", flush=True)
            answer = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            answer = answer.strip().lower()
            verdict = {"y": "allow", "a": "allow_always"}.get(answer, "deny")
            engine.answer_permission(event.request_id, verdict)
        elif isinstance(event, QuestionAsked):
            print(f"\nquestion: {event.question}")
            for i, opt in enumerate(event.options):
                print(f"  {i + 1}. {opt}")
            answer = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            answer = answer.strip()
            if answer.isdigit() and event.options and 0 < int(answer) <= len(event.options):
                answer = event.options[int(answer) - 1]
            engine.answer_question(event.request_id, answer)
        elif isinstance(event, TaskStarted):
            print(f"\n[crew] {event.agent} <- {event.description}")
        elif isinstance(event, RunFinished):
            print(f"\n[run {event.status}]")
            return


async def main() -> None:
    parser = argparse.ArgumentParser(description="ByteBarn engine CLI harness")
    parser.add_argument("prompt")
    parser.add_argument("--project", default=".")
    parser.add_argument("--agent", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    engine = Engine(Path(args.project))
    await engine.start()
    try:
        session = await engine.new_session(agent=args.agent, model=args.model)
        consumer = asyncio.ensure_future(_consume_events(engine))
        await engine.submit_prompt(session.id, args.prompt)
        await consumer
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
