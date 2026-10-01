"""Linux /proc/self/mountinfo parsing and validation."""

from dataclasses import dataclass
from typing import Collection, Sequence


@dataclass(frozen=True)
class MountEntry:
    mount_id: int
    parent_id: int
    root: str
    mount_point: str
    options: str
    fstype: str
    source: str
    super_options: str


STANDARD_EXACT: frozenset[str] = frozenset({
    "/",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/resolv.conf",
    "/run/.containerenv",
    "/run/secrets",
})

STANDARD_TREES: tuple[str, ...] = ("/proc", "/sys", "/dev")


def _unescape_mountinfo_path(path: str) -> str:
    """Unescape octal escapes used in mountinfo (space, tab, newline, backslash)."""
    return (
        path.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def parse_mountinfo(text: str) -> list[MountEntry]:
    """Parse /proc/self/mountinfo text into structured MountEntry records.

    Raises ValueError if any non-empty line cannot be parsed according to the mountinfo format.
    """
    entries: list[MountEntry] = []
    for line_idx, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        parts = line.split(" - ")
        if len(parts) != 2:
            raise ValueError(f"Line {line_idx}: separator ' - ' not found or occurs multiple times: {line!r}")

        first_half, second_half = parts[0], parts[1]
        first_fields = first_half.split()
        if len(first_fields) < 6:
            raise ValueError(f"Line {line_idx}: expected at least 6 fields in first half, got {len(first_fields)}")

        try:
            mount_id = int(first_fields[0])
            parent_id = int(first_fields[1])
        except ValueError as exc:
            raise ValueError(f"Line {line_idx}: mount_id or parent_id not integer") from exc

        root = _unescape_mountinfo_path(first_fields[3])
        mount_point = _unescape_mountinfo_path(first_fields[4])
        options = first_fields[5]

        second_fields = second_half.split(maxsplit=2)
        if len(second_fields) < 2:
            raise ValueError(f"Line {line_idx}: expected at least fstype and source in second half")

        fstype = second_fields[0]
        source = second_fields[1]
        super_options = second_fields[2] if len(second_fields) > 2 else ""

        entries.append(
            MountEntry(
                mount_id=mount_id,
                parent_id=parent_id,
                root=root,
                mount_point=mount_point,
                options=options,
                fstype=fstype,
                source=source,
                super_options=super_options,
            )
        )
    return entries


def find_unexpected_mounts(entries: Sequence[MountEntry], allowed: Collection[str]) -> list[MountEntry]:
    """Find any mount whose mount_point is not standard and not in allowed paths.

    Evaluation is based purely on mount_point. Root, options, and overlay options are ignored.
    """
    unexpected: list[MountEntry] = []
    for entry in entries:
        mp = entry.mount_point
        # 1. Exact standard mount point
        if mp in STANDARD_EXACT:
            continue

        # 2. Standard tree root or subpath
        if any(mp == tree or mp.startswith(tree + "/") for tree in STANDARD_TREES):
            continue

        # 3. Allowed mount point or subpath (e.g. nested overlay mount)
        if any(mp == allow or mp.startswith(allow.rstrip("/") + "/") for allow in allowed):
            continue

        unexpected.append(entry)

    return unexpected
