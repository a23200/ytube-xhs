from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.errors import PipelineError
from app.services.image_client import image_client
from app.services.runtime_store import ProjectPaths, write_json
from app.services.text_utils import clean_text

CARD_WIDTH = 1080
CARD_HEIGHT = 1350
CARD_BG = (248, 247, 243)
INK = (24, 27, 31)
MUTED = (102, 112, 125)
ACCENT = (223, 71, 89)
PANEL = (255, 255, 255)
LINE = (222, 226, 230)


def _safe_text(value: Any, default: str = "") -> str:
    text = clean_text(str(value or ""))
    return text or default


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: Any, limit: int = 90) -> str:
    text = _safe_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _page_number(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def _keyframes(keyframes_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    frames = []
    for frame in keyframes_payload.get("keyframes", []) or []:
        if not isinstance(frame, dict):
            continue
        frame_time = _as_float(frame.get("time"))
        frame_path = str(frame.get("path") or "")
        if frame_time is None and not frame_path:
            continue
        frames.append({**frame, "time": frame_time, "path": frame_path})
    return frames


def _resolve_frame(paths: ProjectPaths, value: Any) -> Optional[Path]:
    if not value:
        return None
    raw_path = Path(str(value))
    candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(paths.frames_dir.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or not resolved.name.startswith("frame_") or resolved.suffix.lower() not in {".jpg", ".jpeg"}:
        return None
    return resolved


def _frame_for_plan_item(paths: ProjectPaths, frames: List[Dict[str, Any]], item: Dict[str, Any], index: int) -> Dict[str, Any]:
    explicit = _resolve_frame(paths, item.get("source_frame_path"))
    if explicit is not None:
        for frame in frames:
            if _resolve_frame(paths, frame.get("path")) == explicit:
                return frame
        return {"time": _as_float(item.get("source_frame_time")), "path": str(explicit)}

    item_time = _as_float(item.get("source_frame_time"))
    if item_time is not None and frames:
        return min(frames, key=lambda frame: abs(float(frame.get("time") or 0) - item_time))
    if frames:
        return frames[min(index, len(frames) - 1)]
    return {"time": item_time, "path": None}


def _font(size: int, bold: bool = False):
    try:
        from PIL import ImageFont
    except Exception as exc:
        raise PipelineError(
            code="missing_dependency",
            message="Pillow is not installed, so image cards cannot be rendered.",
            step="rendering_cards",
            details={"dependency": "Pillow"},
        ) from exc

    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_width(draw: Any, text: str, font: Any) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _wrap_text(draw: Any, text: str, font: Any, max_width: int, max_lines: int) -> List[str]:
    text = _safe_text(text)
    if not text:
        return []
    lines: List[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1].rstrip("，。；,. ") + "..."
    return lines


def _draw_wrapped(draw: Any, xy: tuple[int, int], text: str, font: Any, fill: tuple[int, int, int], max_width: int, max_lines: int, line_gap: int) -> int:
    x, y = xy
    lines = _wrap_text(draw, text, font, max_width=max_width, max_lines=max_lines)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += int(bbox[3] - bbox[1]) + line_gap
    return y


def _cover_image(paths: ProjectPaths, frame: Dict[str, Any]):
    from PIL import Image, ImageEnhance, ImageFilter

    frame_path = _resolve_frame(paths, frame.get("path"))
    if frame_path is None:
        img = Image.new("RGB", (CARD_WIDTH, 620), (236, 240, 236))
        return img
    try:
        img = Image.open(frame_path).convert("RGB")
    except Exception:
        img = Image.new("RGB", (CARD_WIDTH, 620), (236, 240, 236))
        return img

    target_w, target_h = CARD_WIDTH - 120, 560
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = img.resize((int(src_w * scale), int(src_h * scale)))
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    cropped = resized.crop((left, top, left + target_w, top + target_h))
    cropped = ImageEnhance.Color(cropped).enhance(0.92)
    cropped = ImageEnhance.Contrast(cropped).enhance(0.94)
    return cropped.filter(ImageFilter.UnsharpMask(radius=1.0, percent=80, threshold=3))


def _generated_image(paths: ProjectPaths, generated_path: Optional[Path], cards_dir: Optional[Path] = None):
    if generated_path is None:
        return None
    from PIL import Image, ImageEnhance, ImageFilter

    try:
        resolved = generated_path.resolve()
        resolved.relative_to((cards_dir or paths.cards_dir).resolve())
        img = Image.open(resolved).convert("RGB")
    except Exception:
        return None

    target_w, target_h = CARD_WIDTH - 120, 560
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = img.resize((int(src_w * scale), int(src_h * scale)))
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    cropped = resized.crop((left, top, left + target_w, top + target_h))
    cropped = ImageEnhance.Color(cropped).enhance(0.96)
    cropped = ImageEnhance.Contrast(cropped).enhance(0.96)
    return cropped.filter(ImageFilter.UnsharpMask(radius=1.0, percent=70, threshold=3))


def _draw_rounded(draw: Any, box: tuple[int, int, int, int], radius: int, fill: tuple[int, int, int], outline: Optional[tuple[int, int, int]] = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _role_label(role: str) -> str:
    labels = {
        "cover": "封面",
        "point": "要点",
        "step": "步骤",
        "quote": "金句",
        "summary": "总结",
    }
    return labels.get(role, role or "图文")


def _plan_items(xhs_post: Dict[str, Any], content_assets: Dict[str, Any]) -> List[Dict[str, Any]]:
    image_plan = [item for item in xhs_post.get("image_plan", []) or [] if isinstance(item, dict)]
    if image_plan:
        return image_plan[:8]
    return [
        {
            "page": 1,
            "role": "cover",
            "caption": xhs_post.get("cover_text") or "原创观点封面",
            "content_point": content_assets.get("one_sentence_summary"),
        }
    ]


def _card_filename(page: int, role: str, index: int) -> str:
    if role == "cover" or index == 0:
        return "cover.png"
    if role == "summary":
        return "summary.png"
    return f"slide_{page - 1:02d}.png"


def _render_single_card(
    paths: ProjectPaths,
    *,
    metadata: Dict[str, Any],
    xhs_post: Dict[str, Any],
    item: Dict[str, Any],
    frame: Dict[str, Any],
    output_path: Path,
    page: int,
    index: int,
    style: str,
    generated_image_path: Optional[Path] = None,
    cards_dir: Optional[Path] = None,
) -> None:
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        raise PipelineError(
            code="missing_dependency",
            message="Pillow is not installed, so image cards cannot be rendered.",
            step="rendering_cards",
            details={"dependency": "Pillow"},
        ) from exc

    canvas = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), CARD_BG)
    draw = ImageDraw.Draw(canvas)
    title_font = _font(76 if index == 0 else 60, bold=True)
    body_font = _font(34)
    meta_font = _font(26)
    small_font = _font(22)

    role = _safe_text(item.get("role"), "point")
    title = _safe_text(item.get("caption"), xhs_post.get("cover_text") or "原创观点封面")
    if index == 0:
        title = _safe_text(xhs_post.get("cover_text"), title)
    caption = _safe_text(item.get("content_point"), item.get("caption") or xhs_post.get("hook"))
    video_title = _clip(metadata.get("title"), 42)
    frame_time = _as_float(item.get("source_frame_time"))
    if frame_time is None:
        frame_time = _as_float(frame.get("time"))

    image = _generated_image(paths, generated_image_path, cards_dir=cards_dir) or _cover_image(paths, frame)
    if index == 0:
        image_box = (60, 90, CARD_WIDTH - 60, 650)
        title_y = 720
    else:
        image_box = (60, 120, CARD_WIDTH - 60, 590)
        title_y = 670
    _draw_rounded(draw, (image_box[0] - 1, image_box[1] - 1, image_box[2] + 1, image_box[3] + 1), 32, PANEL, LINE)
    canvas.paste(image, image_box[:2])

    badge_text = f"{_role_label(role)} · P{page}"
    _draw_rounded(draw, (78, image_box[1] + 22, 78 + _text_width(draw, badge_text, small_font) + 42, image_box[1] + 72), 25, (255, 255, 255))
    draw.text((99, image_box[1] + 34), badge_text, font=small_font, fill=ACCENT)

    if frame_time is not None:
        time_text = f"{frame_time:.1f}s"
        width = _text_width(draw, time_text, small_font)
        _draw_rounded(draw, (CARD_WIDTH - 92 - width, image_box[3] - 72, CARD_WIDTH - 60, image_box[3] - 22), 25, (255, 255, 255))
        draw.text((CARD_WIDTH - 72 - width, image_box[3] - 60), time_text, font=small_font, fill=MUTED)

    y = _draw_wrapped(draw, (72, title_y), title, title_font, INK, CARD_WIDTH - 144, 3, 18)
    y += 18
    _draw_rounded(draw, (72, y, CARD_WIDTH - 72, min(CARD_HEIGHT - 170, y + 260)), 26, PANEL, LINE)
    _draw_wrapped(draw, (104, y + 34), caption, body_font, INK, CARD_WIDTH - 208, 4, 16)

    footer_y = CARD_HEIGHT - 120
    draw.line((72, footer_y - 28, CARD_WIDTH - 72, footer_y - 28), fill=LINE, width=2)
    draw.text((72, footer_y), video_title or "原创图文", font=meta_font, fill=MUTED)
    source_text = "基于原始信息二次创作"
    draw.text((CARD_WIDTH - 72 - _text_width(draw, source_text, meta_font), footer_y), source_text, font=meta_font, fill=MUTED)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)


def render_image_cards(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    xhs_post: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    image_prompts: Dict[str, Any],
    paths: ProjectPaths,
    style: str = "clean",
    platform: str = "xhs",
    output_filename: Optional[str] = None,
    cards_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    frames = _keyframes(keyframes_payload)
    items = _plan_items(xhs_post, content_assets)
    output_filename = output_filename or ("toutiao-image-cards.json" if platform == "toutiao" else "image-cards.json")
    cards_dir = cards_dir or (paths.toutiao_cards_dir if platform == "toutiao" else paths.cards_dir)
    prompt_by_page = {
        _page_number(item.get("page"), index + 1): item
        for index, item in enumerate(image_prompts.get("image_prompts", []) or [])
        if isinstance(item, dict)
    }
    cards = []
    used_names = set()
    external_image_error: Optional[Dict[str, Any]] = None
    for index, item in enumerate(items):
        page = _page_number(item.get("page"), index + 1)
        role = _safe_text(item.get("role"), "point")
        frame = _frame_for_plan_item(paths, frames, item, index)
        filename = _card_filename(page, role, index)
        if filename in used_names:
            filename = f"slide_{index:02d}.png"
        used_names.add(filename)
        output_path = cards_dir / filename
        prompt = prompt_by_page.get(page, {})
        generated_image_path = None
        image_generation: Dict[str, Any] = {"provider": "pillow_template_v1", "enabled": False}
        if image_client.enabled:
            generated_image_path = cards_dir / f"{output_path.stem}_generated.png"
            prompt_text = prompt.get("image_prompt") or _safe_text(item.get("content_point"), item.get("caption") or xhs_post.get("hook"))
            if external_image_error is not None:
                generated_image_path = None
                image_generation = {
                    "provider": "pillow_template_v1",
                    "enabled": True,
                    "fallback": True,
                    "failed_provider": "openai_compatible_images",
                    "error": external_image_error,
                    "skipped_external_request": True,
                }
            else:
                try:
                    image_generation = image_client.generate_to_file(
                        prompt_text,
                        generated_image_path,
                        attempts=1,
                        timeout_seconds=20,
                    )
                    image_generation["enabled"] = True
                except PipelineError as exc:
                    external_image_error = exc.to_dict()
                    generated_image_path = None
                    image_generation = {
                        "provider": "pillow_template_v1",
                        "enabled": True,
                        "fallback": True,
                        "failed_provider": "openai_compatible_images",
                        "error": external_image_error,
                    }
        _render_single_card(
            paths,
            metadata=metadata,
            xhs_post=xhs_post,
            item=item,
            frame=frame,
            output_path=output_path,
            page=page,
            index=index,
            style=style,
            generated_image_path=generated_image_path,
            cards_dir=cards_dir,
        )
        source_frame_path = str(_resolve_frame(paths, frame.get("path")) or frame.get("path") or "")
        cards.append(
            {
                "page": page,
                "role": role,
                "title": _safe_text(item.get("caption"), xhs_post.get("cover_text") or "原创观点封面"),
                "caption": _safe_text(item.get("content_point"), item.get("caption") or xhs_post.get("hook")),
                "source_frame_time": _as_float(item.get("source_frame_time")) if _as_float(item.get("source_frame_time")) is not None else frame.get("time"),
                "source_frame_path": source_frame_path or None,
                "layout": "vertical_4_5_media_text",
                "platform": platform,
                "style": style,
                "output_path": str(output_path),
                "image_prompt": prompt.get("image_prompt") or "",
                "image_generation": image_generation,
            }
        )

    if image_client.enabled and any(not card.get("image_generation", {}).get("fallback") for card in cards):
        renderer = "external_image_api_plus_pillow_layout_v1"
    elif image_client.enabled:
        renderer = "pillow_template_v1_with_external_image_fallback"
    else:
        renderer = "pillow_template_v1"
    payload = {"cards": cards, "card_count": len(cards), "aspect_ratio": "4:5", "platform": platform, "renderer": renderer}
    write_json(paths.analysis_dir / output_filename, payload)
    return payload
