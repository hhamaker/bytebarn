import pytest

from crew.engine.config import Config
from crew.engine.providers.base import (
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
from crew.engine.providers.catalog import cost_of, model_info
from crew.engine.providers.fake import FakeProvider, text_turn, tool_turn
from crew.engine.providers.registry import ProviderRegistry


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
