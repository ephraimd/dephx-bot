# Praxium

A desktop app for one coding agent that works like a senior engineer. You open a project folder and ask for a change; the agent evaluates the code, proposes a plan for your approval, builds it, tests it, and only reports "verified" when a check actually passed. Every shell command waits for your approval.

Its judgement comes from the [senior-dephx](https://github.com/ephraimd/senior-dephx) skills, and the evaluate, ideate, build, test process is enforced by the app rather than left to the model.

Works with [Praxiom Cloud](https://cloud.praxiomai.io) and DeepSeek, both through the OpenAI-compatible chat API.

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv run praxium
```

A window opens. On first run, choose a provider and paste its API key, then open a project folder.

## Develop

```bash
uv run praxium --dev            # API on 127.0.0.1:8765, no window; prints a URL with the launch token
cd ui && npm install && npm run dev
```

Open the printed URL's `#token=...` part on the Vite dev server (for example `http://localhost:5173/#token=...`). Vite proxies `/api` to the Python server.

After changing the UI, rebuild it into the Python package (the build is committed, so running the app never needs Node):

```bash
cd ui && npm run build
```

If `npm install` fails with `Cannot read properties of null (reading 'edgesOut')`, your npm has a known resolver bug; run `npx npm@11 install` instead.

### Checks

```bash
uv run mypy --strict praxium tests && uv run pytest && (cd ui && npm run check && npm run test)
```

## Where things are stored

| What | Where |
| --- | --- |
| Provider choice, API keys, default token limit | `~/Library/Application Support/praxium/settings.json` (macOS), `%LOCALAPPDATA%\praxium\praxium\settings.json` (Windows). Environment variables `PRAXIUM_API_KEY` and `DEEPSEEK_API_KEY` override the file. |
| Commands you allowed for a project | `<project>/.praxium/settings.json`. If a folder already has this file, the app asks you to trust its commands first. |

Chats are not saved between launches.

## Layout

```
praxium/          Python app (composition in app.py)
  agent/          the loop, phases, token budget, events, prompt
  tools/          file, shell and skill tools
  chat/           chat service and HTTP routes
  settings/       app and project settings
  skills/         pinned copy of senior-dephx 0.10.0
  static/         the built UI
ui/               SvelteKit source
docs/reports/     findings on the Praxiom API and the M1 verification
docs/tutorials/   one short tutorial per build step
```

## Limits

- The agent cannot see or click your screen, and does not browse the web; web apps are tested with Playwright tests it writes.
- Commands run on your machine with your permissions. Approval is the only safeguard in this version.
- Commands stop after 120 seconds, so long installs may need to be run by you.
