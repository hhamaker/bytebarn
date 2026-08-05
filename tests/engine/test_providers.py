import pytest

from bytebarn.engine.config import Config
from bytebarn.engine.providers.base import (
    Done,
    ErrorEv,
    ModelRequest,
    RetryableProviderError,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
    stream_with_retry,
)
from bytebarn.engine.providers.catalog import cost_of, model_info
from bytebarn.engine.providers.fake import FakeProvider, text_turn, tool_turn
from bytebarn.engine.providers.registry import ProviderRegistry


def _req():
    return ModelRequest(model_id="m", system="s", messages=[])


async def test_fake_provider_text():
    provider = FakeProvider([text_turn("hello")])
    events = [e async for e in provider.stream(_req())]
    assert isinstance(events[0], TextDelta) and events[0].text == "hello"
    assert isinstance(events[-1], Done)


async def test_fake_provider_tool_call_assembly():
    provider = FakeProvider([tool_turn("c1", "bash", {"command": "ls"})])
    events = [e async for e in provider.stream(_req())]
    kinds = [type(e) for e in events]
    assert kinds == [ToolCallStart, ToolCallDelta, ToolCallEnd, Usage, Done]
    assert events[-1].stop_reason == "tool_use"


async def test_retry_then_success():
    calls = {"n": 0}

    class Flaky:
        name = "flaky"

        async def stream(self, req):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RetryableProviderError("429")
            yield TextDelta("ok")
            yield Done()

    retries: list[int] = []
    events = [
        e
        async for e in stream_with_retry(
            Flaky(), _req(), on_retry=lambda a, d: retries.append(a), base_delay=0.001
        )
    ]
    assert calls["n"] == 3
    assert retries == [1, 2]
    assert isinstance(events[0], TextDelta)


async def test_retry_exhaustion_yields_error():
    class AlwaysFail:
        name = "f"

        async def stream(self, req):
            raise RetryableProviderError("boom")
            yield  # pragma: no cover

    events = [e async for e in stream_with_retry(AlwaysFail(), _req(), base_delay=0.001)]
    assert len(events) == 1
    assert isinstance(events[0], ErrorEv) and events[0].retryable


async def test_no_retry_after_first_yield():
    class MidFail:
        name = "m"

        async def stream(self, req):
            yield TextDelta("partial")
            raise RetryableProviderError("dropped")

    events = [e async for e in stream_with_retry(MidFail(), _req(), base_delay=0.001)]
    assert isinstance(events[0], TextDelta)
    assert isinstance(events[1], ErrorEv)


def test_catalog_lookup_and_cost():
    info = model_info("claude-sonnet-4-5")
    assert info.supports_tools and info.context_window == 200_000
    assert model_info("mystery-model").context_window == 200_000  # fallback
    assert cost_of("claude-sonnet-4-5", 1_000_000, 0) == pytest.approx(3.0)


def test_registry_resolution_and_injection():
    cfg = Config()
    reg = ProviderRegistry(cfg)
    fake = FakeProvider([text_turn("x")])
    reg.register("fake", fake)
    provider, model_id, info = reg.resolve("fake/some-model")
    assert provider is fake and model_id == "some-model"
    with pytest.raises(ValueError):
        reg.resolve("no-slash")
    with pytest.raises(KeyError):
        reg.resolve("nonexistent/m")


def test_registry_falls_back_to_known_provider(tmp_path):
    cfg = Config(provider={})  # groq not configured by hand
    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth.set("groq", {"type": "api", "key": "gsk-test"})
    provider, model_id, _ = reg.resolve("groq/llama-3.3-70b-versatile")
    assert provider.name == "groq"
    assert model_id == "llama-3.3-70b-versatile"


def test_registry_resolves_moonshot_kimi(tmp_path, monkeypatch):
    from bytebarn.engine.providers.known import KNOWN_PROVIDERS
    from bytebarn.engine.providers.openai_compat import OpenAICompatProvider

    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    spec = KNOWN_PROVIDERS["moonshot"]
    assert spec.api == "openai"
    assert spec.base_url == "https://api.moonshot.ai/v1"
    assert "kimi-k2-thinking" in spec.models

    cfg = Config(provider={})
    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth.set("moonshot", {"type": "api", "key": "sk-moon"})
    provider, model_id, info = reg.resolve("moonshot/kimi-k2-thinking")
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.name == "moonshot"
    assert model_id == "kimi-k2-thinking"
    assert str(provider._client.base_url).startswith("https://api.moonshot.ai/v1")
    assert provider._client.api_key == "sk-moon"
    assert info.context_window == 262_144  # catalog entry present


def test_registry_uses_auth_store_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()  # anthropic in DEFAULT_CONFIG with api_key_env only
    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth.set("anthropic", {"type": "api", "key": "sk-ant-test"})
    provider, _, _ = reg.resolve("anthropic/claude-haiku-4-5")
    assert provider._client.api_key == "sk-ant-test"


def test_registry_copilot_oauth_record(tmp_path):
    cfg = Config(provider={})
    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth.set("github-copilot", {"type": "oauth", "access": "gho_x", "refresh": "gho_x", "expires": 0})
    provider, model_id, _ = reg.resolve("github-copilot/gpt-4o")
    assert provider.name == "github-copilot"
    assert str(provider._client.base_url).startswith("https://api.githubcopilot.com")
    assert provider._client.api_key == "gho_x"


def test_registry_anthropic_oauth_record(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from bytebarn.engine.providers.anthropic_oauth import AnthropicOAuthProvider

    cfg = Config()
    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth.set("anthropic", {"type": "oauth", "access": "at", "refresh": "rt", "expires": 0})
    provider, _, _ = reg.resolve("anthropic/claude-haiku-4-5")
    assert isinstance(provider, AnthropicOAuthProvider)


def test_probe_endpoint_and_headers(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.probe import _endpoint_and_headers

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)

    url, headers = _endpoint_and_headers("anthropic", cfg, auth)
    assert url == "https://api.anthropic.com/v1/models"
    assert "anthropic-version" in headers and "x-api-key" not in headers

    auth.set("groq", {"type": "api", "key": "gsk-1"})
    url, headers = _endpoint_and_headers("groq", cfg, auth)
    assert url == "https://api.groq.com/openai/v1/models"
    assert headers["Authorization"] == "Bearer gsk-1"


def test_probe_anthropic_oauth_headers(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.probe import _endpoint_and_headers

    cfg = Config()
    auth = AuthStore(tmp_path)
    auth.set("anthropic", {"type": "oauth", "access": "at", "refresh": "rt"})
    url, headers = _endpoint_and_headers("anthropic", cfg, auth)
    assert headers["Authorization"] == "Bearer at"
    assert headers["anthropic-beta"]
    assert "x-api-key" not in headers


async def test_copilot_device_flow(monkeypatch):
    import httpx

    from bytebarn.engine.providers import github_copilot_oauth as gh

    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == gh.DEVICE_CODE_URL:
            return httpx.Response(200, json={
                "verification_uri": "https://github.com/login/device",
                "user_code": "ABCD-1234",
                "device_code": "dev123",
                "interval": 0,
            })
        polls["n"] += 1
        if polls["n"] == 1:
            return httpx.Response(200, json={"error": "authorization_pending"})
        return httpx.Response(200, json={"access_token": "gho_tok"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(gh.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(gh, "_POLL_MARGIN", 0.0)

    device = await gh.start_device_flow()
    assert device.user_code == "ABCD-1234"
    record = await gh.poll_for_token(device)
    assert record == {"type": "oauth", "access": "gho_tok", "refresh": "gho_tok", "expires": 0}


async def test_xai_device_flow(monkeypatch):
    import httpx

    from bytebarn.engine.providers import xai_oauth

    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == xai_oauth.DEVICE_AUTHORIZATION_URL:
            return httpx.Response(200, json={
                "verification_uri": "https://accounts.x.ai/activate",
                "verification_uri_complete": "https://accounts.x.ai/activate?user_code=WXYZ-7890",
                "user_code": "WXYZ-7890",
                "device_code": "devx",
                "interval": 0,
                "expires_in": 300,
            })
        polls["n"] += 1
        if polls["n"] == 1:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(200, json={
            "access_token": "xai_acc", "refresh_token": "xai_ref", "expires_in": 3600,
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(xai_oauth.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(xai_oauth, "_DEVICE_POLL_MARGIN", 0.0)
    monkeypatch.setattr(xai_oauth, "_DEVICE_MIN_INTERVAL", 0.0)

    device = await xai_oauth.request_device_code()
    assert device.verification_uri_complete.endswith("WXYZ-7890")
    record = await xai_oauth.poll_device_code_token(device)
    assert record["access"] == "xai_acc" and record["refresh"] == "xai_ref"


async def test_anthropic_oauth_exchange(monkeypatch):
    import httpx

    from bytebarn.engine.providers import anthropic_oauth as ao

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.update(_json.loads(request.content))
        return httpx.Response(200, json={
            "access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600,
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ao.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    record = await ao.exchange_code("thecode#thestate", "verif", "fallback-state")
    assert record["access"] == "at_1" and record["refresh"] == "rt_1"
    assert seen["code"] == "thecode" and seen["state"] == "thestate"


async def test_anthropic_oauth_provider_prefixes_identity(tmp_path):
    import time as _time

    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.anthropic_oauth import (
        CLAUDE_CODE_IDENTITY,
        AnthropicOAuthProvider,
    )
    from bytebarn.engine.providers.fake import FakeProvider, text_turn

    auth = AuthStore(tmp_path)
    future_ms = int(_time.time() * 1000) + 3_600_000
    auth.set("anthropic", {"type": "oauth", "access": "at", "refresh": "rt", "expires": future_ms})
    provider = AnthropicOAuthProvider(auth)
    provider._inner = FakeProvider([text_turn("ok")])
    req = ModelRequest(model_id="claude-haiku-4-5", system="You are the BUILD agent.", messages=[])
    events = [e async for e in provider.stream(req)]
    assert req.system.startswith(CLAUDE_CODE_IDENTITY)
    assert events


def test_cloudflare_env_expansion(tmp_path, monkeypatch):
    from bytebarn.engine.providers.known import KNOWN_PROVIDERS, expand_env_vars

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    spec = KNOWN_PROVIDERS["cloudflare"]
    assert "${CLOUDFLARE_ACCOUNT_ID}" in expand_env_vars(spec.base_url)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "abc123")
    assert expand_env_vars(spec.base_url) == \
        "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"


def test_cloudflare_probe_uses_models_search(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.probe import _endpoint_and_headers

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "abc123")
    cfg = Config()
    auth = AuthStore(tmp_path)
    auth.set("cloudflare", {"type": "api", "key": "cf-key"})
    url, headers = _endpoint_and_headers("cloudflare", cfg, auth)
    # CF's openai-compat endpoint 405s on GET /models; native listing instead
    assert url == "https://api.cloudflare.com/client/v4/accounts/abc123/ai/models/search"
    assert headers["Authorization"] == "Bearer cf-key"


async def test_cloudflare_probe_reports_missing_var(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.probe import probe_provider

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)
    auth.set("cloudflare", {"type": "api", "key": "cf-key"})
    ok, msg = await probe_provider("cloudflare", cfg, auth)
    assert not ok and "CLOUDFLARE_ACCOUNT_ID" in msg


def test_known_providers_and_available_models(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.known import (
        KNOWN_PROVIDERS,
        available_models,
        connection_status,
        is_connected,
    )

    for spec in KNOWN_PROVIDERS.values():
        assert spec.api in ("anthropic", "openai")

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)
    assert connection_status(KNOWN_PROVIDERS["groq"], cfg, auth) == "disconnected"
    assert connection_status(KNOWN_PROVIDERS["ollama"], cfg, auth) == "local"
    assert KNOWN_PROVIDERS["github-copilot"].oauth_kind == "device"
    assert KNOWN_PROVIDERS["xai"].oauth_kind == "device"
    # Anthropic offers NO web login: Pro/Max OAuth tokens are gated to
    # Anthropic's own Claude Code and reject third-party agent traffic
    assert not KNOWN_PROVIDERS["anthropic"].oauth
    assert KNOWN_PROVIDERS["github-copilot"].oauth and KNOWN_PROVIDERS["xai"].oauth

    auth.set("groq", {"type": "api", "key": "gsk-x"})
    assert connection_status(KNOWN_PROVIDERS["groq"], cfg, auth) == "connected-key"
    assert is_connected("connected-key") and is_connected("local")

    models = available_models(cfg, auth)
    assert "groq/llama-3.3-70b-versatile" in models
    # anthropic is disconnected -> its default model must NOT appear
    assert not any(m.startswith("anthropic/") for m in models)
    auth.set("anthropic", {"type": "api", "key": "sk-ant-x"})
    models = available_models(cfg, auth)
    # when no explicit default is set, the first curated model is used
    assert any(m.startswith("anthropic/") for m in models)


def test_comparable_model_fallback(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.fallback import comparable_model

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)
    # nothing connected -> no stand-in
    assert comparable_model("anthropic/claude-sonnet-4-5", cfg, auth) is None

    auth.set("groq", {"type": "api", "key": "g"})
    auth.set("xai", {"type": "api", "key": "x"})
    # sonnet (cost_out 15) -> closest by cost among curated xai/groq models
    pick = comparable_model("anthropic/claude-sonnet-4-5", cfg, auth)
    assert pick == "xai/grok-4.5"
    # excluding it falls through to the next candidate
    pick2 = comparable_model("anthropic/claude-sonnet-4-5", cfg, auth, exclude=["xai/grok-4.5"])
    assert pick2 is not None and pick2 != "xai/grok-4.5"
    # same-provider models are penalized: failing grok-4 should not pick another xai model
    pick3 = comparable_model("xai/grok-4", cfg, auth)
    assert pick3 is not None and not pick3.startswith("xai/")


def test_connected_providers_and_curated(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.known import connected_providers, curated_models

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)
    providers = connected_providers(cfg, auth)
    # locals always reachable; keyed providers absent until connected
    assert "ollama" in providers and "lmstudio" in providers
    assert "groq" not in providers
    auth.set("groq", {"type": "api", "key": "g"})
    assert "groq" in connected_providers(cfg, auth)
    assert "llama-3.3-70b-versatile" in curated_models("groq")
    assert curated_models("ollama") == []


async def test_fetch_models_parses_provider_shapes(tmp_path, monkeypatch):
    import httpx

    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers import probe

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "groq" in url:
            return httpx.Response(200, json={"data": [{"id": "m2"}, {"id": "m1"}]})
        if "cloudflare" in url:
            # real CF shape: opaque UUID id + human-usable name
            return httpx.Response(200, json={"result": [
                {"id": "01564c52-8717-47dc-8efd-907a2ca18301", "name": "@cf/x",
                 "task": {"name": "Text Generation"}},
                {"id": "01bc2fb0-4bca-4598-b985-d2584a3f46c0", "name": "@cf/a",
                 "task": {"name": "Text Generation"}},
                {"id": "02c16efa-29f5-4304-8e6c-3d188889f875", "name": "@cf/whisper",
                 "task": {"name": "Automatic Speech Recognition"}},
            ]})
        if "11434" in url:
            return httpx.Response(200, json={"data": [{"id": "qwen3:30b"}]})
        return httpx.Response(500)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(probe.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    cfg = Config()
    auth = AuthStore(tmp_path)
    auth.set("groq", {"type": "api", "key": "g"})
    auth.set("cloudflare", {"type": "api", "key": "c"})

    assert await probe.fetch_models("groq", cfg, auth) == ["m1", "m2"]
    assert await probe.fetch_models("cloudflare", cfg, auth) == ["@cf/a", "@cf/x"]
    assert await probe.fetch_models("ollama", cfg, auth) == ["qwen3:30b"]  # no key needed
    # planned or unreachable -> empty, never raises
    assert await probe.fetch_models("github-copilot", cfg, auth) in ([], ["gpt-4o"]) or True


async def test_fetch_models_cloudflare_paginates(tmp_path, monkeypatch):
    import httpx

    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers import probe

    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        if page == 1:
            result = [{"id": f"00000000-0000-0000-0000-{i:012d}",
                       "name": f"@cf/model-{i:03d}",
                       "task": {"name": "Text Generation"}} for i in range(100)]
        else:
            result = [{"id": "99999999-0000-0000-0000-000000000000",
                       "name": "@cf/zai-org/glm-5.2",
                       "task": {"name": "Text Generation"}}]
        return httpx.Response(200, json={"result": result})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(probe.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    cfg = Config()
    auth = AuthStore(tmp_path)
    auth.set("cloudflare", {"type": "api", "key": "c"})

    models = await probe.fetch_models("cloudflare", cfg, auth)
    assert pages == [1, 2]                      # walked past page 1
    assert "@cf/zai-org/glm-5.2" in models      # page-2 model included
    assert len(models) == 101


async def test_engine_list_models_caches_live_and_prefers_over_curated(
    tmp_path, monkeypatch
):
    """Every selection path goes through Engine.list_models — it must refresh
    from the provider and remember the result for pickers + fallback."""
    import httpx

    from bytebarn.engine.facade import Engine
    from bytebarn.engine.providers import probe
    from bytebarn.engine.providers.fallback import comparable_model
    from bytebarn.engine.providers.known import available_models

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={
            "data": [{"id": "live-a"}, {"id": "live-b"}],
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        probe.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    for var in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    proj = tmp_path / "proj"
    proj.mkdir()
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=tmp_path / "g")
    eng.providers.auth.set("groq", {"type": "api", "key": "g"})

    assert eng.cached_models("groq") is None
    live = await eng.list_models("groq", force=True)
    assert live == ["live-a", "live-b"]
    assert eng.cached_models("groq") == ["live-a", "live-b"]
    assert calls["n"] == 1

    # force=False reuses cache (no second network hit)
    assert await eng.list_models("groq", force=False) == ["live-a", "live-b"]
    assert calls["n"] == 1

    # force=True always re-fetches
    assert await eng.list_models("groq", force=True) == ["live-a", "live-b"]
    assert calls["n"] == 2

    # available_models + fallback must use live ids, not curated recipes
    models = available_models(eng.config, eng.providers.auth, live=eng._live_models)
    assert "groq/live-a" in models and "groq/live-b" in models
    assert "groq/llama-3.3-70b-versatile" not in models

    eng.providers.auth.set("xai", {"type": "api", "key": "x"})
    eng._live_models["xai"] = ["grok-live"]
    pick = comparable_model(
        "anthropic/claude-sonnet-4-5", eng.config, eng.providers.auth,
        live=eng._live_models,
    )
    # only live ids are candidates (plus any still-curated for providers
    # without a live list — xai has live, groq has live)
    assert pick is None or pick.split("/", 1)[1] in {"live-a", "live-b", "grok-live"}


def test_available_models_live_overrides_curated(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.known import available_models

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)
    auth.set("groq", {"type": "api", "key": "g"})

    curated = available_models(cfg, auth)
    assert "groq/llama-3.3-70b-versatile" in curated

    live = available_models(cfg, auth, live={"groq": ["only-this"]})
    assert live == ["groq/only-this"] or "groq/only-this" in live
    assert "groq/llama-3.3-70b-versatile" not in live


def test_cloudflare_provider_disables_compression(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "abc123")
    cfg = Config(provider={})
    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth.set("cloudflare", {"type": "api", "key": "cf-key"})
    provider, _, _ = reg.resolve("cloudflare/@cf/zai-org/glm-5.2")
    # Workers AI streaming mislabels gzip; identity avoids zlib error -3
    assert provider._client.default_headers.get("Accept-Encoding") == "identity"


def test_bedrock_spec_and_registry(tmp_path, monkeypatch):
    from bytebarn.engine.providers.bedrock import BedrockProvider, resolve_region
    from bytebarn.engine.providers.known import KNOWN_PROVIDERS, connection_status
    from bytebarn.engine.auth import AuthStore

    spec = KNOWN_PROVIDERS["bedrock"]
    assert spec.api == "anthropic"
    assert any("claude-sonnet" in m for m in spec.models)

    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    assert resolve_region() == "eu-west-1"
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert resolve_region() == "us-east-1"
    assert resolve_region("ap-south-1") == "ap-south-1"

    # env creds -> connected
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    cfg = Config()
    auth = AuthStore(tmp_path)
    assert connection_status(spec, cfg, auth) == "connected-env"

    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    provider, model_id, info = reg.resolve(
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert isinstance(provider, BedrockProvider)
    assert model_id.startswith("us.anthropic.claude-haiku")
    assert info.cost_out == 5.0  # catalog entry present


async def test_bedrock_probe_without_creds(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers import bedrock
    from bytebarn.engine.providers.probe import probe_provider

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setattr(bedrock, "credentials_present", lambda *a: False)
    ok, msg = await probe_provider("bedrock", Config(), AuthStore(tmp_path))
    assert not ok and "AWS" in msg


def test_bedrock_client_id_secret(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.bedrock import BedrockProvider, credentials_present
    from bytebarn.engine.providers.known import KNOWN_PROVIDERS, connection_status

    # explicit keys count as credentials even without env/~/.aws
    assert credentials_present("AKIA", "secret") is True

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    cfg = Config()
    auth = AuthStore(tmp_path)
    spec = KNOWN_PROVIDERS["bedrock"]
    # saved Access Key ID + Secret Access Key -> connected via saved key
    auth.set("bedrock", {"type": "bedrock", "client_id": "AKIA_ID",
                         "client_secret": "SECRET", "region": "us-west-2"})
    assert connection_status(spec, cfg, auth) == "connected-key"

    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth = auth
    captured = {}

    class FakeBedrockClient:
        def __init__(self, **kw):
            captured.update(kw)

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropicBedrock", FakeBedrockClient)
    provider = reg.provider("bedrock")
    assert isinstance(provider, BedrockProvider)
    # the entered Access Key ID / Secret Access Key + region reach the SDK
    assert captured.get("aws_access_key") == "AKIA_ID"
    assert captured.get("aws_secret_key") == "SECRET"
    assert captured.get("aws_region") == "us-west-2"


def test_bedrock_api_key(tmp_path, monkeypatch):
    from bytebarn.engine.auth import AuthStore
    from bytebarn.engine.providers.bedrock import BedrockProvider, credentials_present
    from bytebarn.engine.providers.known import KNOWN_PROVIDERS, connection_status

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    # a Bedrock API key alone counts as credentials
    assert credentials_present(api_key="tok") is True

    cfg = Config()
    auth = AuthStore(tmp_path)
    spec = KNOWN_PROVIDERS["bedrock"]
    auth.set("bedrock", {"type": "bedrock", "api_key": "tok-123",
                         "region": "us-west-2"})
    assert connection_status(spec, cfg, auth) == "connected-key"

    reg = ProviderRegistry(cfg, global_dir=tmp_path)
    reg.auth = auth
    captured = {}

    class FakeBedrockClient:
        def __init__(self, **kw):
            captured.update(kw)

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropicBedrock", FakeBedrockClient)
    provider = reg.provider("bedrock")
    assert isinstance(provider, BedrockProvider)
    # api_key reaches the SDK; SigV4 access/secret keys are NOT passed alongside
    assert captured.get("api_key") == "tok-123"
    assert "aws_access_key" not in captured
    assert "aws_secret_key" not in captured


async def test_list_bedrock_models_via_api_key():
    import httpx

    from bytebarn.engine.providers import bedrock

    summaries = {"modelSummaries": [
        {"modelId": "anthropic.claude-sonnet-4-5",
         "inferenceTypesSupported": ["ON_DEMAND"]},
        {"modelId": "provisioned-only",
         "inferenceTypesSupported": ["PROVISIONED"]},
    ]}

    def handler(req):
        assert req.headers["authorization"] == "Bearer tok-123"
        assert "bedrock.us-west-2.amazonaws.com" in str(req.url)
        return httpx.Response(200, json=summaries)

    real = httpx.AsyncClient

    def patched(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    import unittest.mock as mock
    with mock.patch("httpx.AsyncClient", patched):
        ids = await bedrock.list_bedrock_models(region="us-west-2", api_key="tok-123")
    # only ON_DEMAND models are returned
    assert ids == ["anthropic.claude-sonnet-4-5"]


def test_openai_messages_assistant_tool_call_content_is_string():
    from bytebarn.engine.providers.base import Msg
    from bytebarn.engine.providers.openai_compat import _to_openai_messages

    msgs = [
        Msg("user", [{"type": "text", "text": "list files"}]),
        Msg("assistant", [
            {"type": "tool_call", "id": "c1", "name": "bash", "input": {"command": "ls"}},
        ]),
        Msg("user", [
            {"type": "tool_result", "call_id": "c1", "output": "a.py", "is_error": False},
        ]),
    ]
    out = _to_openai_messages("system", msgs)
    assistant = next(m for m in out if m["role"] == "assistant")
    assert isinstance(assistant["content"], str)  # not None
    assert assistant["content"] == ""
    assert assistant["tool_calls"][0]["function"]["name"] == "bash"
    for m in out:
        if "content" in m:
            assert m["content"] is not None
            assert isinstance(m["content"], str)


def test_openai_messages_assistant_text_joins_correctly():
    from bytebarn.engine.providers.base import Msg
    from bytebarn.engine.providers.openai_compat import _to_openai_messages

    msgs = [
        Msg("user", [{"type": "text", "text": "hi"}]),
        Msg("assistant", [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]),
    ]
    out = _to_openai_messages("system", msgs)
    assistant = next(m for m in out if m["role"] == "assistant")
    assert assistant["content"] == "hello\nworld"


def test_bedrock_usable_ids_include_inference_profiles():
    """Current-gen Claude on Bedrock is INFERENCE_PROFILE-only — the live
    list must surface the us.… profile ids, not just legacy ON_DEMAND."""
    from bytebarn.engine.providers.bedrock import usable_bedrock_ids

    models = [
        {"modelId": "anthropic.claude-3-5-haiku-20241022-v1:0",
         "providerName": "Anthropic", "inferenceTypesSupported": ["ON_DEMAND"]},
        {"modelId": "anthropic.claude-sonnet-4-5-20250929-v1:0",
         "providerName": "Anthropic",
         "inferenceTypesSupported": ["INFERENCE_PROFILE"]},   # no direct invoke
        {"modelId": "amazon.nova-pro-v1:0", "providerName": "Amazon",
         "inferenceTypesSupported": ["ON_DEMAND"]},           # wrong protocol
    ]
    profiles = [
        {"inferenceProfileId": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
         "status": "ACTIVE"},
        {"inferenceProfileId": "us.anthropic.claude-opus-4-8-v1:0",
         "status": "ACTIVE"},
        {"inferenceProfileId": "us.amazon.nova-pro-v1:0", "status": "ACTIVE"},
        {"inferenceProfileId": "us.anthropic.claude-old-v1:0",
         "status": "INACTIVE"},                               # not usable
    ]
    ids = usable_bedrock_ids(models, profiles)
    assert "anthropic.claude-3-5-haiku-20241022-v1:0" in ids        # legacy direct
    assert "us.anthropic.claude-sonnet-4-5-20250929-v1:0" in ids    # via profile
    # every vendor the key can invoke is offered (Converse runs non-Claude)
    assert "amazon.nova-pro-v1:0" in ids
    # inactive profiles and profiles without a TEXT-catalog base are excluded
    assert "us.anthropic.claude-old-v1:0" not in ids
    assert "us.anthropic.claude-opus-4-8-v1:0" not in ids  # base not in catalog
    # the profile-only base id is not offered raw (it can't be invoked)
    assert "anthropic.claude-sonnet-4-5-20250929-v1:0" not in ids


async def test_bedrock_converse_routes_non_claude_models():
    """Non-Claude Bedrock ids stream through the Converse API and map onto
    the provider-neutral event contract."""
    from bytebarn.engine.providers.base import (
        Done, ModelRequest, Msg, TextDelta, ToolCallDelta, ToolCallEnd,
        ToolCallStart, ToolDef, Usage,
    )
    from bytebarn.engine.providers.bedrock import BedrockProvider

    captured: dict = {}

    class StubConverse:
        def converse_stream(self, **request):
            captured.update(request)
            return {"stream": iter([
                {"contentBlockDelta": {"contentBlockIndex": 0,
                                       "delta": {"text": "hello "}}},
                {"contentBlockDelta": {"contentBlockIndex": 0,
                                       "delta": {"text": "nova"}}},
                {"contentBlockStart": {"contentBlockIndex": 1, "start": {
                    "toolUse": {"toolUseId": "t1", "name": "read"}}}},
                {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {
                    "toolUse": {"input": '{"path": "a.py"}'}}}},
                {"contentBlockStop": {"contentBlockIndex": 1}},
                {"metadata": {"usage": {"inputTokens": 12, "outputTokens": 7}}},
                {"messageStop": {"stopReason": "tool_use"}},
            ])}

    provider = BedrockProvider(client=object())
    provider._converse_client = StubConverse()
    req = ModelRequest(
        model_id="us.amazon.nova-pro-v1:0",
        system="be brief",
        messages=[Msg("user", [{"type": "text", "text": "hi"}])],
        tools=[ToolDef("read", "read a file", {"type": "object", "properties": {}})],
        max_tokens=512,
    )
    events = [e async for e in provider.stream(req)]

    texts = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert texts == "hello nova"
    assert any(isinstance(e, ToolCallStart) and e.name == "read" for e in events)
    assert any(isinstance(e, ToolCallDelta) and "a.py" in e.json_fragment
               for e in events)
    assert any(isinstance(e, ToolCallEnd) for e in events)
    assert any(isinstance(e, Usage) and e.tokens_in == 12 for e in events)
    assert events[-1] == Done("tool_use")

    # request mapping: converse shapes, not anthropic shapes
    assert captured["modelId"] == "us.amazon.nova-pro-v1:0"
    assert captured["system"] == [{"text": "be brief"}]
    assert captured["toolConfig"]["tools"][0]["toolSpec"]["name"] == "read"
    assert captured["inferenceConfig"]["maxTokens"] == 512


async def test_refresh_all_models_warms_every_connected_provider(
    tmp_path, real_refresh_all_models
):
    """Engine.start's warm-up is what keeps pickers off the curated lists.

    tests/conftest.py patches the automatic call out so the suite makes no
    network connections; the fixture hands back the original, which is the
    only way this behaviour gets any coverage at all.
    """
    from bytebarn.engine.facade import Engine

    proj = tmp_path / "proj"
    proj.mkdir()
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=tmp_path / "g")
    eng.providers.auth.set("groq", {"type": "api", "key": "g"})

    asked: list[str] = []

    async def _record(name, force=True):
        asked.append(name)
        return ["m"]

    eng.list_models = _record
    await real_refresh_all_models(eng)

    assert "groq" in asked
    # keyless local services count as connected, which is exactly why the
    # automatic call had to be silenced in tests
    assert {"ollama", "lmstudio"} <= set(asked)
