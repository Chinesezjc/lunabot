from ..utils import *
from .common import *
from .handler import SekaiHandlerContext


# 通过角色ID获取角色头像
def get_chara_icon_by_chara_id(cid: int, size: int = None, raise_exc=True, default=None, unit=None):
    """
    通过角色ID获取角色头像
    """
    nickname = get_character_first_nickname(cid)
    if not nickname:
        if raise_exc: raise Exception(f"要获取的icon的角色ID={cid}错误")
        else: return default
    path = f"chara_icon/{nickname}"
    if unit is not None and unit != "piapro" and nickname == "miku":
        path += f"_{unit}"
    path += ".png"
    img = SekaiHandlerContext.from_region('jp').static_imgs.get(path)
    if size is not None:
        img = img.resize((size, size))
    return img
    
# 通过角色昵称获取角色头像
def get_chara_icon_by_nickname(nickname: str, size: int = None, raise_exc=True, default=None, unit=None):
    """
    通过角色昵称获取角色头像
    """
    cid = get_cid_by_nickname(nickname)
    if not cid:
        if raise_exc: raise Exception(f"要获取的icon的角色昵称错误")
        else: return default
    return get_chara_icon_by_chara_id(cid, size, raise_exc, default, unit)

# 获取团logo
def get_unit_logo(unit: str, size: int = None):
    img = SekaiHandlerContext.from_region('jp').static_imgs.get(f"logo_{unit}.png")
    if size is not None:
        img = img.resize((size, size))
    return img

# 获取团图标
def get_unit_icon(unit: str, size: int = None):
    img = SekaiHandlerContext.from_region('jp').static_imgs.get(f"icon_{unit}.svg")
    if size is not None:
        img = img.resize((size, size))
    return img

# 获取属性图标
def get_attr_icon(attr: str, size: int = None):
    img = SekaiHandlerContext.from_region('jp').static_imgs.get(f"card/attr_icon_{attr}.png")
    if size is not None:
        img = img.resize((size, size))
    return img


SEKAI_BLUE_BG = RandomTriangleBg("sekai.bg_preset.blue")
SEKAI_RED_BG = RandomTriangleBg("sekai.bg_preset.red")
SEKAI_BLUE_BG_DAY   = RandomTriangleBg("sekai.bg_preset.blue", dt = datetime.strptime("10:00", "%H:%M"))
SEKAI_BLUE_BG_NIGHT = RandomTriangleBg("sekai.bg_preset.blue", dt = datetime.strptime("22:00", "%H:%M"))

BG_PADDING = 20
WIDGET_BG_COLOR_CFG = config.item("draw.widget_bg_color")
WIDGET_BG_RADIUS_CFG = config.item("draw.widget_bg_radius")
BLURGLASS_CFG = config.item("draw.blurglass")

# 统一的半透明白色圆角矩形背景
def roundrect_bg(
    fill: Color | ConfigItem=WIDGET_BG_COLOR_CFG, 
    radius: int | ConfigItem=WIDGET_BG_RADIUS_CFG, 
    alpha: int=None,
    blurglass: bool=None, 
    blurglass_kwargs: dict={}
):
    """
    统一的半透明白色圆角矩形背景
    """
    if blurglass is None:
        blurglass = BLURGLASS_CFG.get()
    fill = get_cfg_or_value(fill)
    radius = get_cfg_or_value(radius)
    if alpha is not None:
        fill = (*fill[:3], alpha)
    return RoundRectBg(fill, radius, blurglass=blurglass, blurglass_kwargs=blurglass_kwargs)


COMMON_BG_NAMES = [
    "bg/title_background.png",
    "bg/bg_area_1.png",
    "bg/bg_area_2.png",
    "bg/bg_area_3.png",
    "bg/bg_area_4.png",
    "bg/bg_area_11.png",
    "bg/bg_area_12.png",
    "bg/bg_area_13.png",
]
GROUP_BG_NAMES = {
    UNIT_LN:   ["bg/bg_area_5.png",  "bg/bg_area_17.png", "bg/bg_light_sound.png"],
    UNIT_MMJ:  ["bg/bg_area_7.png",  "bg/bg_area_18.png", "bg/bg_idol.png"],
    UNIT_VBS:  ["bg/bg_area_8.png",  "bg/bg_area_19.png", "bg/bg_street.png"],
    UNIT_WS:   ["bg/bg_area_9.png",  "bg/bg_area_20.png", "bg/bg_theme_park.png"],
    UNIT_25:   ["bg/bg_area_10.png", "bg/bg_area_21.png", "bg/bg_school_refusal.png"],
    UNIT_VS:   ["bg/bg_virtual_singer.png"]
}

# 随机选择团队背景
def random_unit_bg(unit: str = None):
    """
    随机选择团队背景
    unit为None时随机选择一个通用背景
    """
    ctx = SekaiHandlerContext.from_region('jp')
    if unit is None:
        bg_name = random.choice(COMMON_BG_NAMES)
        img = ctx.static_imgs.get(bg_name)
    else:
        bg_name = random.choice(GROUP_BG_NAMES.get(unit, COMMON_BG_NAMES))
        img = ctx.static_imgs.get(bg_name)
    return ImageBg(img)


DEFAULT_WATERMARK_CFG = config.item("draw.default_watermark")

# 在画布上添加水印
def add_watermark(canvas: Canvas, text: str | ConfigItem=DEFAULT_WATERMARK_CFG, size=12):
    """
    在画布上添加水印
    """
    text = get_cfg_or_value(text)
    frame_watermark = Frame().set_content_align('rb').set_padding(0)
    frame_canvas = Frame().set_content_align(canvas.get_content_align()).set_padding(0).set_size((canvas.w, canvas.h))
    s1 = TextStyle(font=DEFAULT_FONT, size=size, color=(255, 255, 255, 256))
    s2 = TextStyle(font=DEFAULT_FONT, size=size, color=(75, 75, 75, 256))
    offset1 = (int(16 - BG_PADDING * 0.5), 16)
    offset2 = (offset1[0] + 1, offset1[1] + 1)
    text1 = TextBox(text, style=s1).set_omit_parent_bg(True).set_offset(offset1)
    text2 = TextBox(text, style=s2).set_omit_parent_bg(True).set_offset(offset2)
    items = canvas.items
    canvas.set_items([])
    canvas.set_padding(BG_PADDING)
    for item in items:
        frame_canvas.add_item(item)
    frame_watermark.add_item(frame_canvas)
    frame_watermark.add_item(text2)
    frame_watermark.add_item(text1)
    canvas.add_item(frame_watermark).set_size(None)


DIFF_COLORS = {
    "easy": (102, 221, 17, 255),
    "normal": (51,187, 238, 255),
    "hard": (255, 170, 0, 255),
    "expert": (238, 68, 102, 255),
    "master": (187, 51, 238, 255),
    "append": LinearGradient((182, 144, 247, 255), (243, 132, 220, 255), (0, 0), (1, 1)),
}
PLAY_RESULT_COLORS = {
    'not_clear': (69, 67, 104, 255),
    'clear': (255, 226, 118, 255),
    'fc': (253, 167, 249, 255),
    'ap': (63, 230, 228, 255),
}


