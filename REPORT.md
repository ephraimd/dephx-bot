# Praxiom AI and dephx-bot

**Author:** Ephraim Adedamola · **Period:** 20 to 22 October 2026 · **Model tested:** Praxiom Cloud (`praxiom`, `praxiom-image`, `praxiom-image-edit`), with DeepSeek (`deepseek-flash`) as a reference point

## Summary

I built dephx-bot, an AI coding harness, to find out how far Praxiom AI can go on real agentic work, and to try out two ideas of my own about how coding agents should be built. This report covers both.

Praxiom held up well. Its API behaved exactly as OpenAI's does in all 13 compatibility checks I ran through the official OpenAI SDK, including the tool-calling features an agent depends on. DeepSeek passed 12. It solved all five coding tasks in my benchmark at the first attempt, answered a clinical question about malaria with real care and local knowledge, starts replying in under a second and uses far fewer output tokens than a reasoning model to reach the same result. Integrating it took no Praxiom-specific code beyond a few numbers in a provider record.

Its costs in agent use come mostly from two things that are easy to fix on the platform side: a hidden system prompt of about 3,000 tokens that is billed on every request, and no discount for the repeated part of a prompt, which most of an agent's input is. Section 7 sets out what I would suggest.

On my side, dephx-bot moves the agent's working state out of the model and into a small state machine I call the phase engine, and gives the agent senior engineering judgement through senior-dephx, a set of guidelines I wrote and use in my own work. Both models I tested tried to skip steps, and the phase engine stopped them each time.

| | Praxiom | DeepSeek |
| --- | --- | --- |
| OpenAI compatibility checks passed | **13 of 13** | 12 of 13 |
| Coding tasks solved at the first attempt | **5 of 5** | 5 of 5 |
| Time to first token (median) | **0.94 s** | 1.50 s |
| Output tokens for all five tasks | **4,951** | 12,149 |
| Cost of all five tasks | ₦31.94 | ₦10.17 off-peak, about ₦20 at peak |

## 1. Why I built dephx-bot

Praxiom Cloud, Praxiom's developer API, published its first full API reference on 20 October 2026. It describes an OpenAI-compatible chat API with tool calling, a 65,536-token context window and prepaid billing in Espees. I wanted to know whether that holds up under the hardest kind of use a chat model gets today, which is an agent that writes and tests code on someone's machine for many steps in a row.

Rather than test it with toy prompts, I built the agent. dephx-bot is a desktop app: you open a project folder, describe a change, and the agent reads the code, proposes a plan for you to approve, makes the change and proves it works by running the project's checks. Every shell command it wants to run is shown to you first. It runs on macOS and is written for Windows, and it works with any model behind an OpenAI-compatible API, so the same harness could be pointed at Praxiom and at DeepSeek and compared fairly.

## 2. The dephx-bot system

A coding harness is everything around the model: what the model is shown, which tools it may use, when its output is trusted and when the work counts as finished. I believe most of the quality of a coding agent comes from the harness rather than the model, and that is where my two ideas sit.

### 2.1 The phase engine

In most coding agents the model keeps track of where it is in the work. It writes its own to-do list, decides when it has finished planning, and decides when the work is done. The harness takes the model's word for it. This works with the strongest models and fails quietly with weaker ones: a model that skips investigation, starts editing before it understands the code, or announces success without running the tests is still treated as following the process.

dephx-bot holds that state in the app instead. The phase engine is a small state machine with five phases, and the model can only move between them by calling one tool, `advance_phase`, which the app checks before it agrees.

| Phase | Tools the model can use | Can move to |
| --- | --- | --- |
| Evaluate | Read files, search, load guidance | Ideate, or Done if nothing needs changing |
| Ideate | Read files, search, load guidance | Build, once you approve the plan |
| Build | Everything, including writing files and running commands | Test, or back to Ideate |
| Test | Read files and run commands, but not write | Done, Build or Ideate |
| Done | Nothing | |

Three rules do most of the work. A tool that the current phase does not allow is refused before it runs, with a message telling the model what it may do instead. Moving from Ideate to Build shows the plan to the user with Approve and Request changes buttons, so no code is written before a person has agreed to the approach. And the agent can only finish as "verified" if a check passed after its last file change; otherwise it must finish as unverified and say why. The user sees the result as either "Finished and verified" or "Finished, not verified", and that label is a fact the app checked, not a sentence the model wrote.

This mattered in practice. In testing, both Praxiom and DeepSeek tried at least once to jump from Evaluate straight to Build. The engine refused, the model read the reason and went back through planning. An instruction in the prompt alone would not have caught it.

Because the state lives outside the model, it also survives things that the model's memory does not. When a long chat is compacted into a summary, the phase and the verification record are untouched, so no summary can skip a plan approval or carry a passing test into the next request.

### 2.2 senior-dephx

The phase engine decides when the agent may do something. senior-dephx shapes how well it does it.

senior-dephx is a set of written guidelines I developed from my own work as a software engineer, packaged so that an AI agent can follow them. It covers the habits that separate code that works from code that a team can live with for years: reading the real code before changing it instead of recalling it, finding what already exists before adding something new, tracing what else a change touches, keeping each change as small as the problem, fitting the project's existing structure, checking how a change behaves when the data grows, reviewing user-facing work through the eyes of a first-time user, and proving that something works before claiming it does. There are ten guides in all, from code comprehension and engineering standards to scale review, UI craft and git discipline, plus a short core gate that sets the bar for everything else.

The guides are about 40,000 tokens together, too many to send with every request, especially on a 65,536-token window. dephx-bot puts only a short index of them in the system prompt and gives the agent a `load_skill` tool to read a guide when the task in front of it calls for one. A loaded guide is pinned, so trimming and compaction never drop it. The chat shows that a guide was loaded and how large it was, but not its text.

The effect shows in the agent's output. In testing, its final reports consistently separated what it had verified from what it had not. In one run it tried to prove that its new test could actually fail by briefly breaking the code it had just written, which is a habit straight from the guidelines; the command was not on the allow-list, so it was refused and the agent carried on.

My working estimate, from using senior-dephx on my own projects, is that this combination of enforced process and written judgement makes AI-assisted development somewhere between 50 and 200 times more productive than prompting a model directly. The gain is largest with weaker models, because the guidelines and the gates supply the judgement those models lack, so a model that would otherwise produce plausible but unmaintainable code is held to the same standard as a strong one. I want to be clear that this range comes from experience, not from a controlled measurement, and nothing in this report was designed to test it. Measuring it properly is the next piece of work: the same feature requests built with and without senior-dephx, on strong and weak models, judged on correctness, on how well the result fits the existing codebase, and on the engineer's time.

### 2.3 The rest of the harness

Around those two ideas are the parts any serious agent needs, each built and tested:

- **Approvals.** Every shell command is shown in full before it runs, with Run once, Always allow in this project, and Deny. Commands containing hidden or direction-changing characters are refused, so a command cannot be disguised.
- **A per-chat token limit.** Before each request the app adds up what the chat has used and the largest possible size of the next request, and refuses to send it if that would go over the limit. Spending never overshoots.
- **Trimming and compaction.** Old tool output is hidden from later requests at no cost, because the agent can run the tool again. When that is not enough, the older part of the chat is replaced by a summary in which the record of files changed, commands run and approvals given is written by the app from its own log, and only the narrative is written by the model. The user can compact on demand and undo it.
- **A strict file boundary.** The agent can only read and write inside the project folder, and never the `.git` folder or the app's own settings, with symlinks and Windows path quirks handled.
- **Stop that actually stops.** Stop closes the model's stream, cancels any open approval and kills a running command together with everything it started.

Building it on real runs, not just unit tests, found nine bugs that the tests had missed, and a fresh review of the whole codebase found eleven more. All of them are fixed, most with a test that failed first. The Python side has 195 tests and the interface 27.

## 3. Integrating Praxiom

### 3.1 Interoperability

Praxiom Cloud says you can use your existing OpenAI client libraries by changing the base URL. I tested that claim with a probe that drives the API through the official OpenAI Python SDK, changing only the address, key and model name, with the SDK's automatic retries turned off so that I saw the raw behaviour. It ran twice per provider with the same results.

| What was checked | Praxiom | DeepSeek |
| --- | --- | --- |
| Listing models, basic chat, text content parts | pass | pass |
| A reply cut off by `max_tokens` reports `finish_reason: length` | pass | pass |
| JSON mode returns valid JSON | pass | pass |
| The model chooses to call a tool, with valid arguments | pass | pass |
| A full tool call, tool result and final answer | pass | pass |
| Forcing a specific tool with `tool_choice` | pass | **fail** |
| Suppressing tools with `tool_choice: none` | pass | pass |
| Streaming text, with usage in the final chunk | pass | pass |
| Streaming a tool call and reassembling it | pass | pass |
| An unknown model and a bad key raise the SDK's usual errors | pass | pass |
| **Total** | **13 of 13** | **12 of 13** |

Praxiom also made two parallel tool calls in one reply when asked for the weather in two cities, and followed a system instruction to reply with a single word exactly.

Two things went beyond compatibility. Praxiom's error codes are specific (`model_not_supported`, `invalid_api_key`, `concurrency_limit`, `idempotency_replay`), where DeepSeek returns `invalid_request_error` for different failures, so a client can act on Praxiom's errors directly. And Praxiom accepts a forced `tool_choice`, which DeepSeek refuses in its default thinking mode. Forcing a tool is how an agent asks for a structured answer at a particular step, so this is a real advantage for agent builders.

### 3.2 What the integration needed

Very little. dephx-bot has one provider client for every model, and Praxiom needed only a record of its limits and prices: the 65,536-token window, about 3,000 tokens for its own prompt, an 8,192-token maximum reply, two requests at a time, and ESP 0.276 and ESP 1.101 per million input and output tokens. Everything else is shared with DeepSeek and with any custom endpoint a user configures.

Praxiom's documentation was accurate everywhere I checked it. Three documented behaviours shaped the client, and each was easy to handle once known:

- **Two requests at a time per account.** A third gets `429 concurrency_limit` with a `Retry-After` header. The client holds a gate of two, so parallel work queues instead of failing.
- **Idempotency keys.** Repeating a request with the same `Idempotency-Key` is never charged twice, but the second response is `409 idempotency_replay` without the original answer. The OpenAI SDK retries 409 by default, so I wrote my own retry policy: retry 429 and 503 using `Retry-After`, and never retry a 409.
- **Failed requests are free.** Requests rejected with 400, 402, 429 or 503 cost nothing, which billing confirmed for the rejected image requests. This makes it safe to probe the API's limits.

## 4. How Praxiom behaves

Across all the testing, a few traits set Praxiom apart from the other models I have built with.

**It answers without hidden reasoning, and that makes it economical.** Reasoning models such as DeepSeek in its default mode think before they answer and bill that thinking as output. Praxiom goes straight to the answer. For the same five coding tasks it produced 4,951 output tokens against DeepSeek's 12,149, and since output tokens cost four times as much as input on both platforms, this is Praxiom's biggest cost advantage.

**It starts quickly.** Its median time to the first token was 0.94 seconds against DeepSeek's 1.50, and DeepSeek reached 3.5 seconds in an earlier set of runs. For a coding tool where the user watches every step begin, this is noticeable.

**It keeps its own identity.** Praxiom always runs its own system prompt, and the instructions an app sends are applied on top of it as developer instructions. They set the task, format and tone but do not replace Praxiom's persona or values. In practice this meant writing dephx-bot's prompt as a description of the job rather than as an identity, which is better practice anyway, and the agent followed it well.

**It is concise in what it writes.** It wrote 52 tests of its own across the five coding tasks where DeepSeek wrote 78, and all of its code passed the hidden tests. Fewer, well-aimed tests are not a weakness, though an agent that needs broad coverage should ask for it explicitly.

**It streams in larger pieces.** A short reply arrived in 7 chunks where DeepSeek sent 51. This makes no difference to correctness, but an interface should not assume a smooth character-by-character stream.

**It shares the common failure of skipping steps.** Like DeepSeek, it tried to move from investigating to editing without a plan. This is a property of today's models in general, and it is exactly what the phase engine is for.

## 5. Raw intelligence

### 5.1 The malaria agent

Before building dephx-bot I wrote a small clinical decision-support agent for malaria, with one tool that reads a web page and summarises it. It was written against DeepSeek and then run on Praxiom by changing one setting, with no code changes.

The test question was a realistic one: someone who returned to Lagos from Kano ten days ago and has had fever, chills and headache for three days. Praxiom decided on its own to read the World Health Organization's malaria fact sheet, then wrote an answer that I would be comfortable showing a patient. It:

- worked out that the timing fits malaria's incubation period, and explained why;
- listed the danger signs of severe malaria first, with a clear instruction to go to an emergency department if any are present;
- insisted on a blood test before any treatment, in line with WHO guidance;
- warned that a negative rapid test does not rule malaria out, because parasites with pfhrp2 and pfhrp3 gene deletions, which have been documented in Nigeria, can be missed, and advised a repeat test;
- named the other causes a clinician would consider, including meningitis for someone coming from northern Nigeria;
- explained that artemisinin-based combination therapy is the standard treatment but left the choice and dose to a clinician, and warned against self-medicating or taking a borrowed course;
- cited its source.

That is medical reasoning grounded in local context. The deletion warning in particular is a detail many general answers miss.

### 5.2 Coding

On the five coding tasks below, Praxiom passed every hidden test with code written at the first attempt. The tasks were small and single-file, and DeepSeek also passed all five, so this shows that Praxiom is fully capable at everyday coding work rather than ranking the two. Harder tasks are needed to separate them.

### 5.3 Images

The malaria agent also produced the image test: a flat illustration of a community health worker in a Nigerian village clinic doing a malaria rapid test, followed by an edit asking for the same scene at night lit by a lantern.

<p>
  <img src="docs/images/praxiom-image-generate.jpg" alt="Praxiom illustration of a health worker testing a patient for malaria in a village clinic" width="320">
  <img src="docs/images/praxiom-image-edit.jpg" alt="The same illustration after the edit request" width="320">
</p>

Both endpoints worked through the OpenAI SDK's standard image calls, a 1K image took 8.5 seconds and an edit 19.5, and billing matched the price list exactly: ESP 0.04 per image and ESP 0.043 for the edit. The scene, setting and style are good. As with most image models at this level, written text in the picture is garbled, there is an extra hand, and the health worker holds a syringe-like pen rather than a test kit. The edit kept the composition almost pixel for pixel but applied a heavy filter instead of making the scene night-time. These were single samples, so they are impressions rather than measurements. For illustration the generator is usable today; for anything that must be accurate, such as patient material, a person should check every image.

## 6. Benchmark results

All numbers come from the providers' own usage reports, collected by a local meter that every request passes through. A hidden test that the model never saw decided whether each task was done. The coding tasks were run through Aider, a widely used open-source coding assistant, with the same settings for both models, so that the comparison is about the models and not the harness. Everything was run on Wednesday 21 October 2026 between 21:00 and 22:00 UTC, which is DeepSeek's off-peak period.

### Coding tasks

| Task | What the model had to do | Praxiom | DeepSeek | Praxiom time | DeepSeek time |
| --- | --- | --- | --- | --- | --- |
| slugify | Write a URL slug function that strips accents, with tests | passed | passed | 12.5 s | 25.9 s |
| cart-bug | Find and fix a bug in an existing shopping cart, with a regression test | passed | passed | 12.2 s | 11.3 s |
| low-stock | Add a low-stock report to an existing inventory module | passed | passed | 9.2 s | 10.6 s |
| roman | Convert to and from Roman numerals, rejecting invalid forms | passed | passed | 21.3 s | 16.1 s |
| durations | Parse and format durations such as `1h30m`, with error handling | passed | passed | 15.2 s | 20.3 s |
| **All five** | | **5 of 5** | **5 of 5** | **70.4 s** | **84.2 s** |

### Speed

The same short question, three times each, streamed.

| | Praxiom | DeepSeek |
| --- | --- | --- |
| Time to first token, median | **0.94 s** | 1.50 s |
| Time to the whole answer, median | 2.29 s | 2.36 s |
| Visible output speed, median | 186 tokens/s | 282 tokens/s |

### Cost

| | Praxiom | DeepSeek off-peak | DeepSeek peak |
| --- | --- | --- | --- |
| Price per million input tokens | ESP 0.276 (about $0.42) | $0.15 | $0.30 |
| Price per million cached input tokens | not offered | $0.003 | $0.006 |
| Price per million output tokens | ESP 1.101 (about $1.68) | $0.60 | $1.20 |
| All five coding tasks | **₦31.94** (ESP 0.0157) | ₦10.17 | about ₦20 |
| Output tokens for the five tasks | **4,951** | 12,149 | 12,149 |

Espees are converted at ₦2,030 per ESP and dollars at ₦1,332.68.

### An agent run on Praxiom

dephx-bot fixed the cart-bug task on Praxiom in 13 model requests: it read the code, planned, fixed the bug and added a regression test, and the hidden test passed. It reached its 120,000-token limit before it got to the Done phase, so the app stopped it without marking the work verified, which is the intended behaviour. The run cost ESP 0.0311 (about ₦63). About 39,000 of its 102,909 input tokens, 38%, were Praxiom's hidden prompt repeated on each request.

## 7. Merits and shortcomings

### What Praxiom does well

- Complete OpenAI compatibility for agent work, including forced tool choice and streamed tool calls, verified through the official SDK.
- Specific, documented error codes, and documentation that matched the API's behaviour in every case I checked.
- No charge for failed requests, and idempotency keys that prevent double billing.
- Reliable first-attempt code on everyday tasks, and careful, context-aware reasoning on a medical question.
- Fast first token and lean output, which keeps interactive tools responsive and output costs down.
- Billing that matched the published prices to the last decimal in every test.

### Where it holds agent builders back, and what would help

These are listed in the order I expect them to matter for agent workloads.

1. **No cached-input price.** Almost every request in an agent loop repeats the system prompt, the tool list and the conversation so far. DeepSeek bills that repeated part at a fiftieth of its normal input price, and across five dephx-bot runs on DeepSeek, three quarters of all input was billed that way. Prefix caching reported in the usual `prompt_tokens_details.cached_tokens` field would probably lower Praxiom's agent costs more than any change to its headline prices, and existing tools would pick it up without changes.
2. **The hidden prompt on every request.** About 3,000 tokens is a fair price for a consistent identity in a chat app, but an agent sends one request per step, and in my agent run it was over a third of all input. A shorter prompt for API use, or caching it, would help. It also takes about 5% of the context window on every request.
3. **The context window.** At 65,536 tokens, including the hidden prompt, a long agent session needs compaction sooner than on larger-window models. dephx-bot handles this, but a larger window or one that excluded the provider's own prompt would let agents work longer between summaries.
4. **Two requests at a time.** Enough for one agent, tight for tools that run several in parallel. A higher limit on paid tiers would suit agent platforms.
5. **The image models** need work on written text and anatomy, and the edit endpoint did not follow the instruction it was given. The video model and the planned `praxiom-code` model were not tested.

I would also make one point in Praxiom's documentation: because it does not bill hidden reasoning, it reaches the same result with far fewer output tokens than reasoning models, and output is the expensive side of the bill.

## 8. Limits of this testing

- The coding tasks were small and single-file, each run once per model. Every run passed, so they show parity on everyday work, not a ranking.
- Speed was measured three times per model from one location at one time of day.
- The image tests were one generation and one edit.
- The malaria agent is a learning project, not a medical device, and its answer was judged by reading it, not by a clinician.
- dephx-bot has not yet been run on a real Windows machine, and the productivity estimate in section 2.2 has not been measured.

## 9. Cost of this testing

The image tests, both dephx-bot runs on Praxiom and the benchmark together took the Praxiom balance from ESP 0.966747 to ESP 0.804259, a spend of ESP 0.16, about ₦330. The compatibility probes and the first malaria runs came before that and were not measured separately; they were a handful of short requests. The DeepSeek side of the benchmark cost under ten US cents.
