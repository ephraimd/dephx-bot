import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx2
import pytest

from praxium.model import Message, ModelError, ModelReply, TextDelta, ToolCall, Usage
from praxium.providers import PROVIDERS, ClientPool, OpenAICompatibleClient

pytestmark = pytest.mark.anyio

SPEC = PROVIDERS["praxiom"]


def chunk(delta: dict[str, Any] | None = None, usage: dict[str, Any] | None = None) -> str:
    body: dict[str, Any] = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "praxiom",
        "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    if usage is not None:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


def sse(*frames: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=("".join(frames) + "data: [DONE]\n\n").encode(),
    )


class Transport(httpx2.AsyncBaseTransport):
    """Answers each request with the next canned response and records the request bodies."""

    def __init__(self, *responses: httpx2.Response | Callable[[], httpx2.Response]) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    @property
    def calls(self) -> int:
        return len(self.bodies)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        response = self.responses.pop(0)
        return response() if callable(response) else response


def client_for(transport: Transport, spec: Any = SPEC) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(spec, "px_test", http_client=httpx2.AsyncClient(transport=transport))


async def collect(client: OpenAICompatibleClient, messages: list[Message]) -> list[TextDelta | ModelReply]:
    return [item async for item in client.stream(messages, [], 100)]


USAGE = {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}


async def test_streams_text_then_reply() -> None:
    transport = Transport(sse(chunk({"content": "Hel"}), chunk({"content": "lo"}), chunk(usage=USAGE)))
    items = await collect(client_for(transport), [Message("user", "hi")])

    assert [i.text for i in items if isinstance(i, TextDelta)] == ["Hel", "lo"]
    assert items[-1] == ModelReply("Hello", [], Usage(12, 2, None, estimated=False))


async def test_request_asks_for_streamed_usage_and_caps_output() -> None:
    transport = Transport(sse(chunk({"content": "ok"}), chunk(usage=USAGE)))
    await collect(client_for(transport), [Message("user", "hi")])

    body = transport.bodies[0]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 100
    assert body["model"] == "praxiom"


async def test_reassembles_streamed_tool_call() -> None:
    transport = Transport(
        sse(
            chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                   "function": {"name": "read_file", "arguments": ""}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'th": '}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"a.py"}'}}]}),
            chunk(usage=USAGE),
        )
    )
    items = await collect(client_for(transport), [Message("user", "read a.py")])

    reply = items[-1]
    assert isinstance(reply, ModelReply)
    assert reply.tool_calls == [ToolCall("call_1", "read_file", '{"path": "a.py"}')]


async def test_cached_tokens_are_reported_when_present() -> None:
    usage = {**USAGE, "prompt_tokens_details": {"cached_tokens": 8}}
    transport = Transport(sse(chunk({"content": "ok"}), chunk(usage=usage)))
    reply = (await collect(client_for(transport), [Message("user", "hi")]))[-1]

    assert isinstance(reply, ModelReply)
    assert reply.usage.cached_tokens == 8


async def test_missing_usage_is_estimated() -> None:
    transport = Transport(sse(chunk({"content": "Hello"})))
    reply = (await collect(client_for(transport), [Message("user", "hi")]))[-1]

    assert isinstance(reply, ModelReply)
    assert reply.usage.estimated is True
    assert reply.usage.output_tokens == 2


async def test_sends_tool_history_in_openai_shape() -> None:
    transport = Transport(sse(chunk({"content": "done"}), chunk(usage=USAGE)))
    history = [
        Message("user", "read a.py"),
        Message("assistant", "", tool_calls=[ToolCall("call_1", "read_file", '{"path": "a.py"}')]),
        Message("tool", "print(1)", tool_call_id="call_1"),
    ]
    await collect(client_for(transport), history)

    assert transport.bodies[0]["messages"] == [
        {"role": "user", "content": "read a.py"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "content": "print(1)", "tool_call_id": "call_1"},
    ]


async def test_retries_429_before_stream_using_retry_after() -> None:
    busy = httpx2.Response(
        429,
        headers={"retry-after": "0"},
        json={"error": {"code": "concurrency_limit", "message": "busy"}},
    )
    transport = Transport(busy, sse(chunk({"content": "ok"}), chunk(usage=USAGE)))
    started = time.monotonic()
    items = await collect(client_for(transport), [Message("user", "hi")])

    assert transport.calls == 2
    assert isinstance(items[-1], ModelReply)
    assert time.monotonic() - started < 0.5  # Retry-After: 0 means retry now


async def test_gives_up_after_two_retries() -> None:
    def busy() -> httpx2.Response:
        return httpx2.Response(503, headers={"retry-after": "0"},
                              json={"error": {"code": "model_unavailable", "message": "down"}})

    transport = Transport(busy, busy, busy)
    with pytest.raises(ModelError) as error:
        await collect(client_for(transport), [Message("user", "hi")])

    assert error.value.code == "model_unavailable"
    assert transport.calls == 3


async def test_does_not_retry_409_and_maps_code() -> None:
    transport = Transport(
        httpx2.Response(409, json={"error": {"code": "idempotency_replay", "message": "used"}})
    )
    with pytest.raises(ModelError) as error:
        await collect(client_for(transport), [Message("user", "hi")])

    assert error.value.code == "idempotency_replay"
    assert error.value.status == 409
    assert transport.calls == 1


async def test_concurrency_gate_holds_until_stream_finishes() -> None:
    spec = replace(SPEC, max_concurrency=1)
    transport = Transport(
        sse(chunk({"content": "a"}), chunk(usage=USAGE)),
        sse(chunk({"content": "b"}), chunk(usage=USAGE)),
    )
    client = client_for(transport, spec)
    first = client.stream([Message("user", "1")], [], 100)
    await anext(first)

    second = asyncio.ensure_future(collect(client, [Message("user", "2")]))
    await asyncio.sleep(0.05)
    assert transport.calls == 1

    async for _ in first:
        pass
    await second
    assert transport.calls == 2


def test_client_pool_reuses_one_client_per_provider_and_key() -> None:
    pool = ClientPool()
    assert pool.get(SPEC, "k1") is pool.get(SPEC, "k1")
    assert pool.get(SPEC, "k1") is not pool.get(SPEC, "k2")


def test_praxiom_contract_values() -> None:
    praxiom = PROVIDERS["praxiom"]
    assert (praxiom.history_budget, praxiom.min_token_limit(), praxiom.max_concurrency) == (54_344, 65_536, 2)
    assert PROVIDERS["deepseek"].min_token_limit() == 128_192


def test_cost_uses_published_prices_or_none() -> None:
    assert PROVIDERS["praxiom"].cost(Usage(3_000, 100, None, False)) == pytest.approx(0.0009381)
    assert PROVIDERS["deepseek"].cost(Usage(3_000, 100, None, False)) is None
