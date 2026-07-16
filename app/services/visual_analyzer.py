import re
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.config import settings
from app.services.errors import PipelineError
from app.services.media_utils import command_env, find_command
from app.services.runtime_store import ProjectPaths, write_json

FRAME_FILENAME_RE = re.compile(r"^frame_\d{4}\.jpg$")


class OCRProvider(ABC):
    name: str

    @abstractmethod
    def available(self) -> Tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    def read_text(self, image_path: Path) -> Dict[str, Any]:
        raise NotImplementedError


class PaddleOCRProvider(OCRProvider):
    name = "paddleocr"

    def __init__(self, language: str) -> None:
        self.language = "ch" if language.startswith("zh") else "en"
        self._engine = None
        self._error = ""
        try:
            from paddleocr import PaddleOCR

            self._engine = PaddleOCR(use_angle_cls=True, lang=self.language, show_log=False)
        except Exception as exc:
            self._error = str(exc)

    def available(self) -> Tuple[bool, str]:
        if self._engine is None:
            return False, self._error or "PaddleOCR is not available."
        return True, ""

    def read_text(self, image_path: Path) -> Dict[str, Any]:
        ok, error = self.available()
        if not ok:
            return {"ocr_text": "", "confidence": 0.0, "error": error}
        result = self._engine.ocr(str(image_path), cls=True)
        pieces: List[str] = []
        confidences: List[float] = []
        for block in result or []:
            for line in block or []:
                if len(line) < 2:
                    continue
                text_info = line[1]
                if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                    pieces.append(str(text_info[0]))
                    try:
                        confidences.append(float(text_info[1]))
                    except (TypeError, ValueError):
                        pass
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return {
            "ocr_text": " ".join(piece.strip() for piece in pieces if piece.strip()),
            "confidence": round(confidence, 4),
            "error": "",
        }


class NullOCRProvider(OCRProvider):
    name = "none"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def available(self) -> Tuple[bool, str]:
        return False, self.reason

    def read_text(self, image_path: Path) -> Dict[str, Any]:
        return {"ocr_text": "", "confidence": 0.0, "error": self.reason}


class TesseractOCRProvider(OCRProvider):
    name = "tesseract"

    def __init__(self, language: str) -> None:
        self.requested_language = "chi_sim+eng" if language.startswith("zh") else "eng"
        self.language = self.requested_language
        self._command = find_command("tesseract")
        self._error = "" if self._command else "tesseract command is not available."
        self._warning = ""
        if self._command:
            self.language, self._warning, self._error = self._resolve_language(self.requested_language)

    def _available_languages(self) -> Set[str]:
        if not self._command:
            return set()
        try:
            result = subprocess.run(
                [self._command, "--list-langs"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env=command_env(),
            )
        except Exception as exc:
            self._error = f"Could not list tesseract languages: {exc}"
            return set()
        if result.returncode != 0:
            self._error = f"Could not list tesseract languages: {(result.stderr or result.stdout)[-500:]}"
            return set()
        return {
            line.strip()
            for line in (result.stdout or "").splitlines()
            if line.strip() and not line.lower().startswith("list of available languages")
        }

    def _resolve_language(self, requested_language: str) -> Tuple[str, str, str]:
        languages = self._available_languages()
        if not languages:
            return requested_language, "", self._error or "No tesseract language data is available."
        requested_parts = [part for part in requested_language.split("+") if part]
        available_parts = [part for part in requested_parts if part in languages]
        if available_parts == requested_parts:
            return requested_language, "", ""
        if requested_language.startswith("chi_sim") and "eng" in languages:
            return (
                "eng",
                "Tesseract Chinese language data chi_sim is not installed; falling back to eng OCR.",
                "",
            )
        if available_parts:
            fallback = "+".join(available_parts)
            return (
                fallback,
                f"Tesseract language data for {requested_language} is incomplete; falling back to {fallback}.",
                "",
            )
        return requested_language, "", f"Tesseract language data for {requested_language} is not installed."

    def available(self) -> Tuple[bool, str]:
        if not self._command:
            return False, self._error
        if self._error:
            return False, self._error
        return True, ""

    def read_text(self, image_path: Path) -> Dict[str, Any]:
        ok, error = self.available()
        if not ok:
            return {"ocr_text": "", "confidence": 0.0, "error": error}
        try:
            result = subprocess.run(
                [
                    self._command or "tesseract",
                    str(image_path),
                    "stdout",
                    "-l",
                    self.language,
                    "--psm",
                    "6",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                env=command_env(),
            )
        except Exception as exc:
            return {"ocr_text": "", "confidence": 0.0, "error": f"Tesseract failed: {exc}"}
        if result.returncode != 0:
            return {
                "ocr_text": "",
                "confidence": 0.0,
                "error": f"Tesseract failed: {result.stderr[-500:]}",
            }
        text = " ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
        return {"ocr_text": text, "confidence": 0.0, "error": self._warning}


def _label_brightness(value: float) -> str:
    if value < 70:
        return "low"
    if value > 185:
        return "high"
    return "medium"


def _label_sharpness(value: float) -> str:
    if value < 40:
        return "soft_or_blurry"
    if value > 180:
        return "sharp"
    return "moderate"


def _label_color_tone(red: float, blue: float) -> str:
    if red - blue > 12:
        return "warm"
    if blue - red > 12:
        return "cool"
    return "neutral"


def _frame_metrics(image_path: Path) -> Dict[str, Any]:
    try:
        import cv2
    except Exception as exc:
        return {"available": False, "error": f"OpenCV unavailable for visual metrics: {exc}"}
    image = cv2.imread(str(image_path))
    if image is None:
        return {"available": False, "error": f"Could not read frame image: {image_path}"}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    height, width = image.shape[:2]
    mean_b, _mean_g, mean_r = [float(value) for value in image.reshape(-1, 3).mean(axis=0)]
    return {
        "available": True,
        "width": int(width),
        "height": int(height),
        "brightness": round(brightness, 2),
        "sharpness": round(sharpness, 2),
        "brightness_label": _label_brightness(brightness),
        "sharpness_label": _label_sharpness(sharpness),
        "color_tone": _label_color_tone(mean_r, mean_b),
        "error": "",
    }


def _visual_summary(ocr_text: str, metrics: Dict[str, Any], metrics_warning: Optional[str]) -> str:
    pieces = []
    if ocr_text:
        pieces.append(f"OCR text detected: {ocr_text[:180]}")
    else:
        pieces.append("No OCR text detected or OCR provider unavailable.")
    if metrics.get("available"):
        pieces.append(
            "Frame metrics: "
            f"{metrics.get('width')}x{metrics.get('height')}, "
            f"{metrics.get('brightness_label')} brightness, "
            f"{metrics.get('sharpness_label')} clarity, "
            f"{metrics.get('color_tone')} color tone."
        )
    elif metrics_warning:
        pieces.append(f"Frame metrics unavailable: {metrics_warning}")
    return " ".join(pieces)


def _resolve_standard_frame(paths: ProjectPaths, value: Any) -> Optional[Path]:
    if not value:
        return None
    raw_path = Path(str(value))
    candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(paths.frames_dir.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or FRAME_FILENAME_RE.fullmatch(resolved.name) is None:
        return None
    return resolved


def _select_ocr_provider(language: str, use_ocr: bool = True) -> OCRProvider:
    if not use_ocr:
        return NullOCRProvider("OCR disabled for this project.")
    requested = settings.ocr_provider
    if requested not in {"auto", "paddleocr", "tesseract", "none"}:
        return NullOCRProvider(
            f"Unsupported XHS_OCR_PROVIDER={requested!r}. Use auto, paddleocr, tesseract, or none."
        )

    if requested in {"auto", "paddleocr"}:
        paddle = PaddleOCRProvider(language)
        ok, reason = paddle.available()
        if ok:
            return paddle
        if requested == "paddleocr":
            return NullOCRProvider(
                "PaddleOCR was requested but is unavailable. OCR text is empty. "
                f"Provider error: {reason}"
            )

    if requested in {"auto", "tesseract"}:
        tesseract = TesseractOCRProvider(language)
        ok, reason = tesseract.available()
        if ok:
            return tesseract
        if requested == "tesseract":
            return NullOCRProvider(
                "Tesseract OCR was requested but is unavailable. OCR text is empty. "
                f"Provider error: {reason}"
            )

    if requested == "none":
        return NullOCRProvider("OCR disabled by XHS_OCR_PROVIDER=none.")

    return NullOCRProvider(
        "No OCR provider is available. Install paddleocr+paddlepaddle or tesseract to enable OCR."
    )


def analyze_visuals(
    keyframes_payload: Dict[str, Any],
    language: str,
    paths: ProjectPaths,
    use_ocr: bool = True,
) -> Dict[str, Any]:
    if keyframes_payload.get("skipped") and not keyframes_payload.get("keyframes"):
        warning = str(keyframes_payload.get("skip_reason") or "No keyframes are available; visual analysis was skipped.")
        payload = {
            "ocr_provider": "none",
            "requested_ocr_provider": settings.ocr_provider if use_ocr else "none",
            "ocr_enabled": False,
            "warnings": [warning],
            "frames": [],
            "skipped": True,
            "skip_reason": warning,
            "analysis_mode": keyframes_payload.get("analysis_mode") or "transcript_only",
        }
        write_json(paths.analysis_dir / "visual-analysis.json", payload)
        return payload

    provider = _select_ocr_provider(language, use_ocr=use_ocr)
    ok, reason = provider.available()

    warnings: List[str] = []
    if not ok:
        warnings.append(reason)

    frames = []
    for frame in keyframes_payload.get("keyframes", []):
        if not isinstance(frame, dict):
            warning = "Skipping invalid keyframe item during visual analysis."
            if warning not in warnings:
                warnings.append(warning)
            continue
        image_path = _resolve_standard_frame(paths, frame.get("path"))
        if image_path is None:
            warning = f"Skipping keyframe with invalid or non-standard frame path: {frame.get('path')}"
            if warning not in warnings:
                warnings.append(warning)
            continue
        metrics = _frame_metrics(image_path)
        metrics_warning = str(metrics.get("error") or "") if not metrics.get("available") else ""
        if metrics_warning and metrics_warning not in warnings:
            warnings.append(metrics_warning)
        try:
            ocr = provider.read_text(image_path)
        except Exception as exc:
            ocr = {"ocr_text": "", "confidence": 0.0, "error": f"{provider.name} OCR failed: {exc}"}
        ocr_text = ocr.get("ocr_text", "")
        confidence = float(ocr.get("confidence") or 0.0)
        if ocr.get("error") and ocr.get("error") not in warnings:
            warnings.append(ocr["error"])
        frames.append(
            {
                "time": frame.get("time"),
                "path": str(image_path),
                "ocr_text": ocr_text,
                "visual_summary": _visual_summary(ocr_text, metrics, metrics_warning),
                "detected_objects": [],
                "screen_text_confidence": confidence,
                "ocr_provider": provider.name,
                "frame_metrics": metrics,
            }
        )

    if not frames:
        raise PipelineError(
            code="no_valid_visual_frames",
            message="Visual analysis found no valid standard keyframe images to analyze.",
            step="analyzing_visuals",
            details={"warnings": warnings},
        )

    payload = {
        "ocr_provider": provider.name,
        "requested_ocr_provider": settings.ocr_provider if use_ocr else "none",
        "ocr_enabled": use_ocr,
        "warnings": warnings,
        "frames": frames,
    }
    write_json(paths.analysis_dir / "visual-analysis.json", payload)
    return payload
