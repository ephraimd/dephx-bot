# dephx-bot

dephx-bot is an AI coding harness: the program between a language model and your codebase that decides what the model sees, which tools it may use, and when its work counts as finished. You open a project folder, describe a change, and an agent reads the code, proposes a plan for your approval, makes the change and proves it works, asking before every command it runs.

It is my research into harness engineering, the craft of making agentic coding flows efficient. The goal is a harness that makes a software engineer many times more productive while spending far fewer tokens than the harnesses in use today.

## How it works

**The phase engine.** The app, not the model, decides what stage the work is in. Every task moves through evaluate, ideate, build, test and done, and each phase unlocks its own tools. The agent cannot write while it is investigating, cannot build until you approve its plan, and cannot report the work as verified unless a check passed after its last change. "Finished and verified" is a fact the app checked, not a sentence the model wrote.

**senior-dephx.** The agent's engineering judgement comes from [senior-dephx](https://github.com/ephraimd/senior-dephx), my plugin that teaches AI coding agents to work like senior engineers: read the real code before changing it, find what already exists, trace what a change breaks, keep changes as small as the problem, and prove the work before claiming it. Its guides are loaded only when a task calls for them, so they cost tokens only when used.

Around those sit a per-chat token limit enforced before every request, a live token count for every step, trimming of recoverable tool output, and compaction that summarises long chats while the app, not the model, keeps the record of files, commands and approvals.

## Models and Praxiom

dephx-bot works with any model behind an OpenAI-compatible chat API. Choose Custom in Settings and enter the API address, model name, context window and largest reply; a couple of providers are already filled in so that you only need a key.

The project was inspired by [Praxiom AI](https://www.praxiomai.io) and built to test what Praxiom can do on real agentic work. [REPORT.md](REPORT.md) covers the design of dephx-bot, the Praxiom integration, how the model behaves, and benchmarks against DeepSeek on speed, cost and coding ability.

## Installing and running

You need [uv](https://docs.astral.sh/uv/) (via Homebrew or its official installer), Git and an API key. uv downloads a suitable Python (3.12 or newer) on first run. The built interface is in the repository, so Node.js is only needed to change the UI.

**macOS**

```bash
brew install uv
git clone https://github.com/ephraimd/dephx-bot.git
cd dephx-bot
uv run dephx-bot
```

**Windows** (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/ephraimd/dephx-bot.git
cd dephx-bot
uv run dephx-bot
```

Open a new terminal after installing uv so the command is on your path. The first start takes a minute or two while dependencies install. On Windows the window needs the Microsoft Edge [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/), which Windows 11 already has, and the agent's commands run through `cmd.exe`.

**Linux, or any browser.** `uv run dephx-bot --dev` serves the same interface without a native window. Open the address it prints, including the `#token=...` part; the token is new on every start and is what lets that tab, and nothing else, talk to the app.

## API keys

- **Praxiom Cloud:** create an account at [cloud.praxiomai.io](https://cloud.praxiomai.io) and an API key (it starts with `px_live_` and is shown once).
- **DeepSeek:** create an account and key at [platform.deepseek.com](https://platform.deepseek.com).
- **Any other provider:** choose Custom in Settings.

Keys are stored only on your computer, in `~/Library/Application Support/dephx-bot/settings.json` on macOS or `%LOCALAPPDATA%\dephx-bot\dephx-bot\settings.json` on Windows. The interface shows whether a key is set, never the key. The environment variables `PRAXIOM_API_KEY` and `DEEPSEEK_API_KEY` take precedence when set.

## Using it

1. On first start, choose a provider, paste its key and set a token limit per chat.
2. Open the folder the agent should work in. It can read and write only inside it, and never inside `.git`.
3. Describe the change, for example "Add a /health endpoint and a test for it", and follow the phases at the top of the window. Approve the plan, then approve each command with Run once, Always allow in this project, or Deny.

Each step shows the tokens it used. Stop ends the current step and kills any running command along with everything it started. Compact summarises the chat on demand, and Undo restores it until your next message.

Commands you always allow are saved in the project's `.dephx/settings.json`. If you open a folder that already has one, dephx-bot shows you the list and asks whether to trust it. You can also ask the agent to set this up for you; it proposes the change and nothing is saved until you press Apply.

## Costs

An agent sends the whole conversation on every step, so tokens add up: a small task takes around 30,000 tokens and a small web page with a Playwright test around 100,000. The token limit is checked before each request, so a chat never goes over it; raise it from the counter at the top of the window to continue. Stop prevents further spending, but tokens a provider has already generated are still billed.

## Development

The backend is a FastAPI package in `dephx_bot/`, and the interface is a SvelteKit app in `ui/` built into `dephx_bot/static/`.

```bash
uv run dephx-bot --dev          # backend on 127.0.0.1:8765
cd ui && npm install && npm run dev   # interface with live reload on localhost:5173
```

Open `http://localhost:5173/#token=...` with the token from the first command, and run `npm run build` when finished. Before sending a change:

```bash
uv run mypy --strict dephx_bot tests && uv run pytest
cd ui && npm run check && npm run test
```

`playground/malaria-agent/` is the small agent used for the first Praxiom tests.

## Limitations

The agent does not see your screen or browse the web; it tests web projects through Playwright tests it writes. Commands run with your user's permissions, so read each one before allowing it. Commands stop after 120 seconds. One chat is open at a time, and chats are not saved.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
