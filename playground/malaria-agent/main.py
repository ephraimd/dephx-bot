"""Command-line chat with the malaria agent.

    uv run python main.py                      # interactive chat
    uv run python main.py "your question"      # one question, then exit
    PROVIDER=praxiom uv run python main.py     # same agent, different provider
"""

import sys

from openai import OpenAI

from agent import Agent
from config import load_settings
from prompts import SYSTEM_PROMPT
from tools import make_read_webpage_tool


def build_agent() -> Agent:
    """Wire everything together in one place."""
    settings = load_settings()
    client = OpenAI(base_url=settings.base_url, api_key=settings.api_key)
    tools = [make_read_webpage_tool(client, settings.model)]
    return Agent(client, settings.model, SYSTEM_PROMPT, tools)


def main() -> None:
    agent = build_agent()

    if len(sys.argv) > 1:
        print(agent.ask(" ".join(sys.argv[1:])))
        return

    print(f"Malaria assistant ({agent.model}). Type 'exit' to quit.")
    while True:
        try:
            user_text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_text.lower() in ("exit", "quit"):
            break
        if user_text:
            print(f"\nagent> {agent.ask(user_text)}")


if __name__ == "__main__":
    main()
