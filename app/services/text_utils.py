import html
import re
from typing import Iterable, List

from app.schemas.models import TranscriptSegment

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TIMESTAMP_TAG_RE = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{3}>")


def clean_text(text: str) -> str:
    text = html.unescape(text)
    text = TIMESTAMP_TAG_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = text.replace("\u200b", "")
    return SPACE_RE.sub(" ", text).strip()


def timestamp_to_seconds(value: str) -> float:
    value = value.strip().replace(",", ".")
    pieces = value.split(":")
    if len(pieces) == 2:
        hours = 0
        minutes = int(pieces[0])
        seconds = float(pieces[1])
    elif len(pieces) == 3:
        hours = int(pieces[0])
        minutes = int(pieces[1])
        seconds = float(pieces[2])
    else:
        raise ValueError(f"Invalid timestamp: {value}")
    return hours * 3600 + minutes * 60 + seconds


def normalize_segments(segments: Iterable[TranscriptSegment]) -> List[TranscriptSegment]:
    cleaned: List[TranscriptSegment] = []
    previous_text = ""
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        text = clean_text(segment.text)
        if not text:
            continue
        if text == previous_text:
            continue
        start = max(0.0, float(segment.start))
        end = max(start + 0.1, float(segment.end))
        cleaned.append(
            TranscriptSegment(
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                source=segment.source,
                importance=min(1.0, max(segment.importance, min(len(text) / 120.0, 1.0))),
            )
        )
        previous_text = text
    return merge_short_segments(cleaned)


def merge_short_segments(segments: List[TranscriptSegment]) -> List[TranscriptSegment]:
    if not segments:
        return []
    merged: List[TranscriptSegment] = []
    buffer = segments[0]
    for current in segments[1:]:
        gap = current.start - buffer.end
        merged_text = f"{buffer.text} {current.text}".strip()
        should_merge = gap <= 1.0 and (len(buffer.text) < 36 or len(current.text) < 24) and len(merged_text) <= 110
        if should_merge and current.source == buffer.source:
            buffer = TranscriptSegment(
                start=buffer.start,
                end=current.end,
                text=merged_text,
                source=buffer.source,
                importance=max(buffer.importance, current.importance),
            )
        else:
            merged.append(buffer)
            buffer = current
    merged.append(buffer)
    return merged


def transcript_window(
    segments: Iterable[TranscriptSegment],
    center_time: float,
    before: float = 8.0,
    after: float = 8.0,
    max_chars: int = 220,
) -> str:
    pieces = [
        item.text
        for item in segments
        if item.end >= center_time - before and item.start <= center_time + after
    ]
    text = clean_text(" ".join(pieces))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"

