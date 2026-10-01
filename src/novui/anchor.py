"""Anchor matching and text splitting utilities."""


class AnchorError(Exception):
    """Raised when an anchor match is not unique (0 or multiple matches)."""

    def __init__(self, message: str, count: int) -> None:
        super().__init__(message)
        self.count = count


def find_anchor(text: str, anchor: str, before: str = "", after: str = "") -> tuple[int, int]:
    """Find a unique occurrence of anchor with before/after context.

    Returns the UTF-8 byte offset (start, end) of the anchor segment within text.
    Raises ValueError if anchor is empty.
    Raises AnchorError with the match count if 0 or 2+ matches are found.
    Overlapping matches are counted by advancing 1 character after each match.
    """
    if not anchor:
        raise ValueError("Anchor string cannot be empty")

    target = before + anchor + after
    target_len = len(target)
    before_len = len(before)
    anchor_len = len(anchor)

    matches: list[int] = []
    pos = 0
    text_len = len(text)
    while pos <= text_len - target_len:
        idx = text.find(target, pos)
        if idx == -1:
            break
        matches.append(idx)
        pos = idx + 1

    count = len(matches)
    if count != 1:
        raise AnchorError(
            f"Expected exactly 1 match for anchor (with context), found {count}",
            count=count,
        )

    match_start = matches[0]
    anchor_char_start = match_start + before_len
    anchor_char_end = anchor_char_start + anchor_len

    start_byte = len(text[:anchor_char_start].encode("utf-8"))
    end_byte = len(text[:anchor_char_end].encode("utf-8"))

    return (start_byte, end_byte)


def split_by_range(data: bytes, start: int, end: int) -> tuple[bytes, bytes]:
    """Split byte data into prefix (before start) and suffix (after end).

    Raises ValueError if not 0 <= start <= end <= len(data).
    """
    if not (0 <= start <= end <= len(data)):
        raise ValueError(f"Invalid range [{start}, {end}] for data of length {len(data)}")
    return (data[:start], data[end:])
