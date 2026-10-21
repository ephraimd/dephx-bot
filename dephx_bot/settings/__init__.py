import os
from pathlib import Path


def write_text_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Write to a temp file and rename it over the target, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)
    os.replace(temp, path)
