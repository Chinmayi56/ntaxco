"""Shared validation helpers for NTAXCO backend workflows.

Indian mobile policy:
- Accept exactly 10 digits beginning with 6, 7, 8, or 9.
- Do not silently remove letters or arbitrary punctuation.
- Workflows that explicitly support +91 may opt in to that prefix.
"""
from __future__ import annotations

import re
from typing import Any

_INDIAN_MOBILE = re.compile(r"[6-9][0-9]{9}\Z")
_EXPLICIT_PLUS91 = re.compile(r"\+91([6-9][0-9]{9})\Z")


def normalize_indian_mobile(value: Any, *, allow_plus91: bool = False) -> str | None:
    """Return a normalized 10-digit Indian mobile number, or None if invalid.

    Whitespace and punctuation are not accepted. The +91 prefix is accepted
    only for workflows that explicitly enable it.
    """
    if not isinstance(value, str):
        return None
    if _INDIAN_MOBILE.fullmatch(value):
        return value
    if allow_plus91:
        match = _EXPLICIT_PLUS91.fullmatch(value)
        if match:
            return match.group(1)
    return None


def require_indian_mobile(value: Any, *, allow_plus91: bool = False) -> str:
    """Validate and return a normalized number, raising ValueError on failure."""
    normalized = normalize_indian_mobile(value, allow_plus91=allow_plus91)
    if normalized is None:
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    return normalized
