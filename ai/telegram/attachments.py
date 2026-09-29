from __future__ import annotations

import base64
import re
from pathlib import PurePosixPath

IMAGE_LIMIT = 5 * 1024 * 1024
TEXT_LIMIT = 256 * 1024
_TEXT_SUFFIXES = {".py", ".pine", ".txt", ".md"}


def attachment_descriptor(message: dict) -> dict | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        item = photos[-1]
        kind, name, limit = "image", "photo.jpg", IMAGE_LIMIT
    elif isinstance(message.get("document"), dict):
        item = message["document"]
        name = str(item.get("file_name") or "attachment").replace("\\", "/")
        name = PurePosixPath(name).name
        suffix = PurePosixPath(name).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            kind, limit = "image", IMAGE_LIMIT
        elif suffix in _TEXT_SUFFIXES:
            kind, limit = "text", TEXT_LIMIT
        else:
            raise ValueError("Attach PNG/JPEG/WebP images or UTF-8 .py/.pine/.txt/.md files.")
    else:
        return None
    if not isinstance(item, dict) or not isinstance(item.get("file_id"), str):
        raise ValueError("Invalid attachment.")
    size = item.get("file_size", 0)
    if type(size) is not int or size < 0 or size > limit:
        raise ValueError("Attachment too large (images: 5 MB; scripts: 256 KB).")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:160] or "attachment"
    return {"file_id": item["file_id"], "kind": kind, "name": name, "limit": limit}


def validate_attachment(descriptor: dict, data: bytes) -> dict:
    if not data or len(data) > descriptor["limit"]:
        raise ValueError("Attachment is empty or exceeds its size limit.")
    if descriptor["kind"] == "text":
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError("Script attachments must use UTF-8 encoding.") from None
        if "\x00" in content:
            raise ValueError("Binary script attachments are not supported.")
        mime = "text/plain"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("The attachment does not contain a supported image.")
    return {"name": descriptor["name"], "kind": descriptor["kind"], "mime": mime, "data": data}


def attachment_content(item: dict) -> dict:
    if item["kind"] == "text":
        return {"type": "inputText", "text": item["data"].decode("utf-8-sig")}
    return {"type": "inputImage", "imageUrl": (
        f"data:{item['mime']};base64," + base64.b64encode(item["data"]).decode("ascii")
    )}
