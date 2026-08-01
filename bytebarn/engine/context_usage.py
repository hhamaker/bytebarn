"""Estimate what is filling the model context window.

Produces structured buckets so the UI / ``/context`` can show a breakdown
rather than a single token count. Estimates use character length ≈ tokens/4
when the provider did not return per-section usage; last-turn token totals
anchor the scale when available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ContextBucket:
    name: str
    chars: int = 0
    tokens_est: int = 0
    detail: str = ""


@dataclass
class ContextBreakdown:
    model: str = ""
    context_window: int = 0
    last_turn_tokens: int = 0
    estimated_total: int = 0
    remaining: int = 0
    buckets: list[ContextBucket] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "context_window": self.context_window,
            "last_turn_tokens": self.last_turn_tokens,
            "estimated_total": self.estimated_total,
            "remaining": self.remaining,
            "buckets": [asdict(b) for b in self.buckets],
        }

    def format_text(self) -> str:
        lines = [
            f"Context usage for {self.model or '(default model)'}",
            f"Window: {self.context_window:,} tokens",
            f"Last turn input: {self.last_turn_tokens:,} tokens",
            f"Estimated total: {self.estimated_total:,} tokens"
            + (f" ({self.remaining:,} remaining)" if self.context_window else ""),
            "",
            "Breakdown (estimated):",
        ]
        for b in self.buckets:
            pct = (
                f" ({100 * b.tokens_est / self.estimated_total:.0f}%)"
                if self.estimated_total else ""
            )
            lines.append(f"  • {b.name}: ~{b.tokens_est:,} tokens{pct}"
                         + (f" — {b.detail}" if b.detail else ""))
        return "\n".join(lines)


def _est_tokens(text: str) -> int:
    # rough but stable: ~4 chars per token for code/English mix
    n = len(text or "")
    return max(0, (n + 3) // 4)


def build_breakdown(
    *,
    system: str = "",
    history_text: str = "",
    tools_text: str = "",
    skills_text: str = "",
    memory_text: str = "",
    instructions_text: str = "",
    model: str = "",
    context_window: int = 0,
    last_turn_tokens: int = 0,
) -> ContextBreakdown:
    buckets = [
        ContextBucket("system", len(system), _est_tokens(system), "agent prompt + env"),
        ContextBucket("instructions", len(instructions_text), _est_tokens(instructions_text),
                      "AGENTS.md / CLAUDE.md / project notes"),
        ContextBucket("skills", len(skills_text), _est_tokens(skills_text), "skill catalog"),
        ContextBucket("memory", len(memory_text), _est_tokens(memory_text), "project memory"),
        ContextBucket("tools", len(tools_text), _est_tokens(tools_text), "tool schemas"),
        ContextBucket("history", len(history_text), _est_tokens(history_text), "messages"),
    ]
    estimated = sum(b.tokens_est for b in buckets)
    # If we have a real last-turn total, scale estimates to match when close
    if last_turn_tokens > 0 and estimated > 0:
        scale = last_turn_tokens / estimated
        if 0.25 <= scale <= 4.0:
            for b in buckets:
                b.tokens_est = max(0, int(b.tokens_est * scale))
            estimated = sum(b.tokens_est for b in buckets)
    remaining = max(0, context_window - (last_turn_tokens or estimated)) if context_window else 0
    return ContextBreakdown(
        model=model,
        context_window=context_window,
        last_turn_tokens=last_turn_tokens,
        estimated_total=last_turn_tokens or estimated,
        remaining=remaining,
        buckets=buckets,
    )
