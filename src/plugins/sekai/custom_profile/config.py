# custom_profile 渲染配置（适配自 Haruki-Drawing-API src/settings.py 的 CUSTOM_PROFILE_*）
from pathlib import Path
from ..common import SEKAI_ASSET_DIR, SEKAI_DATA_DIR

# 素材/字体根目录：指向 LunaBot rip 资源缓存（data/sekai/assets/{region}/）
# 渲染前由调用方确保所需素材已下载到该目录
CUSTOM_PROFILE_ASSETS_DIR = Path(f"{SEKAI_ASSET_DIR}/rip/{{region}}")
CUSTOM_PROFILE_FONTS_DIR = Path(f"{SEKAI_ASSET_DIR}/rip/{{region}}/custom_profile/font")
CUSTOM_PROFILE_TMP_FONT_METADATA = None  # TMP字体元数据缺失时文字走 fonts 目录降级渲染
CUSTOM_PROFILE_SHAPE_SPRITE_DIR = Path(f"{SEKAI_ASSET_DIR}/rip/{{region}}/custom_profile/shape")
CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR = Path(f"{SEKAI_ASSET_DIR}/static_images/customprofile")

# 用户场景硬边界
CUSTOM_PROFILE_MAX_ELEMENTS = 256
CUSTOM_PROFILE_MAX_SCALE = 8.0
CUSTOM_PROFILE_MAX_TEXT_SIZE = 1024.0
CUSTOM_PROFILE_MAX_TEXT_LENGTH = 4096
# LunaBot 适配：pil 文本模式无 oversized 降级，放宽单图层像素上限（32M，约128MB RGBA）
CUSTOM_PROFILE_MAX_LAYER_PIXELS = 33554432
CUSTOM_PROFILE_MAX_SCENE_BYTES = 256 * 1024 * 1024
CUSTOM_PROFILE_PARALLEL_WORKERS = 1

# 进程级缓存
CUSTOM_PROFILE_GLYPH_CACHE_SIZE = 4096
CUSTOM_PROFILE_GLYPH_CACHE_MAX_BYTES = 64 * 1024 * 1024
CUSTOM_PROFILE_SPRITE_CACHE_SIZE = 512
CUSTOM_PROFILE_SPRITE_CACHE_MAX_BYTES = 128 * 1024 * 1024
