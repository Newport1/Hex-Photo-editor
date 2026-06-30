from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .jpeg_parser import JpegSegment, parse_jpeg_segments, segment_for_offset


@dataclass
class Mutation:
    timestamp: str
    offset: int
    before: int
    after: int
    region: str
    decode_result: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PhotoHexDocument:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.original_bytes = bytearray(self.path.read_bytes())
        self.data = bytearray(self.original_bytes)
        self.segments: list[JpegSegment] = parse_jpeg_segments(bytes(self.data))
        self.mutations: list[Mutation] = []
        self.last_decode_message = "Ready"
        self._last_valid_image = self.decode_image(strict=True)

    def reset(self) -> None:
        self.data = bytearray(self.original_bytes)
        self.segments = parse_jpeg_segments(bytes(self.data))
        self.mutations.clear()
        self.last_decode_message = "Reset to original"
        self._last_valid_image = self.decode_image(strict=True)

    def decode_image(self, strict: bool = False) -> Image.Image:
        try:
            img = Image.open(io.BytesIO(bytes(self.data)))
            img.load()
            decoded = img.convert("RGB")
            self._last_valid_image = decoded.copy()
            self.last_decode_message = f"Decode OK | {decoded.width}x{decoded.height}"
            return decoded
        except Exception as exc:
            self.last_decode_message = f"Decode failed: {exc}"
            if strict:
                raise
            return self._last_valid_image.copy()

    def edit_byte(self, offset: int, new_value: int, note: str = "") -> Mutation:
        if offset < 0 or offset >= len(self.data):
            raise IndexError("Offset out of range")
        new_value = int(new_value) & 0xFF
        before = self.data[offset]
        self.data[offset] = new_value
        seg = segment_for_offset(self.segments, offset)
        try:
            self.segments = parse_jpeg_segments(bytes(self.data))
            self.decode_image(strict=False)
            decode_result = self.last_decode_message
        except Exception as exc:
            decode_result = f"Structure error: {exc}"
            self.last_decode_message = decode_result
        mutation = Mutation(
            timestamp=datetime.now(timezone.utc).isoformat(),
            offset=offset,
            before=before,
            after=new_value,
            region=seg.name if seg else "Unknown",
            decode_result=decode_result,
            note=note,
        )
        self.mutations.append(mutation)
        return mutation

    def is_protected_offset(self, offset: int) -> bool:
        seg = segment_for_offset(self.segments, offset)
        if seg is None:
            return True
        if seg.name in {"SOI", "EOI"}:
            return True
        if seg.marker.startswith("FF") and offset in {seg.start, seg.start + 1}:
            return True
        if seg.name != "Scan Data" and seg.length is not None and offset in {seg.start + 2, seg.start + 3}:
            return True
        return False

    def apply_range_operation(
        self,
        start: int,
        end: int,
        operation: str,
        operand: int,
        *,
        skip_ff: bool = True,
        protect_markers: bool = True,
        note: str = "",
    ) -> list[Mutation]:
        """Apply a byte operation to inclusive [start, end]."""
        if start > end:
            start, end = end, start
        start = max(0, start)
        end = min(len(self.data) - 1, end)
        operand = int(operand) & 0xFF
        edits: list[tuple[int, int, int, str]] = []

        for offset in range(start, end + 1):
            before = self.data[offset]
            if skip_ff and before == 0xFF:
                continue
            if protect_markers and self.is_protected_offset(offset):
                continue
            if operation == "xor":
                after = before ^ operand
            elif operation == "add":
                after = (before + operand) & 0xFF
            elif operation == "sub":
                after = (before - operand) & 0xFF
            elif operation == "set":
                after = operand
            else:
                raise ValueError(f"Unknown operation: {operation}")
            if skip_ff and after == 0xFF:
                after = 0xFE
            if after != before:
                seg = segment_for_offset(self.segments, offset)
                edits.append((offset, before, after, seg.name if seg else "Unknown"))

        if not edits:
            return []

        for offset, _, after, _ in edits:
            self.data[offset] = after

        try:
            self.segments = parse_jpeg_segments(bytes(self.data))
            self.decode_image(strict=False)
            decode_result = self.last_decode_message
        except Exception as exc:
            decode_result = f"Structure error: {exc}"
            self.last_decode_message = decode_result

        timestamp = datetime.now(timezone.utc).isoformat()
        mutations: list[Mutation] = []
        for offset, before, after, region in edits:
            mutation = Mutation(
                timestamp=timestamp,
                offset=offset,
                before=before,
                after=after,
                region=region,
                decode_result=decode_result,
                note=note or f"{operation.upper()} range 0x{start:06X}-0x{end:06X}",
            )
            self.mutations.append(mutation)
            mutations.append(mutation)
        return mutations

    def undo_last(self) -> Mutation | None:
        if not self.mutations:
            return None
        m = self.mutations.pop()
        self.data[m.offset] = m.before
        self.segments = parse_jpeg_segments(bytes(self.data))
        self.decode_image(strict=False)
        return m

    def add_note_to_last(self, note: str) -> None:
        if self.mutations:
            self.mutations[-1].note = note

    def save_mutated(self, path: str | Path) -> None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(self.data))

    def save_log(self, path: str | Path) -> None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": str(self.path),
            "bytes": len(self.data),
            "mutations": [m.to_dict() for m in self.mutations],
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
