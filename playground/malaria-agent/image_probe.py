"""Praxiom image probe: generate one image, edit it, check two documented errors,
and compare the balance before and after with the published prices.

    uv run python image_probe.py

Spends about ESP 0.083 (one 1K image + one edit). Error requests should be free.
Outputs go to results/images/ next to this file.
"""

import base64
import json
import struct
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import openai
from openai import OpenAI

from config import Settings, load_settings

OUT_DIR = Path(__file__).resolve().parent / "results" / "images"
IMAGE_TIMEOUT_SECONDS = 180.0  # docs: keep the HTTP timeout at 2 minutes or more
EXPECTED_SPEND_ESP = 0.04 + 0.043

GENERATE_PROMPT = (
    "A community health worker in a Nigerian village clinic performing a malaria "
    "rapid diagnostic test on a patient's fingertip, warm daylight, clean flat illustration"
)
EDIT_PROMPT = "Make it night time, lit by a single lantern, keep everything else the same"


def balance_esp(settings: Settings) -> float:
    response = httpx.get(
        f"{settings.base_url}/credits/balance",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    return int(response.json()["balance_micros"]) / 1_000_000


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height straight from the PNG header, so we need no image library."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def save_b64(b64: str | None, name: str) -> tuple[Path, tuple[int, int], int]:
    if not b64:
        raise ValueError("response had no b64_json")
    data = base64.b64decode(b64)
    path = OUT_DIR / name
    path.write_bytes(data)
    return path, png_size(data), len(data)


def expect_error(label: str, call: Callable[[], object]) -> str:
    try:
        call()
    except openai.APIStatusError as exc:
        return f"{label}: {type(exc).__name__} {exc.status_code} code={exc.code!r}"
    return f"{label}: accepted (expected an error)"


def main() -> None:
    settings = load_settings("praxiom")
    client = OpenAI(
        base_url=settings.base_url, api_key=settings.api_key,
        timeout=IMAGE_TIMEOUT_SECONDS, max_retries=0,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"sdk_version": openai.__version__}

    before = balance_esp(settings)
    print(f"balance before: ESP {before:.6f}")

    # 1. Generate through the SDK's standard images.generate call.
    started = time.monotonic()
    generated = client.images.generate(model="praxiom-image", prompt=GENERATE_PROMPT, size="1024x1024")
    path, size, nbytes = save_b64(generated.data[0].b64_json if generated.data else None, "generate-1k.png")
    report["generate"] = {"seconds": round(time.monotonic() - started, 1), "size": size, "bytes": nbytes}
    print(f"generate: {report['generate']} -> {path.name}")

    # 2. Edit that image through the SDK's standard images.edit call (multipart upload).
    started = time.monotonic()
    with path.open("rb") as image_file:
        edited = client.images.edit(model="praxiom-image-edit", image=image_file, prompt=EDIT_PROMPT)
    path, size, nbytes = save_b64(edited.data[0].b64_json if edited.data else None, "edit-1k.png")
    report["edit"] = {"seconds": round(time.monotonic() - started, 1), "size": size, "bytes": nbytes}
    print(f"edit: {report['edit']} -> {path.name}")

    # 3. Documented validation errors. These should be rejected and not charged.
    errors = [
        expect_error("size=512x512", lambda: client.images.generate(
            model="praxiom-image", prompt="a red circle", size="512x512")),
        expect_error("n=2", lambda: client.images.generate(
            model="praxiom-image", prompt="a red circle", n=2)),
    ]
    report["errors"] = errors
    for line in errors:
        print(line)

    after = balance_esp(settings)
    spent = round(before - after, 6)
    report["billing"] = {"before": before, "after": after, "spent": spent, "expected": EXPECTED_SPEND_ESP}
    print(f"balance after: ESP {after:.6f}  spent ESP {spent} (expected {EXPECTED_SPEND_ESP})")

    (OUT_DIR / "image-probe.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
