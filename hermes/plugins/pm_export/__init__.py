"""Write-only export tools: save an answer as a .md file or several as one .zip for Slack delivery.

The model only supplies text; it never names a directory. Every file lands in a fresh folder under
$HERMES_HOME/cache/documents/exports (a media-delivery safe root), so a MEDIA: tag for it always
delivers, even with HERMES_MEDIA_DELIVERY_STRICT=1. Nothing here reads existing files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

MAX_BYTES = 2_000_000  # per call, uncompressed; far above any real report, far below Slack's upload limit
MAX_FILES = 20
KEEP_SECONDS = 24 * 3600

_UNSAFE = re.compile(r"[^\w.-]+")  # \w keeps Korean; drops / \ : so no path or MEDIA: tricks


def _export_root() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "cache" / "documents" / "exports"


def _safe_name(raw, ext: str) -> str:
    stem = os.path.basename(str(raw or "").strip())
    if stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    stem = _UNSAFE.sub("-", stem).strip(".-_")[:80]
    return (stem or "export") + ext


def _new_dir() -> Path:
    root = _export_root()
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - KEEP_SECONDS
    for old in root.iterdir():  # ponytail: cleanup on write, no scheduler; fine at team-chat volume
        try:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:  # a concurrent save already removed it
            continue
    return Path(tempfile.mkdtemp(dir=root))


def _done(path: Path) -> str:
    return json.dumps({
        "path": str(path),
        "media_tag": f"MEDIA:{path}",
        "next": "Put media_tag on its own line in your final reply (not inside code or quotes), "
                "with a 3-5 line summary above it. The file is only sent if that line is there.",
    }, ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message + " No file was created; do not tell the user a file was sent."})


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def save_markdown(args: dict, **kw) -> str:
    content = _text(args.get("content"))
    if not content.strip():
        return _error("content is empty.")
    if len(content.encode()) > MAX_BYTES:
        return _error(f"content is over {MAX_BYTES} bytes.")
    path = _new_dir() / _safe_name(args.get("filename"), ".md")
    path.write_text(content, encoding="utf-8")
    return _done(path)


def save_zip(args: dict, **kw) -> str:
    files = args.get("files")
    if not isinstance(files, list) or not files:
        return _error("files must be a non-empty list of {filename, content}.")
    if len(files) > MAX_FILES:
        return _error(f"at most {MAX_FILES} files per zip.")
    entries = {}
    for f in files:
        f = f if isinstance(f, dict) else {}
        name, content = _safe_name(f.get("filename"), ".md"), _text(f.get("content"))
        if not content.strip():
            return _error(f"{name} is empty.")
        if name in entries:
            return _error(f"two files are both named {name}; give each a different filename.")
        entries[name] = content
    if sum(len(c.encode()) for c in entries.values()) > MAX_BYTES:
        return _error(f"files together are over {MAX_BYTES} bytes.")
    if "README.md" not in entries:
        entries = {"README.md": "# Contents\n\n" + "".join(f"- {n}\n" for n in entries), **entries}
    path = _new_dir() / _safe_name(args.get("filename"), ".zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return _done(path)


_FILENAME = {"type": "string", "description": "Short descriptive name with a date, e.g. sprint-summary-2026-10-03. Extension is added for you."}

SAVE_MARKDOWN_SCHEMA = {
    "name": "save_markdown",
    "description": (
        "Save a Markdown document as a .md file the user can download in Slack. Use when the user asks for a "
        "file/doc/report/export, or when the full answer is too long for a chat message. Write the complete "
        "document in content: a title, the date, the facts, and a final 'Sources' section (channels, senders, "
        "dates, Jira keys). Only use facts you found; never invent numbers."
    ),
    "parameters": {"type": "object", "properties": {
        "filename": _FILENAME,
        "content": {"type": "string", "description": "Full Markdown text of the document."},
    }, "required": ["filename", "content"]},
}

SAVE_ZIP_SCHEMA = {
    "name": "save_zip",
    "description": (
        "Save several Markdown documents as one .zip file the user can download in Slack. Use only when the "
        "user asks for a zip or for several separate documents at once. Each file follows the same rules as "
        "save_markdown (title, date, facts, 'Sources' section). A README.md listing the files is added for you."
    ),
    "parameters": {"type": "object", "properties": {
        "filename": _FILENAME,
        "files": {"type": "array", "maxItems": MAX_FILES, "items": {"type": "object", "properties": {
            "filename": {"type": "string", "description": "Name of this document inside the zip."},
            "content": {"type": "string", "description": "Full Markdown text of this document."},
        }, "required": ["filename", "content"]}},
    }, "required": ["filename", "files"]},
}


def register(ctx) -> None:
    ctx.register_tool(name="save_markdown", toolset="pm_export", schema=SAVE_MARKDOWN_SCHEMA,
                      handler=save_markdown, emoji="📝")
    ctx.register_tool(name="save_zip", toolset="pm_export", schema=SAVE_ZIP_SCHEMA,
                      handler=save_zip, emoji="🗜️")
