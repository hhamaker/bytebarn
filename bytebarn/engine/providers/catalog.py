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
    # Anthropic (current generation)
    "claude-fable-5": ModelInfo(1_000_000, 128_000, True, 10.0, 50.0),
    "claude-opus-4-8": ModelInfo(1_000_000, 128_000, True, 5.0, 25.0),
    "claude-opus-4-7": ModelInfo(1_000_000, 128_000, True, 5.0, 25.0),
    "claude-sonnet-5": ModelInfo(1_000_000, 128_000, True, 3.0, 15.0),
    "claude-sonnet-4-6": ModelInfo(1_000_000, 128_000, True, 3.0, 15.0),
    # Anthropic (legacy, still served)
    "claude-sonnet-4-5": ModelInfo(200_000, 64_000, True, 3.0, 15.0),
    "claude-haiku-4-5": ModelInfo(200_000, 64_000, True, 1.0, 5.0),
    "claude-opus-4-1": ModelInfo(200_000, 32_000, True, 15.0, 75.0),
    "claude-sonnet-latest": ModelInfo(200_000, 64_000, True, 3.0, 15.0),
    "claude-haiku-latest": ModelInfo(200_000, 64_000, True, 1.0, 5.0),
    # OpenAI (gpt-5.6 family, Jul 2026; alias gpt-5.6 routes to -sol)
    "gpt-5.6": ModelInfo(1_050_000, 128_000, True, 5.0, 30.0),
    "gpt-5.6-sol": ModelInfo(1_050_000, 128_000, True, 5.0, 30.0),
    "gpt-5.6-terra": ModelInfo(1_050_000, 128_000, True, 2.5, 15.0),
    "gpt-5.6-luna": ModelInfo(1_050_000, 128_000, True, 1.0, 6.0),
    # OpenAI (legacy)
    "gpt-4o": ModelInfo(128_000, 16_384, True, 2.5, 10.0),
    "gpt-4o-mini": ModelInfo(128_000, 16_384, True, 0.15, 0.6),
    "gpt-4.1": ModelInfo(1_000_000, 32_768, True, 2.0, 8.0),
    "o3": ModelInfo(200_000, 100_000, True, 2.0, 8.0),
    # xAI (Grok, Jul 2026)
    "grok-4.5": ModelInfo(500_000, 64_000, True, 2.0, 6.0),
    "grok-4.3": ModelInfo(1_000_000, 64_000, True, 1.25, 2.5),
    "grok-4.20": ModelInfo(2_000_000, 64_000, True, 1.0, 3.0),
    "grok-4.1-fast": ModelInfo(2_000_000, 64_000, True, 0.2, 0.5),
    "grok-build-0.1": ModelInfo(256_000, 64_000, True, 0.2, 1.5),
    # xAI (retired ids redirect to grok-4.3)
    "grok-4": ModelInfo(256_000, 64_000, True, 3.0, 15.0),
    "grok-4-fast": ModelInfo(2_000_000, 64_000, True, 0.2, 0.5),
    "grok-4-fast-reasoning": ModelInfo(2_000_000, 64_000, True, 0.2, 0.5),
    "grok-4-fast-non-reasoning": ModelInfo(2_000_000, 64_000, True, 0.2, 0.5),
    "grok-code-fast-1": ModelInfo(256_000, 64_000, True, 0.2, 1.5),
    "grok-3": ModelInfo(131_072, 8192, True, 3.0, 15.0),
    "grok-3-mini": ModelInfo(131_072, 8192, True, 0.3, 0.5),
    # Groq
    "llama-3.3-70b-versatile": ModelInfo(131_072, 32_768, True, 0.59, 0.79),
    "llama-3.1-8b-instant": ModelInfo(131_072, 8192, True, 0.05, 0.08),
    "openai/gpt-oss-120b": ModelInfo(131_072, 32_768, True, 0.15, 0.75),
    "openai/gpt-oss-20b": ModelInfo(131_072, 32_768, True, 0.10, 0.50),
    "moonshotai/kimi-k2-instruct": ModelInfo(131_072, 16_384, True, 1.0, 3.0),  # deprecated on Groq
    "qwen/qwen3-32b": ModelInfo(131_072, 16_384, True, 0.29, 0.59),  # deprecated on Groq
    # Google (Gemini, via OpenAI-compatible endpoint; 3.x prices approximate)
    "gemini-3.5-flash": ModelInfo(1_000_000, 65_536, True, 0.30, 2.5),
    "gemini-3.1-pro-preview": ModelInfo(1_000_000, 65_536, True, 2.0, 12.0),
    "gemini-2.5-pro": ModelInfo(1_000_000, 65_536, True, 1.25, 10.0),
    "gemini-2.5-flash": ModelInfo(1_000_000, 65_536, True, 0.30, 2.5),
    # Mistral
    "mistral-large-latest": ModelInfo(131_072, 32_768, True, 2.0, 6.0),
    "codestral-latest": ModelInfo(256_000, 32_768, True, 0.3, 0.9),
    "devstral-medium-latest": ModelInfo(131_072, 32_768, True, 0.4, 2.0),
    # DeepSeek (V4; old aliases retire 2026-07-24)
    "deepseek-v4-flash": ModelInfo(1_000_000, 131_072, True, 0.14, 0.28),
    "deepseek-v4-pro": ModelInfo(1_000_000, 131_072, True, 0.435, 0.87),
    "deepseek-chat": ModelInfo(64_000, 8192, True, 0.27, 1.1),
    "deepseek-reasoner": ModelInfo(64_000, 65_536, True, 0.55, 2.19),
    # Z.ai (GLM) — 200k context, strong tool use; prices approximate
    "glm-5.1": ModelInfo(200_000, 65_536, True, 0.6, 2.2),
    "glm-5-turbo": ModelInfo(200_000, 65_536, True, 0.2, 0.6),
    "glm-4.7": ModelInfo(200_000, 65_536, True, 0.4, 1.6),
    "glm-4.7-flash": ModelInfo(128_000, 32_768, True, 0.0, 0.0),  # free tier
    # Moonshot AI (Kimi K2; 256k context on K2, prices approximate)
    "kimi-k2-thinking": ModelInfo(262_144, 16_384, True, 0.6, 2.5),
    "kimi-k2-turbo-preview": ModelInfo(262_144, 16_384, True, 1.15, 8.0),
    "kimi-k2-0905-preview": ModelInfo(262_144, 16_384, True, 0.6, 2.5),
    "kimi-latest": ModelInfo(131_072, 16_384, True, 0.6, 2.5),
    "moonshot-v1-128k": ModelInfo(131_072, 16_384, True, 2.0, 5.0),
    # Together
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": ModelInfo(131_072, 8192, True, 0.88, 0.88),
    "Qwen/Qwen2.5-Coder-32B-Instruct": ModelInfo(32_768, 8192, True, 0.8, 0.8),
    # Cerebras
    "llama-3.3-70b": ModelInfo(65_536, 8192, True, 0.85, 1.2),
    "qwen-3-32b": ModelInfo(65_536, 8192, True, 0.4, 0.8),
    # AWS Bedrock (Claude; same list prices as first-party)
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": ModelInfo(200_000, 64_000, True, 3.0, 15.0),
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": ModelInfo(200_000, 64_000, True, 1.0, 5.0),
    "us.anthropic.claude-opus-4-1-20250805-v1:0": ModelInfo(200_000, 32_000, True, 15.0, 75.0),
    # Cloudflare Workers AI
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": ModelInfo(131_072, 8192, True, 0.29, 2.25),
    "@cf/qwen/qwen2.5-coder-32b-instruct": ModelInfo(32_768, 8192, True, 0.66, 1.0),
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": ModelInfo(80_000, 8192, True, 0.5, 4.88),
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
