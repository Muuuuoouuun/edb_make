#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import mimetypes
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


OPENAI_IMAGE_EDIT_URL = "https://api.openai.com/v1/images/edits"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_QUALITY = "high"
DEFAULT_IMAGE_SIZE = "auto"


DEFAULT_RECONSTRUCTION_PROMPT = """Edit the provided exam-problem crop into a clean high-resolution printable image.

Preserve the source content exactly:
- Keep every Korean character, English word, number, equation, symbol, option label, diagram, table, graph, arrow, and relative layout unchanged.
- Do not solve, explain, translate, summarize, replace, omit, or invent any text or mathematical content.
- Do not add headers, page numbers, watermarks, decorations, or extra marks.

Improve only visual quality:
- Remove blur, scanner noise, shadows, paper texture, compression artifacts, and low-resolution jagged edges.
- Use crisp dark ink on a clean white background with the same composition and generous crop margins.
- Keep diagrams and tables faithful to the original geometry.
"""


@dataclass(slots=True)
class ImageReconstructionResult:
    output_path: Path
    model: str
    prompt: str
    source_path: Path
    latency_ms: int
    revised_prompt: str | None = None
    usage: dict[str, Any] | None = None
    response_id: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": "applied",
            "provider": "openai",
            "model": self.model,
            "source_path": str(self.source_path),
            "output_path": str(self.output_path),
            "latency_ms": self.latency_ms,
            "revised_prompt": self.revised_prompt,
            "usage": self.usage or {},
            "response_id": self.response_id,
        }


def reconstruct_problem_image(
    source_path: str | Path,
    output_path: str | Path,
    *,
    api_key: str,
    model: str = DEFAULT_OPENAI_IMAGE_MODEL,
    prompt: str = DEFAULT_RECONSTRUCTION_PROMPT,
    quality: str = DEFAULT_IMAGE_QUALITY,
    size: str = DEFAULT_IMAGE_SIZE,
    timeout_ms: int = 120000,
) -> ImageReconstructionResult:
    source = Path(source_path)
    output = Path(output_path)
    if not source.exists():
        raise FileNotFoundError(f"source image not found: {source}")
    key = api_key.strip()
    if not key:
        raise ValueError("OPENAI_API_KEY is required for image reconstruction")

    fields: list[tuple[str, str]] = [
        ("model", model.strip() or DEFAULT_OPENAI_IMAGE_MODEL),
        ("prompt", prompt.strip() or DEFAULT_RECONSTRUCTION_PROMPT),
        ("size", size.strip() or DEFAULT_IMAGE_SIZE),
        ("quality", quality.strip() or DEFAULT_IMAGE_QUALITY),
    ]
    content_type = mimetypes.guess_type(str(source))[0] or "image/png"
    body, multipart_type = _encode_multipart(
        fields=fields,
        files=[
            (
                "image[]",
                source.name or "problem.png",
                content_type,
                source.read_bytes(),
            )
        ],
    )
    req = request.Request(
        OPENAI_IMAGE_EDIT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": multipart_type,
        },
        method="POST",
    )

    started_at = time.perf_counter()
    try:
        with request.urlopen(req, timeout=max(1.0, timeout_ms / 1000.0)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI image reconstruction failed ({exc.code}): {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"OpenAI image reconstruction failed: {exc.reason}") from exc

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("OpenAI image reconstruction returned no image data")
    first = data[0] if isinstance(data[0], dict) else {}
    b64_image = first.get("b64_json")
    if not isinstance(b64_image, str) or not b64_image:
        raise RuntimeError("OpenAI image reconstruction response did not include b64_json")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(b64_image))
    latency_ms = int(round((time.perf_counter() - started_at) * 1000.0))
    return ImageReconstructionResult(
        output_path=output,
        model=str(fields[0][1]),
        prompt=str(fields[1][1]),
        source_path=source,
        latency_ms=latency_ms,
        revised_prompt=first.get("revised_prompt") if isinstance(first.get("revised_prompt"), str) else None,
        usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else first.get("usage"),
        response_id=str(payload.get("id")) if payload.get("id") else None,
    )


def _encode_multipart(
    *,
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = f"codex-edb-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def add(line: str | bytes) -> None:
        chunks.append(line if isinstance(line, bytes) else line.encode("utf-8"))

    for name, value in fields:
        add(f"--{boundary}\r\n")
        add(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        add(str(value))
        add("\r\n")

    for name, filename, content_type, content in files:
        add(f"--{boundary}\r\n")
        safe_filename = filename.replace('"', "")
        add(
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{safe_filename}"\r\n'
        )
        add(f"Content-Type: {content_type}\r\n\r\n")
        add(content)
        add("\r\n")

    add(f"--{boundary}--\r\n")
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
