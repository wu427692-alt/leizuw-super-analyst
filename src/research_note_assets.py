# -*- coding: utf-8 -*-
"""Knowledge-planet attachment classification shared by ingest, search and AI queueing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List


AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".aac", ".amr", ".ogg", ".oga", ".wma", ".flac", ".opus",
}
_FILE_PLACEHOLDERS = {"", "「文件」", "[文件]", "【文件】", "文件"}


def asset_name(asset: Dict[str, Any]) -> str:
    return str(asset.get("name") or asset.get("filename") or "").strip()


def is_audio_asset(asset: Dict[str, Any]) -> bool:
    name = asset_name(asset).lower()
    media_type = str(asset.get("type") or asset.get("mime_type") or asset.get("content_type") or "").lower()
    return Path(name).suffix.lower() in AUDIO_EXTENSIONS or media_type.startswith("audio/")


def enrich_file_assets(files: Iterable[Any]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for value in files:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        audio = is_audio_asset(item)
        item["asset_kind"] = "audio" if audio else "file"
        item["ai_eligible"] = not audio
        if audio and item.get("duration") is not None:
            item["duration_seconds"] = item.get("duration")
        enriched.append(item)
    return enriched


def asset_summary(files: Iterable[Any], images: Iterable[Any]) -> Dict[str, Any]:
    normalized_files = enrich_file_assets(files)
    audio_files = [item for item in normalized_files if item.get("asset_kind") == "audio"]
    ordinary_files = [item for item in normalized_files if item.get("asset_kind") != "audio"]
    image_count = sum(1 for item in images if isinstance(item, dict))
    return {
        "audio_count": len(audio_files),
        "file_count": len(ordinary_files),
        "image_count": image_count,
        "has_audio": bool(audio_files),
        "has_files": bool(ordinary_files),
        "has_images": image_count > 0,
        "audio_names": [asset_name(item) for item in audio_files if asset_name(item)],
        "file_names": [asset_name(item) for item in ordinary_files if asset_name(item)],
    }


def is_audio_only_note(*, title: Any, content: Any, files: Iterable[Any], images: Iterable[Any]) -> bool:
    normalized_files = enrich_file_assets(files)
    if not normalized_files or any(item.get("asset_kind") != "audio" for item in normalized_files):
        return False
    if any(isinstance(item, dict) for item in images):
        return False
    normalized_content = str(content or "").strip()
    if normalized_content not in _FILE_PLACEHOLDERS:
        return False
    normalized_title = str(title or "").strip()
    names = {asset_name(item) for item in normalized_files if asset_name(item)}
    return not normalized_title or normalized_title in names or Path(normalized_title).suffix.lower() in AUDIO_EXTENSIONS
