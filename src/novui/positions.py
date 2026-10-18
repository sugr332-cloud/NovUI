"""Order of positions {chapter, scene} (spec §6.4.1, phase2d-design §2).

A position is compared by (index of the chapter in chapters-order.yaml, number of the scene ID).
Scene IDs are compared numerically: S2 < S10, and S003 is the same position as S3.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

_SCENE_RE = re.compile(r"^S([0-9]+)$")


class PositionError(ValueError):
    """A position cannot be compared (chapter not in chapters-order.yaml, malformed scene ID or position)."""


def scene_number(scene_id: str) -> int:
    """Numeric part of a scene ID (S003 -> 3)."""
    m = _SCENE_RE.match(scene_id) if isinstance(scene_id, str) else None
    if m is None:
        raise PositionError(f"invalid scene id: {scene_id!r}")
    return int(m.group(1))


def chapter_index(order: Sequence[str], chapter_id: str) -> int:
    """Index of the chapter in chapters-order.yaml."""
    try:
        return list(order).index(chapter_id)
    except ValueError:
        raise PositionError(f"chapter {chapter_id!r} not in chapters-order.yaml") from None


def position_key(order: Sequence[str], pos: Mapping[str, Any]) -> tuple[int, int]:
    """Sort key of a position: (chapter index, scene number)."""
    if not isinstance(pos, Mapping) or "chapter" not in pos or "scene" not in pos:
        raise PositionError(f"invalid position: {pos!r}")
    return chapter_index(order, pos["chapter"]), scene_number(pos["scene"])


def compare_positions(order: Sequence[str], a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    """-1 if a < b, 0 if equal, 1 if a > b. Raises PositionError if either cannot be compared."""
    try:
        ka = position_key(order, a)
    except PositionError as exc:
        raise PositionError(f"first position: {exc}") from None
    try:
        kb = position_key(order, b)
    except PositionError as exc:
        raise PositionError(f"second position: {exc}") from None
    return (ka > kb) - (ka < kb)


def format_position(pos: Mapping[str, Any]) -> str:
    """'ch-018 S004' (the scene ID is kept as written)."""
    return f"{pos['chapter']} {pos['scene']}"
