import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, cast

import httpx2
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam
from openai.types.completion_usage import CompletionUsage

from dephx_bot.model import (
    Message,
    ModelError,
    ModelReply,
    TextDelta,
    ToolCall,
    ToolSchema,
    Usage,
    estimate_tokens,
)

MAX_RETRIES = 2
MAX_RETRY_WAIT_SECONDS = 60.0
# 409 is deliberately absent: Praxiom uses it for idempotency_replay, and retrying cannot help.
RETRYABLE_STATUSES = (429, 503)


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    base_url: str
    model: str
    env_key: str
    context_window: int
    hidden_prompt_tokens: int
    max_output_tokens: int
    max_concurrency: int
    history_budget: int
    supports_forced_tool_choice: bool
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    currency: str | None = None

    def min_token_limit(self) -> int:
        """The largest single call; a chat limit below this could never send anything."""
        return self.hidden_prompt_tokens + self.history_budget + self.max_output_tokens

    def cost(self, usage: Usage) -> float | None:
        if self.input_price_per_million is None or self.output_price_per_million is None:
            return None
        return (
            usage.input_tokens * self.input_price_per_million
            + usage.output_tokens * self.output_price_per_million
        ) / 1_000_000


# Our cap on any provider's history budget: a window of a million tokens would make every step expensive.
MAX_HISTORY_BUDGET = 120_000
CUSTOM_PROVIDER_ID = "custom"

# Values from Praxiom's API documentation, checked against the live API (REPORT.md, section 3).
PROVIDERS: dict[str, ProviderSpec] = {
    "praxiom": ProviderSpec(
        id="praxiom",
        label="Praxiom",
        base_url="https://cloud.praxiomai.io/v1",
        model="praxiom",
        env_key="PRAXIOM_API_KEY",
        context_window=65_536,
        hidden_prompt_tokens=3_000,
        max_output_tokens=8_192,
        max_concurrency=2,
        history_budget=65_536 - 3_000 - 8_192,
        supports_forced_tool_choice=True,
        input_price_per_million=0.276,
        output_price_per_million=1.101,
        currency="ESP",
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        env_key="DEEPSEEK_API_KEY",
        context_window=1_048_576,
        hidden_prompt_tokens=0,
        max_output_tokens=8_192,
        max_concurrency=4,
        history_budget=MAX_HISTORY_BUDGET,
        supports_forced_tool_choice=False,
    ),
}


def custom_provider(base_url: str, model: str, context_window: int, max_output_tokens: int) -> ProviderSpec:
    """Any OpenAI-compatible endpoint the user names. Limits and prices are unknown, so stay conservative."""
    return ProviderSpec(
        id=CUSTOM_PROVIDER_ID,
        label="Custom",
        base_url=base_url,
        model=model,
        env_key="",
        context_window=context_window,
        hidden_prompt_tokens=0,
        max_output_tokens=max_output_tokens,
        max_concurrency=2,
        history_budget=min(context_window - max_output_tokens, MAX_HISTORY_BUDGET),
        supports_forced_tool_choice=False,
    )


class OpenAICompatibleClient:
    def __init__(
        self, spec: ProviderSpec, api_key: str, *, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        self.spec = spec
        self._client = AsyncOpenAI(
            base_url=spec.base_url, api_key=api_key, max_retries=0, http_client=http_client
        )
        self._gate = asyncio.Semaphore(spec.max_concurrency)

    async def stream(
        self, messages: list[Message], tools: list[ToolSchema], max_tokens: int
    ) -> AsyncGenerator[TextDelta | ModelReply, None]:
        async with self._gate:
            response = await self._open_stream(messages, tools, max_tokens)
            text = ""
            calls: dict[int, dict[str, str]] = {}
            usage: CompletionUsage | None = None
            try:
                async for chunk in response:
                    if chunk.usage:
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text += delta.content
                        yield TextDelta(delta.content)
                    for part in delta.tool_calls or []:
                        call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                        if part.id:
                            call["id"] = part.id
                        if part.function and part.function.name:
                            call["name"] = part.function.name
                        if part.function and part.function.arguments:
                            call["arguments"] += part.function.arguments
            except openai.APIError as exc:
                raise ModelError("provider_error", f"The stream broke off: {exc}") from exc

            tool_calls = [ToolCall(c["id"], c["name"], c["arguments"]) for _, c in sorted(calls.items())]
            yield ModelReply(text, tool_calls, _usage(usage, messages, text, tool_calls))

    async def _open_stream(
        self, messages: list[Message], tools: list[ToolSchema], max_tokens: int
    ) -> openai.AsyncStream[Any]:
        # Retry only here, before the first chunk: a retry after text was shown would repeat it.
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await self._client.chat.completions.create(
                    model=self.spec.model,
                    messages=[_to_openai(m) for m in messages],
                    tools=cast(list[ChatCompletionToolUnionParam], tools) if tools else openai.omit,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
            except openai.APIStatusError as exc:
                error = _to_model_error(exc)
                if exc.status_code not in RETRYABLE_STATUSES or attempt == MAX_RETRIES:
                    raise error from exc
                wait = 1.0 if error.retry_after is None else error.retry_after
                await asyncio.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
            except openai.APIConnectionError as exc:
                raise ModelError(
                    "provider_unreachable", f"Could not reach {self.spec.label}: {exc}"
                ) from exc
        raise AssertionError("unreachable")


class ClientPool:
    """One client per provider and key, so the concurrency limit is shared by every chat."""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], OpenAICompatibleClient] = {}

    def get(self, spec: ProviderSpec, api_key: str) -> OpenAICompatibleClient:
        key = (spec.id, api_key)
        if key not in self._clients:
            self._clients[key] = OpenAICompatibleClient(spec, api_key)
        return self._clients[key]


def _to_openai(message: Message) -> ChatCompletionMessageParam:
    if message.role == "system":
        return {"role": "system", "content": message.content}
    if message.role == "user":
        return {"role": "user", "content": message.content}
    if message.role == "tool":
        return {"role": "tool", "content": message.content, "tool_call_id": message.tool_call_id or ""}
    if message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in message.tool_calls
            ],
        }
    return {"role": "assistant", "content": message.content}


def _usage(
    reported: CompletionUsage | None, messages: list[Message], text: str, tool_calls: list[ToolCall]
) -> Usage:
    if reported is not None:
        details = reported.prompt_tokens_details
        return Usage(
            input_tokens=reported.prompt_tokens,
            output_tokens=reported.completion_tokens,
            cached_tokens=details.cached_tokens if details else None,
            estimated=False,
        )
    # Praxiom bills an unreported stream from the text produced, so estimate the same way.
    sent = "".join(m.content + "".join(c.arguments for c in m.tool_calls) for m in messages)
    received = text + "".join(c.arguments for c in tool_calls)
    return Usage(estimate_tokens(sent), estimate_tokens(received), None, estimated=True)


def _to_model_error(exc: openai.APIStatusError) -> ModelError:
    message = exc.body.get("message") if isinstance(exc.body, dict) else None
    retry_after = exc.response.headers.get("retry-after")
    return ModelError(
        code=exc.code or "provider_error",
        message=str(message or exc.message),
        status=exc.status_code,
        retry_after=float(retry_after) if retry_after else None,
    )
