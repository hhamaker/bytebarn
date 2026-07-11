"""Comparable-model selection for automatic fallback.

When a model keeps failing mid-run, the runner asks for a comparable
replacement among the models the user can actually reach right now.
"Comparable" = closest output-token cost (a rough capability tier proxy),
tool support required, and a different provider preferred — if a model is
failing, its whole provider is often the problem.

Config (global or project, all optional):

    "model_fallback": { "enabled": true, "after": 2 }

``after`` = consecutive failed turns of the current model before switching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from .catalog import model_info
from .known import available_models

if TYPE_CHECKING:
    from ..auth import AuthStore
    from ..config import Config

_UNUSABLE = 1e9


def comparable_model(
    model: str,
    config: "Config",
    auth: "AuthStore",
    exclude: Iterable[str] = (),
) -> str | None:
    """Best available stand-in for ``model``, or None if there is none."""
    if "/" not in model:
        return None
    excluded = set(exclude) | {model}
    candidates = [m for m in available_models(config, auth) if m not in excluded]
    if not candidates:
        return None

    cur_provider, cur_id = model.split("/", 1)
    cur = model_info(cur_id)

    def score(candidate: str) -> float:
        provider, model_id = candidate.split("/", 1)
        info = model_info(model_id)
        if not info.supports_tools:
            return _UNUSABLE
        cost_gap = abs(info.cost_out - cur.cost_out)
        # the failing model's provider is suspect: prefer a different one
        same_provider_penalty = 5.0 if provider == cur_provider else 0.0
        return cost_gap + same_provider_penalty

    best = min(candidates, key=score)
    return best if score(best) < _UNUSABLE else None
