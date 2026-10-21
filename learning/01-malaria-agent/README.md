# Lesson 01: a minimal tool-using agent

A malaria clinical decision-support agent, powered by DeepSeek through the OpenAI client, with one tool: `read_webpage`. The goal is to see the whole agent loop in five small files (under 400 lines, most of it comments), so the Go version later is a translation, not a mystery.

> This is a learning project, not a medical device. It does not replace a test or a clinician.

## Run it

```bash
cd learning/01-malaria-agent
uv sync
uv run python main.py
```

One-shot mode: `uv run python main.py "I got back from Kano 10 days ago and have had fever and chills for 3 days"`.

The key is read from the environment, falling back to `.config/.vault` at the project root. DeepSeek is the default; run the same agent on Praxiom with:

```bash
PROVIDER=praxiom uv run python main.py
```

`compat_probe.py` checks a provider feature by feature against the OpenAI API behaviour; its findings are in section 3 of [`REPORT.md`](../../REPORT.md).

## The files, in reading order

| File | Job | Lines worth staring at |
| --- | --- | --- |
| `config.py` | Which provider, key and model | `base_url` is the only thing that ties us to DeepSeek |
| `prompts.py` | All instruction text | The system prompt defines behaviour and when to use tools |
| `tools.py` | Tool schema + Python function | `Tool.schema()` is what the model sees; `run` is what we execute |
| `agent.py` | The loop | `Agent.ask()` is the whole idea |
| `main.py` | Wiring and the chat prompt | `build_agent()` is the single place things are assembled |

## Concept 1: the model never runs tools

Every request carries a `tools` list: names, descriptions and JSON Schemas for the arguments. When the model wants help, it does not reply with text. It replies with `finish_reason: "tool_calls"` and a structured request:

```json
{"id": "call_00_...", "type": "function",
 "function": {"name": "read_webpage", "arguments": "{\"url\": \"https://www.who.int/...\", \"focus\": \"symptoms\"}"}}
```

Note `arguments` is a JSON **string**, not an object. The model wrote it, so it can be malformed, which is why `_run_tool` parses it inside a `try`.

## Concept 2: the loop

`Agent.ask()` in `agent.py`:

1. Append the user message to `self.messages`.
2. Call the model with the full history and the tool list.
3. Append the model's reply to history, **including its `tool_calls`**.
4. No tool calls? That reply is the answer. Return it.
5. Otherwise run each tool and append one `{"role": "tool", "tool_call_id": ..., "content": ...}` message per call, then go back to step 2.

`MAX_STEPS` caps the loop. Without it, a model that keeps asking for tools would spend money forever.

## Concept 3: memory is just the message list

There is no hidden state. `self.messages` is the agent's entire memory, and the full list is re-sent on every step. That is why every token you append costs you again on every later call, and why it matters so much against Praxiom's 64K window (see section 3.2 of [`REPORT.md`](../../REPORT.md)).

## Concept 4: tool errors are feedback, not crashes

When a tool fails (bad URL, 404, blocked host), `_run_tool` returns `"Tool error: ..."` as the tool result instead of raising. The model reads it and adapts: tries another URL, or answers from what it knows. Only `ToolError` is caught; a real bug in our code still crashes loudly.

## Concept 5: summarise inside the tool

`read_webpage` does not hand the raw page to the agent. It makes a **second, separate** LLM call with a focused summary prompt and returns only that summary. On a test run, the WHO malaria fact sheet was about 13,700 characters of text and the summary that entered memory was under 800. This is the first context-management technique: shrink tool results before they enter memory.

## Concept 6: the security check lives in our code

The model chooses the URL, so the model could ask for `http://localhost:8080/admin` or `http://169.254.169.254/` (cloud metadata). `_block_private_hosts` resolves each host and refuses private, loopback and link-local addresses, and the httpx hook runs on every redirect hop too. This is the web equivalent of the sandbox path check the desktop app needs: never trust the model's arguments.

Known gap: a host could resolve to a public IP during our check and a private one during the actual connection (DNS rebinding). Closing that properly means connecting to the IP we checked. Fine for a lesson, worth knowing for the real app.

## What this agent deliberately does not do

- **No streaming.** Answers arrive all at once. Streaming tool calls arrive as fragments you must stitch together; that is a later lesson.
- **No history trimming.** A long chat will eventually exceed the context window.
- **No retries or idempotency keys.** A network error simply raises.
- **No persistence.** Quit and the conversation is gone.

Each of these is a decision for the Go version.

## Exercises

1. Add a second tool, `get_current_date`, with no arguments. Watch whether the model uses it when you ask "is it rainy season now in Lagos?".
2. Print `len(self.messages)` and `response.usage` after each step to see memory and cost grow.
3. Change `read_webpage` to return the raw page text instead of a summary, and compare `usage.prompt_tokens`.
4. Ask it to read `http://localhost:8000` and watch the model react to the blocked-host error.
