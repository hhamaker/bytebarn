"""Static model catalog (spec §5.2): id -> limits, capabilities, cost.

User-extensible: config may carry a "catalog" object merged over this.
Costs are USD per million tokens (input, output).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelInfo:
    context_window: int = 200_000
    max_output: int = 8192
    supports_tools: bool = True
    cost_in: float = 0.0
    cost_out: float = 0.0


CATALOG: dict[str, ModelInfo] = {
    # Anthropic
    "claude-sonnet-4-5": ModelInfo(200_000, 64_000, True, 3.0, 15.0),
    "claude-haiku-4-5": ModelInfo(200_000, 64_000, True, 1.0, 5.0),
    "claude-opus-4-1": ModelInfo(200_000, 32_000, True, 15.0, 75.0),
    "claude-sonnet-latest": ModelInfo(200_000, 64_000, True, 3.0, 15.0),
    "claude-haiku-latest": ModelInfo(200_000, 64_000, True, 1.0, 5.0),
    # OpenAI
    "gpt-4o": ModelInfo(128_000, 16_384, True, 2.5, 10.0),
    "gpt-4o-mini": ModelInfo(128_000, 16_384, True, 0.15, 0.6),
    "gpt-4.1": ModelInfo(1_000_000, 32_768, True, 2.0, 8.0),
    "o3": ModelInfo(200_000, 100_000, True, 2.0, 8.0),
}

_FALLBACK = ModelInfo()


def model_info(model_id: str, extra: dict[str, ModelInfo] | None = None) -> ModelInfo:
    if extra and model_id in extra:
        return extra[model_id]
    return CATALOG.get(model_id, _FALLBACK)


def catalog_from_config(raw: dict | None) -> dict[str, ModelInfo]:
    """Parse a config "catalog" object into ModelInfo entries."""
    out: dict[str, ModelInfo] = {}
    for model_id, spec in (raw or {}).items():
        out[model_id] = ModelInfo(
            context_window=spec.get("context_window", 200_000),
            max_output=spec.get("max_output", 8192),
            supports_tools=spec.get("supports_tools", True),
            cost_in=spec.get("cost_in", 0.0),
            cost_out=spec.get("cost_out", 0.0),
        )
    return out


def cost_of(model_id: str, tokens_in: int, tokens_out: int, extra: dict[str, ModelInfo] | None = None) -> float:
    info = model_info(model_id, extra)
    return (tokens_in * info.cost_in + tokens_out * info.cost_out) / 1_000_000
