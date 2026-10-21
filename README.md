# dephx-bot

dephx-bot is an AI coding harness: the program that sits between a language model and your codebase, decides what the model is shown, which tools it may use, and when its work counts as finished. You open a project folder, describe a change, and an agent carries it out on your machine while you approve every command it runs.

I built it as my own research into harness engineering, the study of how to make agentic coding flows efficient. The long-term goal is a harness that makes a software engineer many times more productive, on the order of a hundred times, while spending far fewer tokens than the harnesses in use today. It is not there yet. Those are the aims the design is judged against, and measuring the harness against them honestly is part of the work.

## What makes it work

Two ideas carry most of the weight.

**The phase engine.** The app, not the model, decides what stage the work is in. Every task moves through five phases: evaluate, ideate, build, test and done. Each phase unlocks a different set of tools, so the agent cannot write a file while it is still investigating, cannot start building until you approve its plan, and cannot report the work as verified unless a check actually passed after its last change. A model can be persuaded to skip a step by its own reasoning; a state machine in the harness cannot. This keeps runs short and predictable, and it means "done and verified" is a fact the app checked, not a sentence the model wrote.

**senior-dephx.** The agent's engineering judgement comes from [senior-dephx](https://github.com/ephraimd/senior-dephx), a set of guidelines I wrote about how a senior engineer works: read the real code before changing it, find what already exists before adding something new, keep the change as small as the problem, and prove that it works before saying so. The guidelines ship with the app and are loaded one at a time, only when the task in front of the agent calls for them, so they cost tokens only when they are used. The app shows that a guideline was loaded and how large it was, but not its text.

Around those two ideas sit the parts any serious harness needs: a token limit per chat that is enforced before each request rather than after, a live count of the tokens every step used, trimming of old tool output that the agent can recover by running the tool again, and compaction that rewrites a long chat into a summary whose record of files, commands and approvals is kept by the app instead of remembered by the model.

## Models

dephx-bot works with any model served through an OpenAI-compatible chat API. Choose Custom in Settings and enter the API address, the model name, its context window and its largest reply. A couple of providers are already filled in so that you only need to paste a key.

The project was inspired by [Praxiom AI](https://www.praxiomai.io) and was built to test what the Praxiom model can do on real agentic work: long tool-calling loops, a hidden provider prompt on every request, a 64,000-token window and prepaid billing. The reports in `docs/reports/` record what that testing found, from OpenAI compatibility to speed and cost.

## What testing Praxiom showed

The `bench/` folder holds a small benchmark: coding tasks with hidden tests the model never sees, and a meter that adds up the token usage each provider reports. [Report 05](docs/reports/05-praxiom-vs-deepseek.md) uses it to compare Praxiom with DeepSeek on speed, cost and ability. In short, Praxiom solved every task at the first attempt, started answering in under a second and used far fewer output tokens, while its hidden system prompt and the lack of a discount for repeated prompts made agent loops cost more than they need to. The earlier reports cover its OpenAI compatibility and its image models.

## What you need

| | macOS | Windows |
| --- | --- | --- |
| Operating system | A recent macOS. Development and testing were done on macOS 26. | Windows 10 or 11 |
| Python | 3.12 or newer. You do not need to install it yourself if you use uv, which downloads a suitable Python on first run. | Same as macOS |
| [uv](https://docs.astral.sh/uv/) | Required. It installs dephx-bot's Python dependencies into a private environment and runs the app. | Required |
| Git | To download the code. Comes with the Xcode command line tools. | [Git for Windows](https://git-scm.com/download/win) |
| Web view | Built into macOS | Microsoft Edge WebView2, which is already installed on Windows 11 and on Windows 10 machines with a current Edge. If it is missing, install the [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/). |
| An API key | From Praxiom Cloud or DeepSeek, see below | Same as macOS |

Node.js is only needed if you want to change the user interface. The built interface is included in the repository, so running the app does not require it.

## Installing and running on macOS

1. Install uv if you do not already have it. Either use Homebrew:

   ```bash
   brew install uv
   ```

   or the official installer:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Open a new terminal window afterwards so the `uv` command is on your path.

2. Download dephx-bot and move into its folder:

   ```bash
   git clone https://github.com/ephraimd/dephx-bot.git
   cd dephx-bot
   ```

3. Start the app:

   ```bash
   uv run dephx-bot
   ```

   The first start takes a minute or two while uv creates the environment and installs the dependencies. After that it opens in a few seconds. A window titled dephx-bot appears, and closing the window stops the app.

## Installing and running on Windows

1. Open PowerShell and install uv with the official installer:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Close PowerShell and open it again so the `uv` command is available.

2. Download dephx-bot and move into its folder:

   ```powershell
   git clone https://github.com/ephraimd/dephx-bot.git
   cd dephx-bot
   ```

3. Start the app:

   ```powershell
   uv run dephx-bot
   ```

   As on macOS, the first start installs everything and later starts are quick. Commands that the agent runs on Windows go through `cmd.exe`, and the agent is told which operating system and shell it is working with so that it writes commands that fit.

The Windows version has been written with Windows in mind (process handling, paths that ignore letter case, line endings, the location of settings) but it has not yet been tested on a real Windows machine. If something does not work, please open an issue with what you saw.

## Linux

dephx-bot has not been tested on Linux. The window comes from [pywebview](https://pywebview.flowrl.com), which on Linux needs GTK and WebKit2GTK installed through your distribution, so `uv run dephx-bot` may not be able to open a window on a fresh system. You can still use the app by running it in development mode, which serves the same interface to your normal browser:

```bash
uv run dephx-bot --dev
```

The terminal prints an address that ends in `#token=...`. Open that full address in your browser. The token is created fresh every time the app starts and is what allows that browser tab, and nothing else, to talk to the app.

## Getting an API key

dephx-bot does not include a model of its own. You need an API key from a provider whose API follows OpenAI's chat completions format, which today covers most hosted models and local servers such as Ollama and vLLM.

**Praxiom Cloud.** Create an account at [cloud.praxiomai.io](https://cloud.praxiomai.io). New accounts receive a small amount of free credit once the email address is confirmed, and further credit is bought in Espees from the dashboard. Create an API key under API keys; it starts with `px_live_` and is only shown once, so copy it straight into dephx-bot. Praxiom's model has a 64,000-token context window, which includes about 3,000 tokens of Praxiom's own instructions on every request, so long sessions reach compaction sooner than on a model with a larger window.

**DeepSeek.** Create an account at [platform.deepseek.com](https://platform.deepseek.com), add credit, and create an API key. DeepSeek has a very large context window and charges much less for a repeated prompt prefix, which suits the many requests an agent makes.

**Anything else.** Choose Custom in Settings, enter the API address (for example `https://api.example.com/v1`), the model name exactly as the provider spells it, the context window and the largest reply, and paste the key. The token limit you can set depends on those last two numbers, because one full request has to fit inside it.

## Your first session

When the app opens for the first time it asks you to choose a provider and paste its API key. The key is stored in a settings file on your computer and the interface never displays it again; it only shows that a key is set and its first few characters, so you can tell which key is in use. On the same screen you set a token limit per chat, which is explained under Costs below.

Next, choose the folder you want the agent to work in, either by typing its path or with Browse. The agent can only read and write files inside this folder, and it can never touch the folder's `.git` directory or dephx-bot's own `.dephx` settings directory, because writing there would let it run code outside your approval. If the folder already contains a `.dephx/settings.json` with commands someone marked as always allowed, dephx-bot shows you that list and asks whether you trust it before any of those commands can run without asking.

Then describe what you want in plain words, for example "Add a /health endpoint and a test for it". You can watch the agent move through its phases at the top of the window:

- **Evaluate.** The agent reads the files it needs and works out what the change touches. It cannot write anything yet.
- **Ideate.** It considers the options, picks one and writes a short plan. The plan appears in the chat with Approve and Request changes buttons. If you ask for changes, your reason goes back to the agent and it revises the plan.
- **Build.** After you approve, it makes the change. Each command it wants to run appears with Run once, Always allow in this project, and Deny.
- **Test.** It runs the checks that prove the change works. For web projects it writes Playwright tests, which start the development server, run the tests and stop the server in a single command.
- **Done.** It writes a short report of what changed, what it verified and how, and what it did not verify. The chat marks the result as either "Finished and verified" or "Finished, not verified".

Each step of the conversation shows how many tokens it used, and each tool result shows how many tokens it adds to every later request. If you need to stop the agent, press Stop; it ends the current step, cancels any question that is still open, and kills any command that is running along with everything that command started.

Long sessions eventually fill the model's context window, because every step sends the whole conversation again. dephx-bot first hides old command output and old file contents from each request, which costs nothing because the agent can read the file again if it needs to. When that is no longer enough, it compacts the chat: the older part of the conversation is replaced by a summary, and a line in the chat shows how many tokens that saved. The summary's record of which files changed, which commands ran and what you approved comes from dephx-bot's own log rather than from the model, so those facts cannot be lost or made up; the model only writes down what it learned and what is still open. You can also press Compact at the top of the window whenever you like, and tell it anything the agent must keep in mind. Opening the line in the chat shows exactly what the agent now remembers, and Undo puts the full conversation back until you send your next message.

## Costs and limits

Every request to the model is billed by the provider in tokens, and an agent makes many requests in one task, because it sends the whole conversation again each time it takes a step. A small task, such as creating two files and checking them, used around 30,000 tokens with DeepSeek in testing. Building a small web page with a Playwright test from an empty folder used around 100,000.

To keep spending predictable, every chat has a token limit that you set in Settings and can raise for the current chat from the counter at the top of the window. Before each request dephx-bot adds up what the chat has used so far and the largest possible size of the next request, and it does not send the request if that would go over the limit. Instead the chat tells you how much was used and lets you raise the limit and continue.

Pressing Stop stops the agent from doing anything further, but tokens the provider has already generated are still billed. A lower token limit is the reliable way to spend less. Compacting a long chat also helps, because every later step then sends fewer tokens, although writing the summary is itself one request that counts towards the limit.

## Where your settings are kept

| What | Where |
| --- | --- |
| Provider, API keys and the default token limit | `~/Library/Application Support/dephx-bot/settings.json` on macOS and `%LOCALAPPDATA%\dephx-bot\dephx-bot\settings.json` on Windows. On macOS and Linux the file is readable only by your user account. |
| Commands you chose to always allow for a project | `.dephx/settings.json` inside that project folder |

If you set the environment variable `PRAXIOM_API_KEY` or `DEEPSEEK_API_KEY` before starting the app, that key is used for its provider instead of the one in the settings file. Conversations are not saved; closing the app or starting a new chat clears them.

## Working on dephx-bot itself

The backend is a Python package in `dephx_bot/` built on FastAPI, and the interface is a SvelteKit app in `ui/` that is compiled into `dephx_bot/static/`. When you run `uv run dephx-bot`, a small web server starts on a random local port, protected by a token that is created for that run, and a native window opens to show the interface.

To change the interface with live reloading, run the backend in development mode in one terminal:

```bash
uv run dephx-bot --dev
```

and the Vite development server in another:

```bash
cd ui
npm install
npm run dev
```

Then open `http://localhost:5173/#token=...`, using the token printed by the first terminal. Vite forwards API requests to the backend on port 8765. When you are finished, rebuild the interface so that the app picks up your changes:

```bash
cd ui
npm run build
```

Before sending a change, run the type checks and tests for both halves of the project:

```bash
uv run mypy --strict dephx_bot tests && uv run pytest
cd ui && npm run check && npm run test
```

The `docs/` folder explains how the project was designed and built. `docs/tutorials/` has a short guide to each part of the code, written in the order the parts were built. `docs/reports/` records what was found while testing the Praxiom API, what the end-to-end testing of the app showed, including the bugs that real runs uncovered, and the benchmark against Aider. `bench/` holds the benchmark itself, so anyone can rerun it with their own keys.

## Troubleshooting

**`npm install` fails with "Cannot read properties of null (reading 'edgesOut')".** This is a bug in some releases of npm 11 when it resolves the test tools' optional dependencies. Run the install with a newer npm without changing your global one: `npx npm@11 install`.

**The window opens but stays blank on Windows.** The WebView2 runtime is probably missing or out of date. Install it from Microsoft's [WebView2 page](https://developer.microsoft.com/microsoft-edge/webview2/) and start the app again.

**`uv run dephx-bot --dev` says the address is already in use.** Another copy of dephx-bot, or another program, is using port 8765. Close the other program, or stop the earlier dephx-bot with Ctrl+C in its terminal.

**The browser shows "Missing or invalid launch token".** The address you opened is missing the `#token=...` part, or it belongs to an earlier run. Copy the full address from the terminal again.

**A command fails because a tool is missing, such as pytest.** The agent's commands run with your normal system environment, not dephx-bot's own Python environment, so they only find tools that are installed for your project or your user. Install the tool the usual way for that project, or approve the agent's command to install it.

**The agent stops with "Token limit reached".** The chat used up its token limit. Click the token counter at the top of the window, raise the limit, and send a message such as "continue".

## Known limitations

The agent cannot see or click your screen and does not browse the web, so it tests web projects through Playwright tests that it writes. Commands run on your computer with your user's permissions, and your approval is the only safeguard against a harmful command, so read each one before you allow it. Commands are stopped after 120 seconds, which means a very slow install may need to be run by you in a terminal. Only one chat is open at a time, and chats are not saved.

## License

dephx-bot is released under the Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
