"""Bounded, LF-delimited capture reading shared by validation and replay."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from .protocol import MAX_FRAME_BYTES


def iter_capture_file(path: Path) -> Iterator[tuple[int, bytes]]:
    """Own the file inside a generator so callers can offload its full lifecycle."""
    with path.open("rb") as stream:
        yield from iter_capture_records(stream)


def iter_capture_records(stream: BinaryIO) -> Iterator[tuple[int, bytes]]:
    """Yield physical line numbers and bounded records without their final LF.

    An oversized line is drained in bounded reads and represented by its first
    MAX_FRAME_BYTES + 1 bytes, so the live decoder rejects it exactly once.
    Empty/CR-only lines within the limit are skipped. A final LF is optional
    for files; callers append one before feeding each record to the decoder.
    """
    line_number = 0
    read_size = MAX_FRAME_BYTES + 2  # payload, one overflow byte, and possible LF
    while prefix := stream.readline(read_size):
        line_number += 1
        raw = prefix.removesuffix(b"\n")
        if len(raw) > MAX_FRAME_BYTES:
            raw = raw[: MAX_FRAME_BYTES + 1]
            tail = prefix
            while tail and not tail.endswith(b"\n"):
                tail = stream.readline(read_size)
        if raw.rstrip(b"\r") or len(raw) > MAX_FRAME_BYTES:
            yield line_number, raw
