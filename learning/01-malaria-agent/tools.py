"""Tools the agent may call.

A tool has two halves:
- a JSON schema the model reads, so it knows the tool exists and what arguments to send;
- a Python function we run when the model asks for it.

The model never runs anything itself. It only asks; our code decides and executes.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam

from prompts import SUMMARY_PROMPT

FETCH_TIMEOUT_SECONDS = 15.0
MAX_PAGE_BYTES = 2_000_000
# Cap on page text sent to the summariser, to keep its request small and cheap.
MAX_TEXT_CHARS = 30_000
USER_AGENT = "praxium-learning-agent/0.1"


class ToolError(Exception):
    """A problem worth telling the model about, so it can recover and try again."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments
    run: Callable[[dict[str, Any]], str]

    def schema(self) -> ChatCompletionToolParam:
        """The OpenAI-format definition sent to the model with every request."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# --- read_webpage --------------------------------------------------------------


def make_read_webpage_tool(client: OpenAI, model: str) -> Tool:
    """Build the read_webpage tool. It needs the LLM client to write its summary."""

    def run(args: dict[str, Any]) -> str:
        url = str(args.get("url", ""))
        focus = str(args.get("focus", "malaria diagnosis"))
        text = fetch_page_text(url)
        return summarise(client, model, text, focus)

    return Tool(
        name="read_webpage",
        description=(
            "Fetch a public web page and return a short summary of the parts relevant "
            "to a focus question. Use for current guidance from WHO, CDC or health agencies."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full http(s) URL of the page."},
                "focus": {
                    "type": "string",
                    "description": "What to look for, e.g. 'danger signs of severe malaria'.",
                },
            },
            "required": ["url", "focus"],
        },
        run=run,
    )


def fetch_page_text(url: str) -> str:
    """Download a page and strip it down to readable text."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ToolError("Only http and https URLs are allowed.")

    # The hook runs before every request, including each redirect hop,
    # so a public URL cannot redirect us into the local network.
    with httpx.Client(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_block_private_hosts]},
    ) as http:
        try:
            response = http.get(url)
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not fetch {url}: {exc}") from exc

    if response.status_code >= 400:
        raise ToolError(f"{url} answered with HTTP {response.status_code}.")
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        raise ToolError(f"{url} is not a text page (content-type: {content_type}).")
    # Checked after download for simplicity; a streamed read could stop early instead.
    if len(response.content) > MAX_PAGE_BYTES:
        raise ToolError(f"{url} is too large to read.")

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    if not text:
        raise ToolError(f"{url} has no readable text.")
    return text[:MAX_TEXT_CHARS]


def summarise(client: OpenAI, model: str, text: str, focus: str) -> str:
    """A second, separate LLM call. Only its short summary enters the agent's memory,
    not the whole page, which keeps the main conversation small."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": f"Focus: {focus}\n\nPage text:\n{text}"},
        ],
        max_tokens=600,
    )
    return response.choices[0].message.content or "The summariser returned nothing."


def _block_private_hosts(request: httpx.Request) -> None:
    host = request.url.host
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ToolError(f"Could not resolve host {host}.") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ToolError(f"Blocked: {host} points to a private or local address.")
