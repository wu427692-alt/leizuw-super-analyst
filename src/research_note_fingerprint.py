# -*- coding: utf-8 -*-
"""Information-only fingerprints for knowledge-planet research notes."""

from __future__ import annotations

import hashlib
import json
from typing import Any


_VOLATILE_ASSET_KEYS = {
    "url", "download_url", "signed_url", "token", "expires", "expire_time",
    "likes", "like_count", "comments", "comment_count", "readers", "reader_count",
    "views", "view_count",
}


def information_asset_projection(value: Any) -> Any:
    """Keep attachment identity/metadata while excluding expiring and social fields."""
    if isinstance(value, dict):
        return {
            str(key): information_asset_projection(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in _VOLATILE_ASSET_KEYS
            and not str(key).lower().endswith("_url")
        }
    if isinstance(value, list):
        return [information_asset_projection(item) for item in value]
    return value


def research_note_information_hash(*, title: Any, content: Any, files: Any, images: Any) -> str:
    payload = {
        "title": str(title or "").strip(),
        "content": str(content or "").strip(),
        "files": information_asset_projection(files if isinstance(files, list) else []),
        "images": information_asset_projection(images if isinstance(images, list) else []),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
