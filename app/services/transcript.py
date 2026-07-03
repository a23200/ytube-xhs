import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.models import TranscriptSegment
from app.services.config import settings
from app.services.errors import PipelineError
from app.services.media_utils import require_command, run_command
from app.services.runtime_store import ProjectPaths, write_json
from app.services.text_utils import clean_text, normalize_segments, timestamp_to_seconds

TIME_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{3})\s+-->\s+(?P<end>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{3})"
)
ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".ass", ".ssa", ".json3", ".srv1", ".srv2", ".srv3", ".ttml", ".dfxp", ".xml"}


def _parse_vtt_or_srt(path: Path, source: str) -> List[TranscriptSegment]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    segments: List[TranscriptSegment] = []
    i = 0
    while i < len(lines):
        match = TIME_RANGE_RE.search(lines[i])
        if not match:
            i += 1
            continue
        start = timestamp_to_seconds(match.group("start"))
        end = timestamp_to_seconds(match.group("end"))
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            if not TIME_RANGE_RE.search(lines[i]) and not lines[i].strip().isdigit():
                text_lines.append(lines[i].strip())
            i += 1
        text = clean_text(" ".join(text_lines))
        if text:
            segments.append(TranscriptSegment(start=start, end=end, text=text, source=source))
    return normalize_segments(segments)


def _parse_json3(path: Path, source: str) -> List[TranscriptSegment]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments: List[TranscriptSegment] = []
    for event in payload.get("events", []):
        if "segs" not in event:
            continue
        text = clean_text("".join(seg.get("utf8", "") for seg in event.get("segs", [])))
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000.0
        duration = float(event.get("dDurationMs", 0)) / 1000.0
        end = start + max(duration, 0.5)
        segments.append(TranscriptSegment(start=start, end=end, text=text, source=source))
    return normalize_segments(segments)


def _xml_time_to_seconds(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("ms"):
            return float(text[:-2]) / 1000.0
        if text.endswith("s"):
            return float(text[:-1])
        if ":" in text:
            return timestamp_to_seconds(text)
        return float(text)
    except ValueError:
        return None


def _xml_milliseconds_to_seconds(value: Any) -> Optional[float]:
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def _parse_xml_subtitle(path: Path, source: str) -> List[TranscriptSegment]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    segments: List[TranscriptSegment] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"text", "p"}:
            continue

        start = _xml_time_to_seconds(element.attrib.get("start") or element.attrib.get("begin"))
        duration = _xml_time_to_seconds(element.attrib.get("dur"))
        end = _xml_time_to_seconds(element.attrib.get("end"))
        if start is None and element.attrib.get("t") is not None:
            start = _xml_milliseconds_to_seconds(element.attrib.get("t"))
        if duration is None and element.attrib.get("d") is not None:
            duration = _xml_milliseconds_to_seconds(element.attrib.get("d"))
        if start is None:
            continue
        if end is None:
            end = start + max(duration if duration is not None else 0.5, 0.5)

        text = clean_text(" ".join(piece.strip() for piece in element.itertext() if piece.strip()))
        if text:
            segments.append(TranscriptSegment(start=start, end=max(start + 0.1, end), text=text, source=source))
    return normalize_segments(segments)


def _parse_ass_time(value: str) -> float:
    pieces = value.strip().split(":")
    if len(pieces) != 3:
        raise ValueError(f"Invalid ASS timestamp: {value}")
    hours = int(pieces[0])
    minutes = int(pieces[1])
    seconds = float(pieces[2])
    return hours * 3600 + minutes * 60 + seconds


def _clean_ass_text(value: str) -> str:
    value = value.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    value = ASS_OVERRIDE_RE.sub("", value)
    return clean_text(value)


def _parse_ass_or_ssa(path: Path, source: str) -> List[TranscriptSegment]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    format_fields: List[str] = []
    in_events = False
    segments: List[TranscriptSegment] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = stripped.strip("[]").lower() == "events"
            continue
        prefix, _, value = stripped.partition(":")
        if prefix.lower() == "format" and in_events:
            format_fields = [field.strip().lower() for field in value.split(",")]
            continue
        if prefix.lower() != "dialogue":
            continue
        if format_fields:
            text_index = format_fields.index("text") if "text" in format_fields else len(format_fields) - 1
            pieces = value.split(",", max(text_index, 0))
            if len(pieces) <= text_index:
                continue
            fields = dict(zip(format_fields[:text_index], [piece.strip() for piece in pieces[:text_index]]))
            text = pieces[text_index]
            start_raw = fields.get("start")
            end_raw = fields.get("end")
        else:
            pieces = value.split(",", 9)
            if len(pieces) < 10:
                continue
            start_raw = pieces[1].strip()
            end_raw = pieces[2].strip()
            text = pieces[9]
        if not start_raw or not end_raw:
            continue
        try:
            start = _parse_ass_time(start_raw)
            end = _parse_ass_time(end_raw)
        except ValueError:
            continue
        cleaned_text = _clean_ass_text(text)
        if cleaned_text:
            segments.append(TranscriptSegment(start=start, end=end, text=cleaned_text, source=source))
    return normalize_segments(segments)


def _find_subtitle(metadata: Dict[str, Any], paths: ProjectPaths) -> Optional[Path]:
    subtitle_file = metadata.get("subtitle_file")
    if subtitle_file and Path(subtitle_file).exists():
        return Path(subtitle_file)
    for path in sorted(paths.source_dir.glob("subtitles.*")):
        return path
    for path in sorted(paths.source_dir.glob("*")):
        if path.suffix.lower() in SUBTITLE_EXTENSIONS:
            return path
    return None


def _extract_audio(metadata: Dict[str, Any], paths: ProjectPaths) -> Path:
    existing_audio = metadata.get("audio_file")
    if existing_audio and Path(existing_audio).exists():
        return Path(existing_audio)
    video_file = metadata.get("video_file")
    if not video_file or not Path(video_file).exists():
        raise PipelineError(
            code="media_file_missing",
            message="No video or audio file is available for Whisper transcription.",
            step="transcript",
            details={"video_file": video_file, "audio_file": existing_audio},
        )
    require_command("ffmpeg", "transcript")
    audio_path = paths.source_dir / "audio.mp3"
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_file),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(audio_path),
        ],
        step="transcript",
        timeout=900,
    )
    return audio_path


def _transcribe_with_whisper(audio_path: Path, language: str) -> List[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise PipelineError(
            code="missing_dependency",
            message="faster-whisper is not installed, so videos without usable subtitles cannot be transcribed.",
            step="transcript",
            details={"dependency": "faster-whisper"},
        ) from exc

    whisper_context = {
        "model": settings.whisper_model,
        "device": settings.whisper_device,
        "compute_type": settings.whisper_compute_type,
        "language": language,
        "audio_file": str(audio_path),
    }
    try:
        model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            local_files_only=True,
        )
    except Exception as exc:
        local_error = exc
        try:
            model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                local_files_only=False,
            )
        except Exception as exc:
            raise PipelineError(
                code="whisper_model_unavailable",
                message=(
                    "faster-whisper could not load the requested model from local cache or remote download. "
                    "Check XHS_WHISPER_MODEL, XHS_WHISPER_DEVICE, XHS_WHISPER_COMPUTE_TYPE, network access, "
                    "and local model cache."
                ),
                step="transcript",
                details={
                    **whisper_context,
                    "local_error": str(local_error),
                    "local_error_type": type(local_error).__name__,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            ) from exc

    try:
        segments_iter, info = model.transcribe(str(audio_path), language=language if language != "zh" else "zh")
        segments = [
            TranscriptSegment(
                start=float(item.start),
                end=float(item.end),
                text=item.text,
                source=f"faster-whisper:{settings.whisper_model}",
                importance=0.0,
            )
            for item in segments_iter
        ]
    except Exception as exc:
        raise PipelineError(
            code="whisper_failed",
            message="faster-whisper loaded but failed to transcribe the audio.",
            step="transcript",
            details={**whisper_context, "error": str(exc), "error_type": type(exc).__name__},
        ) from exc
    normalized = normalize_segments(segments)
    if not normalized:
        raise PipelineError(
            code="empty_transcript",
            message="Whisper completed but produced no transcript segments.",
            step="transcript",
            details={**whisper_context, "detected_language": getattr(info, "language", None)},
        )
    return normalized


def build_transcript(
    metadata: Dict[str, Any],
    language: str,
    use_whisper: bool,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    subtitle_path = _find_subtitle(metadata, paths)
    transcript_source = "none"
    if subtitle_path:
        if subtitle_path.suffix.lower() in {".vtt", ".srt"}:
            transcript_source = "subtitle"
            segments = _parse_vtt_or_srt(subtitle_path, source=f"subtitle:{subtitle_path.name}")
        elif subtitle_path.suffix.lower() in {".ass", ".ssa"}:
            transcript_source = "subtitle"
            segments = _parse_ass_or_ssa(subtitle_path, source=f"subtitle:{subtitle_path.name}")
        elif subtitle_path.suffix.lower() == ".json3":
            transcript_source = "subtitle"
            segments = _parse_json3(subtitle_path, source=f"subtitle:{subtitle_path.name}")
        elif subtitle_path.suffix.lower() in {".srv1", ".srv2", ".srv3", ".ttml", ".dfxp", ".xml"}:
            transcript_source = "subtitle"
            segments = _parse_xml_subtitle(subtitle_path, source=f"subtitle:{subtitle_path.name}")
        else:
            segments = []
        if segments:
            (paths.source_dir / "transcript_source.txt").write_text(
                subtitle_path.read_text(encoding="utf-8", errors="ignore"),
                encoding="utf-8",
            )
        elif not use_whisper:
            raise PipelineError(
                code="subtitle_parse_failed",
                message="A subtitle file was found but could not be parsed, and Whisper is disabled.",
                step="transcript",
                details={"subtitle_file": str(subtitle_path)},
            )
    else:
        segments = []

    if not segments:
        if not use_whisper:
            raise PipelineError(
                code="no_transcript_source",
                message="No usable subtitles were found and use_whisper=false.",
                step="transcript",
            )
        transcript_source = "faster-whisper"
        audio_path = _extract_audio(metadata, paths)
        segments = _transcribe_with_whisper(audio_path, language=language)
        metadata["audio_file"] = str(audio_path)

    payload = {
        "source": transcript_source,
        "language": language,
        "segments": [segment.model_dump(mode="json") for segment in segments],
        "segment_count": len(segments),
    }
    write_json(paths.transcript_dir / "transcript.json", payload)
    return payload
