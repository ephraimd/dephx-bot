"""The agent loop: an LLM inside a while-loop, with our code running its tool requests.

One user turn looks like this:

    user message
      -> model replies with tool_calls?  yes -> run each tool, append results, ask again
                                         no  -> that reply is the final answer
"""

import json
from collections.abc import Callable

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
)

from tools import Tool, ToolError

# Hard stop so a confused model cannot loop (and spend) forever.
MAX_STEPS = 8


class Agent:
    def __init__(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        on_event: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        self.on_event = on_event  # how we show the user what the agent is doing
        # The whole conversation. This list IS the agent's memory.
        self.messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
        ]

    def ask(self, user_text: str) -> str:
        """Handle one user message and return the agent's final answer."""
        self.messages.append({"role": "user", "content": user_text})

        for _ in range(MAX_STEPS):
            # 1. Send everything: system prompt, history and the tool list.
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=[tool.schema() for tool in self.tools.values()],
            )
            message = response.choices[0].message
            tool_calls = _function_calls(message)
            self.messages.append(_to_history(message, tool_calls))

            # 2. No tool requested: the model is done, this is the answer.
            if not tool_calls:
                return message.content or ""

            # 3. Run every tool the model asked for, and feed each result back.
            for call in tool_calls:
                result = self._run_tool(call)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
            # 4. Loop: the model now reads the results and decides again.

        return f"Stopped after {MAX_STEPS} steps without a final answer."

    def _run_tool(self, call: ChatCompletionMessageFunctionToolCall) -> str:
        """Execute one tool call. Errors become text for the model, never crashes."""
        name = call.function.name
        tool = self.tools.get(name)
        if tool is None:
            return f"Tool error: there is no tool named '{name}'."

        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            return "Tool error: arguments were not valid JSON."

        self.on_event(f"[tool] {name} {args}")
        try:
            result = tool.run(args)
        except ToolError as exc:
            result = f"Tool error: {exc}"
        self.on_event(f"[tool] {name} -> {len(result)} chars")
        return result


def _function_calls(message: ChatCompletionMessage) -> list[ChatCompletionMessageFunctionToolCall]:
    """The SDK also models "custom" tool calls; we only define function tools."""
    return [
        call
        for call in message.tool_calls or []
        if isinstance(call, ChatCompletionMessageFunctionToolCall)
    ]


def _to_history(
    message: ChatCompletionMessage,
    tool_calls: list[ChatCompletionMessageFunctionToolCall],
) -> ChatCompletionAssistantMessageParam:
    """Convert the model's reply into the shape we send back next time.

    The tool_calls must be kept: each later "tool" message points at one of
    these ids, and the API rejects a tool result whose call it cannot find.
    """
    entry: ChatCompletionAssistantMessageParam = {
        "role": "assistant",
        "content": message.content,
    }
    if tool_calls:
        entry["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in tool_calls
        ]
    return entry
