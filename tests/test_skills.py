import asyncio

import pytest

from dephx_bot.agent.approvals import ApprovalAnswer, ApprovalRequest
from dephx_bot.agent.phases import Phase
from dephx_bot.agent.prompt import build_system_prompt, phase_note
from dephx_bot.config import PACKAGE_DIR
from dephx_bot.model import estimate_tokens
from dephx_bot.tools import ToolContext, ToolError
from dephx_bot.tools.skills import SkillLibrary, make_skill_tool

SKILLS_DIR = PACKAGE_DIR / "skills" / "senior-dephx"


async def never_asked(_request: ApprovalRequest) -> ApprovalAnswer:
    raise AssertionError("load_skill never asks for approval")


@pytest.fixture
def library() -> SkillLibrary:
    return SkillLibrary(SKILLS_DIR)


def test_vendored_snapshot_is_pinned() -> None:
    assert (SKILLS_DIR / "VERSION").read_text().strip() == "senior-dephx 0.10.0 @ 80fd75f"


def test_index_lists_vendored_skills(library: SkillLibrary) -> None:
    names = {skill.name for skill in library.index()}
    assert {"engineering-standards", "verify-for-real", "ui-craft", "software-engineer-intuition"} <= names
    assert all(skill.description for skill in library.index())


def test_gate_is_the_core_text(library: SkillLibrary) -> None:
    assert "Correctness is the floor" in library.gate()


def test_load_returns_skill_text(library: SkillLibrary) -> None:
    assert "A build is not a verification." in library.load("verify-for-real")


def test_load_unknown_skill_lists_names(library: SkillLibrary) -> None:
    with pytest.raises(ToolError, match="engineering-standards"):
        library.load("nope")


def test_load_rejects_path_tricks(library: SkillLibrary) -> None:
    with pytest.raises(ToolError):
        library.load("../core")


@pytest.mark.anyio
async def test_skill_tool_result_is_pinned(library: SkillLibrary) -> None:
    tool = make_skill_tool(library)
    result = await tool.run({"name": "verify-for-real"}, ToolContext(never_asked, asyncio.Event()))
    assert result.pinned
    assert result.output == library.load("verify-for-real")


def test_prompt_contains_gate_index_phases_and_os(library: SkillLibrary) -> None:
    prompt = build_system_prompt(library=library, os_name="Windows", shell_name="cmd.exe")
    assert "Correctness is the floor" in prompt
    assert "verify-for-real" in prompt
    assert "cmd.exe" in prompt and "Windows" in prompt
    assert "webServer" in prompt and "error-context.md" in prompt
    for phase in Phase:
        assert phase.value in prompt


def test_prompt_stays_small(library: SkillLibrary) -> None:
    prompt = build_system_prompt(library=library, os_name="macOS", shell_name="/bin/sh")
    assert estimate_tokens(prompt) < 3_500


def test_phase_note_lists_only_allowed_tools() -> None:
    note = phase_note(Phase.EVALUATE)
    assert "evaluate" in note
    assert "read_file" in note
    assert "write_file" not in note


def test_phase_note_in_done_asks_for_the_report() -> None:
    assert "final report" in phase_note(Phase.DONE)


def test_prompt_says_how_to_ask_for_plan_approval(library: SkillLibrary) -> None:
    prompt = build_system_prompt(library=library, os_name="macOS", shell_name="/bin/sh")
    assert "Do not ask for approval in plain text" in prompt
