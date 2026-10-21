import asyncio
import os
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from dephx_bot.agent.approvals import ApprovalAnswer, ApprovalRequest
from dephx_bot.tools import MAX_TOOL_OUTPUT_CHARS, Tool, ToolContext, ToolError, ToolResult, cap_output
from dephx_bot.tools.files import ProjectPaths, is_within, make_file_tools

pytestmark = pytest.mark.anyio

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"


async def never_asked(_request: ApprovalRequest) -> ApprovalAnswer:
    raise AssertionError("file tools never ask for approval")


CONTEXT = ToolContext(request_approval=never_asked, cancel=asyncio.Event())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def hello():\n    return 'hi'\n")
    (root / "README.md").write_text("# Demo\nhello world\n")
    return root


@pytest.fixture
def paths(project: Path) -> ProjectPaths:
    return ProjectPaths(project)


@pytest.fixture
def tools(paths: ProjectPaths) -> dict[str, Tool]:
    return {tool.name: tool for tool in make_file_tools(paths)}


async def run(tools: dict[str, Tool], name: str, **args: Any) -> ToolResult:
    return await tools[name].run(args, CONTEXT)


# --- the path rule ---------------------------------------------------------------


def test_relative_path_inside_is_allowed(paths: ProjectPaths, project: Path) -> None:
    assert paths.resolve("src/app.py") == (project / "src" / "app.py").resolve()


def test_dotdot_escape_refused(paths: ProjectPaths) -> None:
    with pytest.raises(ToolError, match="outside the project"):
        paths.resolve("../secret.txt")


def test_absolute_path_outside_refused(paths: ProjectPaths) -> None:
    with pytest.raises(ToolError, match="outside the project"):
        paths.resolve("/etc/passwd")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
def test_symlink_escape_refused(tmp_path: Path, paths: ProjectPaths, project: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    os.symlink(outside, project / "link")
    with pytest.raises(ToolError, match="outside the project"):
        paths.resolve("link/secret.txt")


@pytest.mark.parametrize(
    "path", [".git/hooks/pre-commit", ".dephx/settings.json", ".GIT/config", "sub/.git/hooks/x"]
)
def test_protected_dirs_refused(paths: ProjectPaths, path: str) -> None:
    with pytest.raises(ToolError, match="protected"):
        paths.resolve(path)


def test_windows_paths_compare_case_insensitively() -> None:
    assert is_within(PureWindowsPath(r"C:\Proj\src\a.py"), PureWindowsPath(r"c:\proj"))
    assert not is_within(PureWindowsPath(r"D:\proj\a.py"), PureWindowsPath(r"C:\proj"))
    assert not is_within(PureWindowsPath(r"C:\projects\a.py"), PureWindowsPath(r"C:\proj"))


def test_posix_paths_compare_by_whole_parts() -> None:
    assert is_within(PurePosixPath("/a/proj/x"), PurePosixPath("/a/proj"))
    assert not is_within(PurePosixPath("/a/project2/x"), PurePosixPath("/a/proj"))


# --- the tools --------------------------------------------------------------------


async def test_list_files_skips_protected_and_node_modules(tools: dict[str, Tool], project: Path) -> None:
    (project / ".git").mkdir()
    (project / "node_modules" / "x").mkdir(parents=True)
    result = await run(tools, "list_files")
    assert "src/" in result.output
    assert "src/app.py" in result.output
    assert ".git" not in result.output
    assert "node_modules" not in result.output


async def test_read_file_numbers_lines(tools: dict[str, Tool]) -> None:
    result = await run(tools, "read_file", path="src/app.py")
    assert "1| def hello():" in result.output
    assert "2|     return 'hi'" in result.output


async def test_read_file_offset_and_limit(tools: dict[str, Tool], project: Path) -> None:
    (project / "long.txt").write_text("".join(f"line {i}\n" for i in range(1, 11)))
    result = await run(tools, "read_file", path="long.txt", offset=3, limit=2)
    assert "4| line 4" in result.output
    assert "5| line 5" in result.output
    assert "line 6" not in result.output


async def test_read_binary_file_is_clear_error(tools: dict[str, Tool], project: Path) -> None:
    (project / "logo.png").write_bytes(PNG_BYTES)
    with pytest.raises(ToolError, match="binary file"):
        await run(tools, "read_file", path="logo.png")


async def test_read_missing_file_is_clear_error(tools: dict[str, Tool]) -> None:
    with pytest.raises(ToolError, match="no such file"):
        await run(tools, "read_file", path="nope.py")


async def test_search_finds_lines(tools: dict[str, Tool]) -> None:
    result = await run(tools, "search_text", pattern="hello")
    assert "src/app.py:1:" in result.output
    assert "README.md:2:" in result.output


async def test_search_skips_binary_and_protected(tools: dict[str, Tool], project: Path) -> None:
    (project / "logo.png").write_bytes(PNG_BYTES + b"hello")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("hello")
    result = await run(tools, "search_text", pattern="hello")
    assert "logo.png" not in result.output
    assert ".git" not in result.output


async def test_write_creates_parents_and_reports_write(tools: dict[str, Tool], project: Path) -> None:
    result = await run(tools, "write_file", path="new/dir/x.py", content="print(1)\n")
    assert result.wrote_file
    assert (project / "new" / "dir" / "x.py").read_text() == "print(1)\n"


async def test_write_into_protected_dir_refused(tools: dict[str, Tool]) -> None:
    with pytest.raises(ToolError, match="protected"):
        await run(tools, "write_file", path=".dephx/settings.json", content="{}")


async def test_edit_replaces_single_match(tools: dict[str, Tool], project: Path) -> None:
    result = await run(tools, "edit_file", path="src/app.py", old="'hi'", new="'hello'")
    assert result.wrote_file
    assert "return 'hello'" in (project / "src" / "app.py").read_text()


async def test_edit_requires_single_match(tools: dict[str, Tool], project: Path) -> None:
    (project / "twice.txt").write_text("a\na\n")
    with pytest.raises(ToolError, match="found 2 times"):
        await run(tools, "edit_file", path="twice.txt", old="a", new="b")
    with pytest.raises(ToolError, match="not found"):
        await run(tools, "edit_file", path="twice.txt", old="zzz", new="b")


async def test_missing_argument_is_clear_error(tools: dict[str, Tool]) -> None:
    with pytest.raises(ToolError, match="path"):
        await run(tools, "read_file")


def test_schemas_are_openai_function_tools(tools: dict[str, Tool]) -> None:
    schema = tools["read_file"].schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert schema["function"]["parameters"]["required"] == ["path"]


def test_cap_output_keeps_head_and_tail() -> None:
    text = "H" * 5_000 + "M" * 20_000 + "T" * 10_000
    capped = cap_output(text)
    assert len(capped) < MAX_TOOL_OUTPUT_CHARS + 100
    assert capped.startswith("H" * 3_000)
    assert capped.endswith("T" * 9_000)
    assert "elided" in capped
    assert cap_output("short") == "short"


async def test_edit_keeps_windows_line_endings(tools: dict[str, Tool], project: Path) -> None:
    (project / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    await run(tools, "edit_file", path="crlf.txt", old="one\ntwo", new="uno\ndos")
    assert (project / "crlf.txt").read_bytes() == b"uno\r\ndos\r\nthree\r\n"


async def test_write_keeps_the_given_line_endings(tools: dict[str, Tool], project: Path) -> None:
    await run(tools, "write_file", path="lf.txt", content="a\nb\n")
    assert (project / "lf.txt").read_bytes() == b"a\nb\n"


async def test_writing_onto_a_folder_is_a_tool_error(tools: dict[str, Tool]) -> None:
    with pytest.raises(ToolError):
        await run(tools, "write_file", path="src", content="x")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
async def test_search_does_not_follow_links_out_of_the_project(tmp_path: Path, tools: dict[str, Tool], project: Path) -> None:
    (tmp_path / "secret.txt").write_text("AWS_SECRET=abc123\n")
    os.symlink(tmp_path / "secret.txt", project / "notes.txt")
    result = await run(tools, "search_text", pattern="AWS")
    assert "abc123" not in result.output


async def test_search_needs_a_pattern(tools: dict[str, Tool]) -> None:
    with pytest.raises(ToolError, match="pattern"):
        await run(tools, "search_text", pattern="")


async def test_search_skips_environment_and_build_folders(tools: dict[str, Tool], project: Path) -> None:
    for folder in (".venv/lib", "dist", "__pycache__"):
        (project / folder).mkdir(parents=True)
        (project / folder / "x.py").write_text("hello")
    result = await run(tools, "search_text", pattern="hello")
    assert ".venv" not in result.output and "dist" not in result.output and "__pycache__" not in result.output


async def test_very_large_file_is_refused(tools: dict[str, Tool], project: Path) -> None:
    (project / "big.log").write_text("x" * 3_000_000)
    with pytest.raises(ToolError, match="too large"):
        await run(tools, "read_file", path="big.log")


@pytest.mark.parametrize("path", [".git./hooks/pre-commit", ".dephx /settings.json"])
def test_windows_trailing_dots_and_spaces_cannot_reach_protected_dirs(paths: ProjectPaths, path: str) -> None:
    with pytest.raises(ToolError, match="protected"):
        paths.resolve(path)
