from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class JpegSegment:
    start: int
    end: int
    marker: str
    name: str
    length: int | None
    risk: str
    details: str = ""

    @property
    def size(self) -> int:
        return self.end - self.start


MARKER_NAMES = {
    0xD8: "SOI",
    0xD9: "EOI",
    0xDA: "SOS",
    0xC0: "SOF0",
    0xC2: "SOF2",
    0xC4: "DHT",
    0xDB: "DQT",
    0xDD: "DRI",
    0xE0: "APP0",
    0xE1: "APP1",
    0xE2: "APP2",
    0xE3: "APP3",
    0xE4: "APP4",
    0xE5: "APP5",
    0xE6: "APP6",
    0xE7: "APP7",
    0xE8: "APP8",
    0xE9: "APP9",
    0xEA: "APP10",
    0xEB: "APP11",
    0xEC: "APP12",
    0xED: "APP13",
    0xEE: "APP14",
    0xEF: "APP15",
    0xFE: "COM",
}

NO_LENGTH_MARKERS = {0xD8, 0xD9} | set(range(0xD0, 0xD8)) | {0x01}


def _read_be16(data: bytes, pos: int) -> int:
    return (data[pos] << 8) | data[pos + 1]


def _risk_for(name: str) -> str:
    if name in {"SOI", "EOI", "DHT", "SOF0", "SOF2", "SOS"}:
        return "high"
    if name == "Scan Data":
        return "medium"
    if name.startswith("APP") or name == "COM":
        return "low"
    if name in {"DQT", "DRI"}:
        return "medium"
    return "medium"


def _app_details(marker_code: int, payload: bytes) -> str:
    if marker_code == 0xE0 and payload.startswith(b"JFIF\x00"):
        return "JFIF metadata"
    if marker_code == 0xE1:
        if payload.startswith(b"Exif\x00\x00"):
            return "EXIF metadata"
        if payload.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            return "XMP metadata"
    if marker_code == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
        return "ICC profile"
    if marker_code == 0xED and payload.startswith(b"Photoshop 3.0\x00"):
        return "Photoshop IRB"
    return ""


def find_eoi(data: bytes, start: int = 0) -> int:
    idx = data.rfind(b"\xff\xd9", start)
    if idx == -1:
        raise ValueError("EOI marker not found")
    return idx


def parse_jpeg_segments(data: bytes) -> list[JpegSegment]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("Not a JPEG file")

    segments: list[JpegSegment] = [JpegSegment(0, 2, "FFD8", "SOI", None, _risk_for("SOI"), "Start of image")]
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError(f"Expected JPEG marker at offset {pos:#x}")
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker_code = data[pos]
        marker_start = pos - 1
        marker = f"FF{marker_code:02X}"
        name = MARKER_NAMES.get(marker_code, f"MARKER_{marker_code:02X}")

        if marker_code in NO_LENGTH_MARKERS:
            seg = JpegSegment(marker_start, marker_start + 2, marker, name, None, _risk_for(name))
            segments.append(seg)
            pos = marker_start + 2
            if marker_code == 0xD9:
                break
            continue

        if pos + 2 >= len(data):
            raise ValueError("Truncated JPEG segment header")
        length = _read_be16(data, pos + 1)
        end = marker_start + 2 + length
        if end > len(data):
            raise ValueError(f"Segment {name} overruns file length")
        payload = data[pos + 3 : end]
        details = _app_details(marker_code, payload)
        seg = JpegSegment(marker_start, end, marker, name, length, _risk_for(name), details)
        segments.append(seg)

        if marker_code == 0xDA:
            scan_start = end
            eoi = find_eoi(data, scan_start)
            if eoi > scan_start:
                segments.append(JpegSegment(scan_start, eoi, "SCAN", "Scan Data", eoi - scan_start, _risk_for("Scan Data"), "Entropy-coded image data"))
            segments.append(JpegSegment(eoi, eoi + 2, "FFD9", "EOI", None, _risk_for("EOI"), "End of image"))
            return segments
        pos = end
    return segments


def segment_for_offset(segments: Iterable[JpegSegment], offset: int) -> JpegSegment | None:
    for seg in segments:
        if seg.start <= offset < seg.end:
            return seg
    return None
