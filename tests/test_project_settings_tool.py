import asyncio
import json
from pathlib import Path

import pytest

from dephx_bot.agent.approvals import ApprovalAnswer, ApprovalRequest, Decision
from dephx_bot.agent.phases import PHASE_TOOLS, Phase
from dephx_bot.settings.project import open_project
from dephx_bot.tools import EDIT_PROJECT_SETTINGS, ToolContext, ToolError
from dephx_bot.tools.project_settings import make_project_settings_tool

pytestmark = pytest.mark.anyio


class Approver:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.asked: list[ApprovalRequest] = []

    async def __call__(self, request: ApprovalRequest) -> ApprovalAnswer:
        self.asked.append(request)
        return ApprovalAnswer(self.decision)


def context(approver: Approver) -> ToolContext:
    return ToolContext(request_approval=approver, cancel=asyncio.Event())


def saved(root: Path) -> list[str]:
    allowed: list[str] = json.loads((root / ".dephx" / "settings.json").read_text())["allowed_commands"]
    return allowed


async def test_an_applied_change_is_saved_and_takes_effect(tmp_path: Path) -> None:
    project = open_project(str(tmp_path))
    approver = Approver(Decision.APPROVE)
    result = await make_project_settings_tool(project).run({"allow": ["uv run pytest"]}, context(approver))

    assert result.ok
    assert approver.asked[0].kind == "settings"
    assert "+ uv run pytest" in approver.asked[0].text
    assert saved(tmp_path) == ["uv run pytest"]
    assert project.is_allowed("uv run pytest")


async def test_a_denied_change_writes_nothing(tmp_path: Path) -> None:
    project = open_project(str(tmp_path))
    result = await make_project_settings_tool(project).run({"allow": ["make"]}, context(Approver(Decision.DENY)))

    assert not result.ok
    assert not (tmp_path / ".dephx").exists()
    assert not project.is_allowed("make")


async def test_removing_a_command_stops_it_running_without_asking(tmp_path: Path) -> None:
    project = open_project(str(tmp_path))
    project.allow_always("npm test")
    approver = Approver(Decision.APPROVE)
    await make_project_settings_tool(project).run({"remove": ["npm test"]}, context(approver))

    assert "- npm test" in approver.asked[0].text
    assert saved(tmp_path) == []
    assert not project.is_allowed("npm test")


async def test_a_change_that_changes_nothing_is_not_shown(tmp_path: Path) -> None:
    approver = Approver(Decision.APPROVE)
    with pytest.raises(ToolError, match="nothing to change"):
        await make_project_settings_tool(open_project(str(tmp_path))).run({"remove": ["ls"]}, context(approver))
    assert approver.asked == []


async def test_commands_with_hidden_characters_are_refused(tmp_path: Path) -> None:
    approver = Approver(Decision.APPROVE)
    with pytest.raises(ToolError, match="hidden characters"):
        await make_project_settings_tool(open_project(str(tmp_path))).run({"allow": ["ls‮"]}, context(approver))
    assert approver.asked == []


def test_the_tool_is_offered_in_every_working_phase() -> None:
    assert all(EDIT_PROJECT_SETTINGS in PHASE_TOOLS[p] for p in Phase if p is not Phase.DONE)
