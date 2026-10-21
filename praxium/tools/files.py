import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

from praxium.settings.project import PROJECT_SETTINGS_DIR
from praxium.tools import Tool, ToolContext, ToolError, ToolResult, int_arg, string_arg

# .git is protected because hooks written there run when the user commits.
PROTECTED_DIRS = (PROJECT_SETTINGS_DIR, ".git")
# Dependencies, environments and build output: large, generated, and rarely what the agent needs.
SKIPPED_DIRS = (
    *PROTECTED_DIRS, "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".tox", "dist", "build", "target", ".next", ".svelte-kit",
)

MAX_LIST_ENTRIES = 500
DEFAULT_READ_LINES = 400
MAX_READ_BYTES = 2_000_000
MAX_SEARCH_MATCHES = 100
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_SEARCH_LINE_CHARS = 200
BINARY_SNIFF_BYTES = 8_192


def is_within(child: PurePath, root: PurePath) -> bool:
    """Compare whole path parts; Windows paths ignore case, so C:\\Proj equals c:\\proj."""
    windows = isinstance(child, PureWindowsPath) or isinstance(root, PureWindowsPath)
    child_parts = [p.casefold() for p in child.parts] if windows else list(child.parts)
    root_parts = [p.casefold() for p in root.parts] if windows else list(root.parts)
    return child_parts[: len(root_parts)] == root_parts


class ProjectPaths:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, relative: str) -> Path:
        # resolve() follows symlinks, so a link pointing outside is caught here too.
        target = (self.root / relative).resolve()
        if not is_within(target, self.root):
            raise ToolError(f"{relative}: path is outside the project.")
        inside = target.parts[len(self.root.parts) :]
        protected = {d.casefold() for d in PROTECTED_DIRS}
        # Windows ignores trailing dots and spaces, so ".git." would open ".git".
        if any(part.rstrip(". ").casefold() in protected for part in inside):
            raise ToolError(f"{relative}: path is protected.")
        return target

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def make_file_tools(paths: ProjectPaths) -> list[Tool]:
    def list_files(args: dict[str, Any]) -> ToolResult:
        start = paths.resolve(string_arg(args, "path", "."))
        if not start.is_dir():
            raise ToolError(f"{paths.relative(start)} is not a folder.")
        entries: list[str] = []
        for folder, dirs, files in os.walk(start):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
            here = Path(folder)
            entries += [paths.relative(here / d) + "/" for d in dirs]
            entries += [paths.relative(here / f) for f in sorted(files)]
            if len(entries) >= MAX_LIST_ENTRIES:
                break
        listing = "\n".join(sorted(entries)[:MAX_LIST_ENTRIES])
        if len(entries) >= MAX_LIST_ENTRIES:
            listing += f"\n[stopped at {MAX_LIST_ENTRIES} entries, list a subfolder to see more]"
        return ToolResult(listing or "(empty folder)")

    def read_file(args: dict[str, Any]) -> ToolResult:
        relative = string_arg(args, "path")
        offset = max(0, int_arg(args, "offset", 0))
        limit = int_arg(args, "limit", DEFAULT_READ_LINES)
        lines = _read_text(paths.resolve(relative), relative).splitlines()
        shown = lines[offset : offset + limit]
        numbered = "\n".join(f"{offset + i + 1:>5}| {line}" for i, line in enumerate(shown))
        footer = f"\n[lines {offset + 1}-{offset + len(shown)} of {len(lines)}]"
        return ToolResult(numbered + footer)

    def search_text(args: dict[str, Any]) -> ToolResult:
        pattern = string_arg(args, "pattern")
        if not pattern:
            raise ToolError("Give a pattern to search for.")
        start = paths.resolve(string_arg(args, "path", "."))
        matches: list[str] = []
        for folder, dirs, files in os.walk(start):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
            for name in sorted(files):
                text = _searchable_text(paths, Path(folder) / name)
                if text is None:
                    continue
                shown_as = paths.relative(Path(folder) / name)
                for number, line in enumerate(text.splitlines(), start=1):
                    if pattern in line:
                        matches.append(f"{shown_as}:{number}: {line.strip()[:MAX_SEARCH_LINE_CHARS]}")
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            return ToolResult("\n".join(matches) + f"\n[stopped at {MAX_SEARCH_MATCHES} matches]")
        return ToolResult("\n".join(matches) or f"No matches for '{pattern}'.")

    def write_file(args: dict[str, Any]) -> ToolResult:
        relative = string_arg(args, "path")
        content = string_arg(args, "content")
        target = paths.resolve(relative)
        _write(target, relative, content)
        return ToolResult(f"Wrote {len(content)} characters to {relative}.", wrote_file=True)

    def edit_file(args: dict[str, Any]) -> ToolResult:
        relative = string_arg(args, "path")
        target = paths.resolve(relative)
        text = _read_text(target, relative)
        # Match the file's own line endings, so a Windows file stays CRLF and "\n" in old still matches.
        eol = "\r\n" if "\r\n" in text else "\n"
        old = _with_line_endings(string_arg(args, "old"), eol)
        new = _with_line_endings(string_arg(args, "new"), eol)
        count = text.count(old)
        if count == 0:
            raise ToolError(f"The old text was not found in {relative}.")
        if count > 1:
            raise ToolError(
                f"The old text was found {count} times in {relative}. Include more surrounding lines so it is unique."
            )
        _write(target, relative, text.replace(old, new, 1))
        return ToolResult(f"Edited {relative}.", wrote_file=True)

    return [
        Tool(
            "list_files",
            "List files and folders in the project, recursively. Skips .git, .praxium, dependency, "
            "environment and build folders.",
            _object({"path": _string("Folder relative to the project root. Defaults to the root.")}, []),
            _in_thread(list_files),
        ),
        Tool(
            "read_file",
            f"Read a text file with line numbers. Returns at most {DEFAULT_READ_LINES} lines unless limit is set.",
            _object(
                {
                    "path": _string("File path relative to the project root."),
                    "offset": {"type": "integer", "description": "Number of lines to skip first."},
                    "limit": {"type": "integer", "description": "Maximum number of lines to return."},
                },
                ["path"],
            ),
            _in_thread(read_file),
        ),
        Tool(
            "search_text",
            f"Find lines containing a plain-text pattern. Returns at most {MAX_SEARCH_MATCHES} matches.",
            _object(
                {
                    "pattern": _string("Exact text to look for. Not a regular expression."),
                    "path": _string("Folder to search, relative to the project root. Defaults to the root."),
                },
                ["pattern"],
            ),
            _in_thread(search_text),
        ),
        Tool(
            "write_file",
            "Create a file or replace its whole content. Prefer edit_file for small changes to existing files.",
            _object(
                {"path": _string("File path relative to the project root."), "content": _string("The full new content.")},
                ["path", "content"],
            ),
            _in_thread(write_file),
        ),
        Tool(
            "edit_file",
            "Replace one exact piece of text in a file. The old text must appear exactly once.",
            _object(
                {
                    "path": _string("File path relative to the project root."),
                    "old": _string("Exact text to replace, including enough lines to be unique."),
                    "new": _string("Replacement text."),
                },
                ["path", "old", "new"],
            ),
            _in_thread(edit_file),
        ),
    ]


def _in_thread(
    work: Callable[[dict[str, Any]], ToolResult],
) -> Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]:
    """Run blocking file work off the event loop, so streaming and Stop stay responsive."""

    async def run(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        return await asyncio.to_thread(work, args)

    return run


def _write(target: Path, shown_as: str, content: str) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" writes the text exactly as given; without it Windows turns every \n into \r\n.
        target.write_text(content, encoding="utf-8", newline="")
    except IsADirectoryError as error:
        raise ToolError(f"{shown_as} is a folder.") from error
    except OSError as error:
        raise ToolError(f"Could not write {shown_as}: {error.strerror or error}.") from error


def _with_line_endings(text: str, eol: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", eol)


def _read_text(path: Path, shown_as: str) -> str:
    if not path.is_file():
        raise ToolError(f"{shown_as}: no such file.")
    try:
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"{shown_as} is {size / 1_000_000:.1f} MB, too large to read. Use search_text instead.")
        data = path.read_bytes()
    except OSError as error:
        raise ToolError(f"Could not read {shown_as}: {error.strerror or error}.") from error
    text = _decode(data)
    if text is None:
        raise ToolError(f"{shown_as} is a binary file and cannot be read as text.")
    return text


def _searchable_text(paths: ProjectPaths, path: Path) -> str | None:
    try:
        # The same rule as every other file tool: a link out of the project is not searched.
        paths.resolve(paths.relative(path))
        if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
            return None
        return _decode(path.read_bytes())
    except (ToolError, OSError):
        return None


def _decode(data: bytes) -> str | None:
    """UTF-8 text, or None for binary content (a NUL byte early on, or bytes that are not UTF-8)."""
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}
