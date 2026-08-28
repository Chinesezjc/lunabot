"""Asset loading + caching for the honor badge. The LAYOUT lives in ``widget.py``.

``compose_full_honor_image`` (async, Pillow) and the Skia route
(``skia.try_render_full_honor_payload``) both build the SAME ``HonorBadgeBox`` widget tree from
the same loaded images; this module only resolves the request's assets, keys the composed-image
cache, and renders the tree with Pillow.
"""

import asyncio
import logging

from ...utils import run_in_pool
from PIL import Image as _PILImage
ImageSource = _PILImage.Image  # LunaBot 适配：素材以 PIL.Image 传入

# 从 model.py 导入数据模型
from .assets import HONOR_ASSET_MANIFEST, honor_asset_specs
from .model import HonorRequest
from .widget import (
    build_honor_badge_canvas,
    # re-exported because custom_profile/renderer.py imports it from here.
    honor_group_uses_scroll_level as honor_group_uses_scroll_level,
)

# NOTE deliberately NOT re-exported: is_world_link_rank_style / resolve_event_rank_position. They
# have no importers, and resolve_event_rank_position's signature changed in the widget-tree port
# (PIL images -> size tuples). Keeping the old public name pointing at new semantics is how you
# break an out-of-tree caller silently; import them from .widget if you ever need them.

logger = logging.getLogger(__name__)


def compose_full_honor_image_from_loaded_assets(
    rqd: HonorRequest,
    images: dict[str, ImageSource | None],
):
    """Synchronous compose from already-resolved sources (the custom-profile renderer's path).

    Sources may be decoded images or lazy asset references. Renders the shared widget tree with
    ``Canvas.get_img_sync`` — same tree, same ops, same pixels as the async entry point below."""
    canvas = build_honor_badge_canvas(rqd, images)
    if canvas is None:
        return None
    return canvas.get_img_sync()


def build_full_honor_cache_key(rqd: HonorRequest) -> str:
    request_payload = rqd.model_dump(mode="json", exclude_none=False, exclude={"timezone"})
    asset_signatures = {
        image_key: get_image_asset_signature(ASSETS_BASE_DIR, getattr(rqd, path_field))
        for image_key, path_field in HONOR_ASSET_MANIFEST.items()
    }
    return build_rendered_image_cache_key(
        "full_honor_image",
        request_payload,
        asset_signatures=asset_signatures,
    )


# Skia shadow path (skia.py) re-exported so the route and the parity harness resolve it from
# the drawer namespace; kept in its own module so this file stays the Pillow entry point.
