import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from dephx_bot.agent.approvals import ApprovalRequest, Decision
from dephx_bot.settings.project import Project
from dephx_bot.tools import (
    HEAD_CHARS,
    MAX_TOOL_OUTPUT_CHARS,
    RUN_COMMAND,
    TAIL_CHARS,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    string_arg,
)

COMMAND_TIMEOUT_SECONDS = 120
POLL_SECONDS = 0.2
# Memory is bounded by reading only the head and tail; this bounds the disk.
MAX_OUTPUT_BYTES = 50_000_000


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str
    timed_out: bool
    cancelled: bool
    output_capped: bool = False


async def run_shell(
    command: str, cwd: Path, *, timeout: float, cancel: asyncio.Event, env: Mapping[str, str] | None = None
) -> CommandResult:
    # Output goes to a temp file, not a pipe: a chatty process can fill a pipe and block forever.
    with tempfile.TemporaryFile() as output:
        process = _start(command, cwd, output, env)
        deadline = asyncio.get_running_loop().time() + timeout
        timed_out = cancelled = capped = False
        while process.poll() is None:
            if cancel.is_set():
                cancelled = True
            elif asyncio.get_running_loop().time() > deadline:
                timed_out = True
            elif os.fstat(output.fileno()).st_size > MAX_OUTPUT_BYTES:
                capped = True
            if timed_out or cancelled or capped:
                kill_tree(process)
                process.wait()
                break
            await asyncio.sleep(POLL_SECONDS)

        exit_code = None if timed_out or cancelled or capped else process.returncode
        return CommandResult(exit_code, _head_and_tail(output), timed_out, cancelled, capped)


def kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """Kill the shell and everything it started, so a dev server cannot outlive Stop."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def make_shell_tool(project: Project, env: Mapping[str, str] | None = None) -> Tool:
    async def run_command(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = string_arg(args, "command").strip()
        if not command:
            raise ToolError("The command is empty.")
        hidden = _hidden_characters(command)
        if hidden:
            # The user approves what they see; invisible or direction-changing characters would hide what runs.
            raise ToolError(f"The command contains hidden characters ({hidden}). Rewrite it with plain text.")

        if not project.is_allowed(command):
            answer = await ctx.request_approval(ApprovalRequest("command", command))
            if answer.decision == Decision.DENY:
                return ToolResult("The user denied this command.", ok=False)
            if answer.decision == Decision.ALWAYS:
                project.allow_always(command)

        result = await run_shell(command, project.root, timeout=COMMAND_TIMEOUT_SECONDS, cancel=ctx.cancel, env=env)
        if result.cancelled:
            status = "The user stopped the command."
        elif result.timed_out:
            status = f"The command was stopped after {COMMAND_TIMEOUT_SECONDS} seconds."
        elif result.output_capped:
            status = f"The command was stopped after printing more than {MAX_OUTPUT_BYTES // 1_000_000} MB."
        else:
            status = f"Command exited with code {result.exit_code}."
        passed = result.exit_code == 0
        # Labelled: a bare "exit code 0" first line was read by the model as the program's own output.
        return ToolResult(f"{status}\n--- output ---\n{result.output}", ok=passed, check_succeeded=passed)

    return Tool(
        RUN_COMMAND,
        "Run a shell command in the project folder. The user approves each command. "
        f"Commands are stopped after {COMMAND_TIMEOUT_SECONDS} seconds, so do not start servers that keep running.",
        {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The exact command line to run."}},
            "required": ["command"],
        },
        run_command,
    )


def _start(command: str, cwd: Path, output: IO[bytes], env: Mapping[str, str] | None) -> "subprocess.Popen[bytes]":
    # A new process group (POSIX) or console group (Windows) lets kill_tree reach grandchildren.
    if sys.platform == "win32":
        return subprocess.Popen(
            command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, env=env,
        )
    return subprocess.Popen(
        command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output,
        stderr=subprocess.STDOUT, start_new_session=True, env=env,
    )


def _hidden_characters(command: str) -> str:
    """Control and format characters (bidi overrides, zero-width spaces), named as U+XXXX."""
    found = {ch for ch in command if unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\t\n\r"}
    return ", ".join(f"U+{ord(ch):04X}" for ch in sorted(found))


def _head_and_tail(output: IO[bytes]) -> str:
    """Read only the start and end of the output, so megabytes of logs never reach memory."""
    size = output.seek(0, os.SEEK_END)
    output.seek(0)
    if size <= MAX_TOOL_OUTPUT_CHARS:
        return output.read().decode("utf-8", errors="replace")
    head = output.read(HEAD_CHARS).decode("utf-8", errors="replace")
    output.seek(size - TAIL_CHARS)
    tail = output.read().decode("utf-8", errors="replace")
    return f"{head}\n[... {size - HEAD_CHARS - TAIL_CHARS} bytes elided ...]\n{tail}"
