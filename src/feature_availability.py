"""Product availability, independent of provider credentials and saved user data."""

import posixpath
from urllib.parse import unquote

# Temporarily withdrawn by the operator. Keep implementation and saved ledgers
# intact; restoring a provider or changing environment variables cannot enable it.
_UNAVAILABLE_FEATURES = frozenset({"jev"})
_JEV_PATH_PREFIXES = (
    "/jev-backtest",
    "/jev-backtest-assets",
    "/api/v1/jev-backtest",
    "/api/v1/jev-convertible-t",
    "/api/v1/jev-live-t",
)


def is_feature_available(feature: str) -> bool:
    return feature not in _UNAVAILABLE_FEATURES


def canonical_feature_path(path: str) -> str | None:
    """Match the SPA resolver's one extra URL decode and dot-segment resolution.

    This is only an availability comparison, never a filesystem access rule.
    Ambiguous separators/controls stay rejected by the existing asset resolver.
    """
    decoded = unquote(path)
    if "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        return None
    return posixpath.normpath("/" + decoded.lstrip("/"))


def unavailable_feature_for_path(path: str) -> str | None:
    """Match whole canonical path segments, never similarly named other routes."""
    normalized = canonical_feature_path(path)
    if normalized is None:
        return None
    if not is_feature_available("jev") and any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in _JEV_PATH_PREFIXES
    ):
        return "jev"
    return None
