from app.schemas.models import TranscriptSegment
from app.services.text_utils import clean_text, normalize_segments, timestamp_to_seconds, transcript_window


def test_clean_text_removes_tags_and_spaces():
    assert clean_text("<c> hello </c>  &amp;   world") == "hello & world"


def test_timestamp_to_seconds():
    assert timestamp_to_seconds("00:01:02.500") == 62.5
    assert timestamp_to_seconds("01:02.500") == 62.5


def test_normalize_segments_dedupes_and_merges_short_segments():
    segments = normalize_segments(
        [
            TranscriptSegment(start=0, end=1, text="  hello  ", source="subtitle"),
            TranscriptSegment(start=1.2, end=2, text="world", source="subtitle"),
            TranscriptSegment(start=2.1, end=3, text="world", source="subtitle"),
        ]
    )
    assert len(segments) == 1
    assert segments[0].text == "hello world"


def test_transcript_window_selects_nearby_text():
    segments = [
        TranscriptSegment(start=0, end=2, text="first", source="subtitle"),
        TranscriptSegment(start=10, end=12, text="second", source="subtitle"),
    ]
    assert transcript_window(segments, center_time=11, before=2, after=2) == "second"

