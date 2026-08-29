from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image

from ...utils import run_in_pool
from .limits import validate_custom_profile_card
from .renderer import PROFILE_RENDER_VIEW_H, PROFILE_RENDER_VIEW_W, PNGRenderer
from .model import CustomProfileCardRenderRequest
from .config import (
    SEKAI_DATA_DIR,
    CUSTOM_PROFILE_ASSETS_DIR,
    CUSTOM_PROFILE_FONTS_DIR,
    CUSTOM_PROFILE_MAX_ELEMENTS,
    CUSTOM_PROFILE_MAX_LAYER_PIXELS,
    CUSTOM_PROFILE_MAX_SCALE,
    CUSTOM_PROFILE_MAX_SCENE_BYTES,
    CUSTOM_PROFILE_MAX_TEXT_LENGTH,
    CUSTOM_PROFILE_MAX_TEXT_SIZE,
    CUSTOM_PROFILE_PARALLEL_WORKERS,
    CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
    CUSTOM_PROFILE_TMP_FONT_METADATA,
    CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
)

logger = logging.getLogger(__name__)

REGION_CODES = {"cn", "jp", "tw", "en", "kr"}


def _require_path(name: str, path: Path | None, *, is_file: bool = False) -> Path:
    if path is None:
        raise RuntimeError(f"drawing.{name} is not configured")
    if not is_file:
        # LunaBot 适配：素材目录由预取按需创建，缺失时自动创建（素材文件级缺失由渲染器 exists() 降级）
        path.mkdir(parents=True, exist_ok=True)
        return path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"drawing.{name} does not exist: {path}")
    return path


def _expand_region_path(path: Path, region: str) -> Path:
    raw = str(path)
    if "{region}" not in raw:
        return path
    return Path(raw.replace("{region}", region))


def _region_path_candidates(path: Path, region: str) -> list[Path]:
    expanded = _expand_region_path(path, region)
    if expanded != path:
        return [expanded]

    candidates: list[Path] = []
    parts = list(path.parts)
    for index, part in enumerate(parts):
        if part in REGION_CODES:
            replaced = parts.copy()
            replaced[index] = region
            candidates.append(Path(*replaced))
        if part.endswith("-assets") and part[: -len("-assets")] in REGION_CODES:
            replaced = parts.copy()
            replaced[index] = f"{region}-assets"
            candidates.append(Path(*replaced))
    candidates.append(path)

    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _require_region_path(name: str, path: Path | None, region: str, *, is_file: bool = False) -> Path:
    if path is None:
        raise RuntimeError(f"drawing.{name} is not configured")
    candidates = _region_path_candidates(path, region)
    for candidate in candidates:
        if candidate.exists():
            return _require_path(name, candidate, is_file=is_file)
    return _require_path(name, candidates[0], is_file=is_file)


def _optional_region_file(name: str, path: Path | None, region: str) -> Path | None:
    if path is None:
        return None
    candidates = _region_path_candidates(path, region)
    for candidate in candidates:
        if candidate.exists():
            if not candidate.is_file():
                raise RuntimeError(f"drawing.{name} must be a file: {candidate}")
            return candidate
    if candidates:
        logger.warning("optional drawing.%s missing: %s", name, candidates[0])
        return None
    return None


def _render_custom_profile_card_sync(
    card: dict[str, Any],
    profile_context: dict[str, Any],
    resources: dict[str, Any],
    region: str,
) -> Image.Image:
    validate_custom_profile_card(
        card,
        max_elements=CUSTOM_PROFILE_MAX_ELEMENTS,
        max_scale=CUSTOM_PROFILE_MAX_SCALE,
        max_text_size=CUSTOM_PROFILE_MAX_TEXT_SIZE,
        max_text_length=CUSTOM_PROFILE_MAX_TEXT_LENGTH,
    )
    assets = _require_region_path("custom_profile_assets_dir", CUSTOM_PROFILE_ASSETS_DIR, region)
    fonts = _require_region_path("custom_profile_fonts_dir", CUSTOM_PROFILE_FONTS_DIR, region)
    tmp_font_metadata = _optional_region_file(
        "custom_profile_tmp_font_metadata",
        CUSTOM_PROFILE_TMP_FONT_METADATA,
        region,
    )
    shape_sprite_dir = _require_region_path(
        "custom_profile_shape_sprite_dir",
        CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
        region,
    )
    unity_ui_sprite_dir = _require_region_path(
        "custom_profile_unity_ui_sprite_dir",
        CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
        region,
    )

    # LunaBot 适配：masterdata 指向本地 masterdata 缓存目录（非 None），
    # 让 honor/stamp/card 的 fallback 构造可用；resources 优先，缺失表 load_index 返回 {}
    masterdata_dir = Path(SEKAI_DATA_DIR) / "assets" / "masterdata" / region
    renderer = PNGRenderer(
        masterdata=masterdata_dir,
        assets=assets,
        fonts=fonts,
        resources=resources,
        tmp_font_metadata=tmp_font_metadata,
        shape_sprite_dir=shape_sprite_dir,
        profile_context=profile_context,
        parallel_workers=max(1, int(CUSTOM_PROFILE_PARALLEL_WORKERS or 1)),
        parallel_stage="transform",
        clip_canvas_transform=True,
        # LunaBot 适配：无游戏字体/TMP metadata，走纯 Pillow 文本渲染
        tmp_dynamic_sdf=False,
        tmp_text_render_mode="pil",
        canvas_w=int(PROFILE_RENDER_VIEW_W),
        canvas_h=int(PROFILE_RENDER_VIEW_H),
        origin_x=PROFILE_RENDER_VIEW_W / 2.0,
        origin_y=PROFILE_RENDER_VIEW_H / 2.0,
        unity_ui_sprite_dir=unity_ui_sprite_dir,
        region=region,
        max_layer_pixels=CUSTOM_PROFILE_MAX_LAYER_PIXELS,
        max_scene_bytes=CUSTOM_PROFILE_MAX_SCENE_BYTES,
    )
    return renderer.render_card(card)


async def compose_custom_profile_card_image(request: CustomProfileCardRenderRequest) -> Image.Image:
    return await run_in_pool(
        _render_custom_profile_card_sync,
        dict(request.card),
        dict(request.profile_context),
        dict(request.resources),
        request.region,
    )
