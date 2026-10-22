"""OpenAI-compatibility probe: does a provider behave like the OpenAI Chat Completions API
when driven by the official `openai` Python SDK?

Each probe exercises one feature and reports one of:
    pass  - behaves as the OpenAI API does
    fail  - errors or departs from the OpenAI behaviour
    info  - model-quality observation; there is no single "correct" answer

    PROVIDER=praxiom uv run python compat_probe.py

Results are printed and saved to results/compat-<provider>.json next to this file.
The SDK's automatic retries are switched off so we see the provider's raw behaviour.
"""

import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam

from config import PROJECT_ROOT, Settings, load_settings

Status = Literal["pass", "fail", "info"]

# A tiny, cheap tool every tool probe uses.
WEATHER_TOOL: ChatCompletionToolParam = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
SMALL = 300  # max_tokens for most probes, to keep cost negligible


@dataclass
class ProbeResult:
    name: str
    status: Status
    detail: str
    seconds: float = 0.0


Probe = Callable[[OpenAI, Settings], tuple[Status, str]]


# --- probes ---------------------------------------------------------------------


def models_list(client: OpenAI, s: Settings) -> tuple[Status, str]:
    ids = [m.id for m in client.models.list()]
    return ("pass" if s.model in ids else "fail"), f"models: {ids}"


def basic_chat(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, messages=[{"role": "user", "content": "Say hello in five words."}],
        max_tokens=SMALL,
    )
    c = r.choices[0]
    ok = bool(c.message.content) and c.finish_reason == "stop" and r.usage is not None
    return ("pass" if ok else "fail"), (
        f"finish_reason={c.finish_reason}, usage={r.usage.model_dump() if r.usage else None}, "
        f"content={c.message.content!r}"
    )


def system_prompt_followed(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL,
        messages=[
            {"role": "system", "content": "Reply with exactly one word: PINEAPPLE. Nothing else."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
    )
    content = r.choices[0].message.content or ""
    return "info", f"followed={content.strip().strip('.').upper() == 'PINEAPPLE'}, content={content!r}"


def text_content_parts(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL,
        messages=[{"role": "user", "content": [{"type": "text", "text": "Reply with the word OK."}]}],
    )
    return "pass", f"content={r.choices[0].message.content!r}"


def max_tokens_length(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=5,
        messages=[{"role": "user", "content": "Write a long story about a river."}],
    )
    reason = r.choices[0].finish_reason
    return ("pass" if reason == "length" else "fail"), f"finish_reason={reason}"


def json_mode(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": 'Return JSON: {"fruit": <any fruit>, "count": <int>}'}],
    )
    content = r.choices[0].message.content or ""
    try:
        json.loads(content)
        return "pass", f"content={content!r}"
    except json.JSONDecodeError:
        return "fail", f"not valid JSON: {content!r}"


def tool_call_auto(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, tools=[WEATHER_TOOL],
        messages=[{"role": "user", "content": "What's the weather in Lagos right now?"}],
    )
    c = r.choices[0]
    calls = c.message.tool_calls or []
    if c.finish_reason != "tool_calls" or not calls:
        return "fail", f"no tool call. finish_reason={c.finish_reason}, content={c.message.content!r}"
    call = calls[0]
    if call.type != "function":
        return "fail", f"unexpected tool call type {call.type}"
    args = json.loads(call.function.arguments)
    ok = call.function.name == "get_weather" and "city" in args and bool(call.id)
    return ("pass" if ok else "fail"), f"id={call.id}, name={call.function.name}, args={args}"


def tool_result_roundtrip(client: OpenAI, s: Settings) -> tuple[Status, str]:
    """The full agent-loop handshake: call, our result, then a final text answer."""
    first = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, tools=[WEATHER_TOOL],
        messages=[{"role": "user", "content": "What's the weather in Lagos right now?"}],
    )
    calls = first.choices[0].message.tool_calls or []
    if not calls or calls[0].type != "function":
        return "fail", "no tool call on the first step"
    call = calls[0]
    second = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, tools=[WEATHER_TOOL],
        messages=[
            {"role": "user", "content": "What's the weather in Lagos right now?"},
            {"role": "assistant", "content": first.choices[0].message.content, "tool_calls": [
                {"id": call.id, "type": "function",
                 "function": {"name": call.function.name, "arguments": call.function.arguments}},
            ]},
            {"role": "tool", "tool_call_id": call.id, "content": '{"temp_c": 31, "sky": "thunderstorms"}'},
        ],
    )
    content = second.choices[0].message.content or ""
    ok = "31" in content and second.choices[0].finish_reason == "stop"
    return ("pass" if ok else "fail"), f"finish_reason={second.choices[0].finish_reason}, content={content!r}"


def tool_choice_forced(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, tools=[WEATHER_TOOL],
        tool_choice={"type": "function", "function": {"name": "get_weather"}},
        messages=[{"role": "user", "content": "Tell me a joke about Abuja."}],
    )
    calls = r.choices[0].message.tool_calls or []
    return ("pass" if calls else "fail"), f"tool_calls={len(calls)}, finish_reason={r.choices[0].finish_reason}"


def tool_choice_none(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, tools=[WEATHER_TOOL], tool_choice="none",
        messages=[{"role": "user", "content": "What's the weather in Lagos right now?"}],
    )
    calls = r.choices[0].message.tool_calls or []
    return ("pass" if not calls else "fail"), f"tool_calls={len(calls)}"


def parallel_tool_calls(client: OpenAI, s: Settings) -> tuple[Status, str]:
    r = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, tools=[WEATHER_TOOL],
        messages=[{"role": "user", "content": "Get the weather in Lagos and in Nairobi."}],
    )
    calls = r.choices[0].message.tool_calls or []
    cities = [json.loads(c.function.arguments).get("city") for c in calls if c.type == "function"]
    return "info", f"calls in one reply={len(calls)}, cities={cities}"


def streaming_text(client: OpenAI, s: Settings) -> tuple[Status, str]:
    stream = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, stream=True, stream_options={"include_usage": True},
        messages=[{"role": "user", "content": "Count from 1 to 5."}],
    )
    text, chunks, usage = "", 0, None
    for chunk in stream:
        chunks += 1
        if chunk.usage:
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            text += chunk.choices[0].delta.content
    ok = bool(text) and usage is not None
    return ("pass" if ok else "fail"), f"chunks={chunks}, usage={usage is not None}, text={text!r}"


def streaming_tool_call(client: OpenAI, s: Settings) -> tuple[Status, str]:
    """Tool calls arrive in fragments; we stitch them together by index."""
    stream = client.chat.completions.create(
        model=s.model, max_tokens=SMALL, stream=True, tools=[WEATHER_TOOL],
        messages=[{"role": "user", "content": "What's the weather in Lagos right now?"}],
    )
    calls: dict[int, dict[str, str]] = {}
    finish = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        finish = choice.finish_reason or finish
        for delta in choice.delta.tool_calls or []:
            slot = calls.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
            if delta.id:
                slot["id"] = delta.id
            if delta.function and delta.function.name:
                slot["name"] = delta.function.name
            if delta.function and delta.function.arguments:
                slot["arguments"] += delta.function.arguments
    if not calls:
        return "fail", f"no tool call streamed, finish_reason={finish}"
    first = calls[0] if 0 in calls else next(iter(calls.values()))
    try:
        args = json.loads(first["arguments"])
    except json.JSONDecodeError:
        return "fail", f"arguments did not reassemble into JSON: {first['arguments']!r}"
    return ("pass" if first["id"] and finish == "tool_calls" else "fail"), (
        f"finish_reason={finish}, call={first['name']}, args={args}"
    )


def error_unknown_model(client: OpenAI, s: Settings) -> tuple[Status, str]:
    try:
        client.chat.completions.create(
            model="no-such-model", messages=[{"role": "user", "content": "hi"}], max_tokens=5
        )
    except openai.APIStatusError as exc:
        return "pass", f"{type(exc).__name__}, status={exc.status_code}, code={exc.code!r}"
    return "fail", "an unknown model was accepted"


def error_bad_key(_client: OpenAI, s: Settings) -> tuple[Status, str]:
    bad = OpenAI(base_url=s.base_url, api_key="sk-invalid-key-for-probe", max_retries=0)
    try:
        bad.chat.completions.create(
            model=s.model, messages=[{"role": "user", "content": "hi"}], max_tokens=5
        )
    except openai.AuthenticationError as exc:
        return "pass", f"AuthenticationError, status={exc.status_code}, code={exc.code!r}"
    except openai.APIStatusError as exc:
        return "fail", f"{type(exc).__name__}, status={exc.status_code}, code={exc.code!r}"
    return "fail", "an invalid key was accepted"


def concurrency_three_at_once(client: OpenAI, s: Settings) -> tuple[Status, str]:
    """Praxiom documents a limit of 2 chat requests in flight per account."""

    def one(_: int) -> str:
        try:
            client.chat.completions.create(
                model=s.model, max_tokens=200,
                messages=[{"role": "user", "content": "Write two sentences about rivers."}],
            )
            return "200"
        except openai.APIStatusError as exc:
            retry_after = exc.response.headers.get("retry-after")
            return f"{exc.status_code} {exc.code} retry-after={retry_after}"

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(one, range(3)))
    return "info", f"outcomes={outcomes}"


def idempotency_replay(client: OpenAI, s: Settings) -> tuple[Status, str]:
    """Praxiom documents: the same Idempotency-Key twice -> 409 idempotency_replay."""
    key = f"probe-{uuid.uuid4()}"
    outcomes = []
    for _ in range(2):
        try:
            client.chat.completions.create(
                model=s.model, max_tokens=5, extra_headers={"Idempotency-Key": key},
                messages=[{"role": "user", "content": "Say OK."}],
            )
            outcomes.append("200")
        except openai.APIStatusError as exc:
            outcomes.append(f"{exc.status_code} {exc.code}")
    return "info", f"same key twice -> {outcomes}"


PROBES: list[tuple[str, Probe]] = [
    ("models_list", models_list),
    ("basic_chat", basic_chat),
    ("system_prompt_followed", system_prompt_followed),
    ("text_content_parts", text_content_parts),
    ("max_tokens_length", max_tokens_length),
    ("json_mode", json_mode),
    ("tool_call_auto", tool_call_auto),
    ("tool_result_roundtrip", tool_result_roundtrip),
    ("tool_choice_forced", tool_choice_forced),
    ("tool_choice_none", tool_choice_none),
    ("parallel_tool_calls", parallel_tool_calls),
    ("streaming_text", streaming_text),
    ("streaming_tool_call", streaming_tool_call),
    ("error_unknown_model", error_unknown_model),
    ("error_bad_key", error_bad_key),
    ("concurrency_three_at_once", concurrency_three_at_once),
    ("idempotency_replay", idempotency_replay),
]


# --- runner ---------------------------------------------------------------------


def run_probe(name: str, probe: Probe, client: OpenAI, settings: Settings) -> ProbeResult:
    started = time.monotonic()
    try:
        status, detail = probe(client, settings)
    except openai.APIStatusError as exc:
        status, detail = "fail", f"{type(exc).__name__}, status={exc.status_code}, code={exc.code!r}, {exc.message}"
    except Exception as exc:  # a probe crashing is itself a finding
        status, detail = "fail", f"{type(exc).__name__}: {exc}"
    return ProbeResult(name, status, detail, round(time.monotonic() - started, 2))


def main() -> None:
    settings = load_settings()
    client = OpenAI(base_url=settings.base_url, api_key=settings.api_key, max_retries=0)
    print(f"Probing {settings.provider} ({settings.model}) at {settings.base_url}\n")

    results = []
    for name, probe in PROBES:
        result = run_probe(name, probe, client, settings)
        results.append(result)
        print(f"[{result.status:4}] {name:26} {result.seconds:6.2f}s  {result.detail[:160]}")

    out = Path(__file__).resolve().parent / "results" / f"compat-{settings.provider}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"provider": settings.provider, "model": settings.model, "base_url": settings.base_url,
         "sdk_version": openai.__version__, "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "results": [asdict(r) for r in results]},
        indent=2,
    ))
    counts = {k: sum(r.status == k for r in results) for k in ("pass", "fail", "info")}
    print(f"\n{counts}  ->  {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
