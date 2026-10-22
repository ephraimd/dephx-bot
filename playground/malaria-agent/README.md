# Malaria agent

A small clinical decision-support agent for malaria questions, written before dephx-bot to test Praxiom AI's tool calling, OpenAI compatibility, reasoning and image models. It has one tool, `read_webpage`, which fetches a page and returns a focused summary of it. The findings are in section 5 of [REPORT.md](../../REPORT.md).

It is a test bed, not a medical device, and it does not replace a test or a clinician.

## Running it

```bash
cd playground/malaria-agent
uv sync
uv run python main.py
```

Ask a single question with `uv run python main.py "I got back from Kano 10 days ago and have had fever and chills for 3 days"`.

The agent uses DeepSeek by default. Set `PROVIDER=praxiom` to run the same code on Praxiom. Keys are read from the environment (`DEEPSEEK_API_KEY`, `PRAXIUM_API_KEY`) or from `.config/.vault` at the repository root.

Two probes come with it:

- `compat_probe.py` checks a provider feature by feature against the OpenAI API's behaviour, through the official SDK.
- `image_probe.py` generates and edits one image and compares the spend with the price list.

Both write their results to `results/`, which is not committed.

## How it is put together

| File | What it does |
| --- | --- |
| `config.py` | Chooses the provider, model and key |
| `prompts.py` | The system prompt and the page-summary prompt |
| `tools.py` | `read_webpage`, with its schema and its safety checks |
| `agent.py` | The tool-calling loop |
| `main.py` | Assembles the agent and runs the chat |

Three design choices are worth knowing:

- **Tool results are summarised before they enter the conversation.** `read_webpage` makes a separate model call that keeps only what is relevant to the question. The WHO malaria fact sheet is about 13,700 characters; the summary that reaches the agent is under 2,000. Every token in the conversation is paid for again on every later step, so this matters on a 65,536-token window.
- **Tool errors are returned to the model as results.** A 404 or a blocked address becomes a message the model reads and works around, while a real bug in the code still fails loudly.
- **The model's arguments are never trusted.** The model chooses the URL, so private, loopback and link-local addresses are refused on the first request and on every redirect, which stops it reaching services such as a cloud metadata endpoint.
