import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from praxium.agent.approvals import ApprovalAnswer, ApprovalRequest, Decision
from praxium.settings.project import open_project
from praxium.tools import ToolContext
from praxium.tools.shell import make_shell_tool, run_shell

pytestmark = pytest.mark.anyio

PY = f'"{sys.executable}"'


def python(code_file: Path) -> str:
    return f'{PY} "{code_file}"'


def script(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.write_text(code)
    return path


async def test_exit_code_and_output(tmp_path: Path) -> None:
    result = await run_shell(f'{PY} -c "print(42)"', tmp_path, timeout=10, cancel=asyncio.Event())
    assert result.exit_code == 0
    assert result.output.strip() == "42"
    assert not result.timed_out and not result.cancelled


async def test_failing_command_reports_exit_code_and_stderr(tmp_path: Path) -> None:
    code = script(tmp_path, "fail.py", "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
    result = await run_shell(python(code), tmp_path, timeout=10, cancel=asyncio.Event())
    assert result.exit_code == 3
    assert "boom" in result.output


async def test_runs_in_the_project_folder(tmp_path: Path) -> None:
    result = await run_shell(f'{PY} -c "import os; print(os.getcwd())"', tmp_path, timeout=10, cancel=asyncio.Event())
    assert Path(result.output.strip()).resolve() == tmp_path.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="checks POSIX process ids; Windows is on the manual checklist")
async def test_timeout_kills_child_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    code = script(
        tmp_path,
        "parent.py",
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pid_file}', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
    )
    result = await run_shell(python(code), tmp_path, timeout=1.5, cancel=asyncio.Event())
    assert result.timed_out
    assert result.exit_code is None

    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    pytest.fail("the grandchild process survived the timeout")


async def test_cancel_stops_command(tmp_path: Path) -> None:
    cancel = asyncio.Event()
    asyncio.get_running_loop().call_later(0.3, cancel.set)
    started = time.monotonic()
    result = await run_shell(f'{PY} -c "import time; time.sleep(30)"', tmp_path, timeout=60, cancel=cancel)
    assert result.cancelled
    assert time.monotonic() - started < 3


async def test_huge_output_is_bounded(tmp_path: Path) -> None:
    code = script(tmp_path, "loud.py", "import sys\nsys.stdout.write('x' * 5_000_000)\nprint()\nprint('SUMMARY: 3 passed')\n")
    result = await run_shell(python(code), tmp_path, timeout=30, cancel=asyncio.Event())
    assert len(result.output) <= 12_100
    assert "elided" in result.output
    assert "SUMMARY: 3 passed" in result.output


# --- the run_command tool ------------------------------------------------------------


class Approver:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.asked: list[ApprovalRequest] = []

    async def __call__(self, request: ApprovalRequest) -> ApprovalAnswer:
        self.asked.append(request)
        return ApprovalAnswer(self.decision)


def context(approver: Approver) -> ToolContext:
    return ToolContext(request_approval=approver, cancel=asyncio.Event())


async def test_denied_command_not_run(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    approver = Approver(Decision.DENY)
    tool = make_shell_tool(open_project(str(tmp_path)))
    command = f'{PY} -c "open(\'ran.txt\', \'w\').write(\'x\')"'

    result = await tool.run({"command": command}, context(approver))

    assert not result.ok
    assert "denied" in result.output
    assert not marker.exists()
    assert approver.asked == [ApprovalRequest("command", command)]


async def test_run_once_runs_and_reports_check(tmp_path: Path) -> None:
    tool = make_shell_tool(open_project(str(tmp_path)))
    result = await tool.run({"command": f'{PY} -c "print(1)"'}, context(Approver(Decision.RUN_ONCE)))
    assert result.ok
    assert result.check_succeeded is True
    # Labelled, so the model cannot mistake our status line for the program's output.
    assert result.output == "Command exited with code 0.\n--- output ---\n1\n"


async def test_failing_command_is_a_failed_check(tmp_path: Path) -> None:
    tool = make_shell_tool(open_project(str(tmp_path)))
    result = await tool.run({"command": f'{PY} -c "raise SystemExit(1)"'}, context(Approver(Decision.RUN_ONCE)))
    assert not result.ok
    assert result.check_succeeded is False


async def test_always_allow_persists_then_skips_approval(tmp_path: Path) -> None:
    project = open_project(str(tmp_path))
    tool = make_shell_tool(project)
    command = f'{PY} -c "print(1)"'

    first = Approver(Decision.ALWAYS)
    await tool.run({"command": command}, context(first))
    second = Approver(Decision.DENY)
    result = await tool.run({"command": command}, context(second))

    assert result.ok
    assert second.asked == []
    saved = json.loads((tmp_path / ".praxium" / "settings.json").read_text())
    assert saved["allowed_commands"] == [command]


async def test_commands_get_the_environment_they_are_given(tmp_path: Path) -> None:
    env = {**os.environ, "PRAXIUM_MARK": "clean"}
    code = 'import os; print(os.environ.get("PRAXIUM_MARK"))'
    result = await run_shell(f"{PY} -c '{code}'" if sys.platform != "win32" else f'{PY} -c "{code}"',
                             tmp_path, timeout=10, cancel=asyncio.Event(), env=env)
    assert result.output.strip() == "clean"


async def test_shell_tool_passes_its_environment(tmp_path: Path) -> None:
    tool = make_shell_tool(open_project(str(tmp_path)), env={**os.environ, "PRAXIUM_MARK": "from-tool"})
    code = 'import os; print(os.environ.get("PRAXIUM_MARK"))'
    command = f"{PY} -c '{code}'" if sys.platform != "win32" else f'{PY} -c "{code}"'
    result = await tool.run({"command": command}, context(Approver(Decision.RUN_ONCE)))
    assert result.output.endswith("from-tool\n")
