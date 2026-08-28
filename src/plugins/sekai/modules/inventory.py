from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..suite import Suite
from .profile import (
    get_detailed_profile,
    get_detailed_profile_card,
    get_detailed_profile_card_filter,
    get_user_data_mode,
)


# ======================= 常量 ======================= #

INVENTORY_HELP = """使用方式:
/查背包
/查背包 水晶
/查背包 火罐
/查背包 ms材料
/查背包 记忆

空参数默认不展示水晶、火罐、MySekai 材料和记忆。"""

INVENTORY_FILTER_DEFAULT = ""
INVENTORY_FILTER_JEWEL = "jewel"
INVENTORY_FILTER_BOOST = "boost"
INVENTORY_FILTER_MYSEKAI = "mysekai"
INVENTORY_FILTER_MEMORY = "memory"

INVENTORY_FILTER_KEYWORDS = {
    INVENTORY_FILTER_JEWEL: ["水晶", "钻石", "石头", "彩石", "晶石"],
    INVENTORY_FILTER_BOOST: ["火罐", "演出能量", "体力", "能量"],
    INVENTORY_FILTER_MYSEKAI: ["mysekai材料", "mysekai素材", "ms材料", "ms素材", "ms"],
    INVENTORY_FILTER_MEMORY: ["记忆", "回忆", "memoria", "memory"],
}

INVENTORY_SECTION_ORDER = [
    ("currency", "货币"),
    ("boost", "演出能量"),
    ("basic", "基础材料"),
    ("training", "育成材料"),
    ("costume", "服装材料"),
    ("music", "音乐与演唱"),
    ("tickets", "招募与兑换券"),
    ("event", "活动材料"),
    ("memory", "记忆"),
    ("mysekai", "MySekai 材料"),
    ("other", "其他"),
]

# ======================= 渲染常量 ======================= #

PANEL_WIDTH = 1180
TILE_WIDTH = 268
TILE_HEIGHT = 112
TILE_COL_COUNT = 4
TILE_GAP = 10
ICON_SIZE = 58
ITEM_TEXT_WIDTH = 178

TITLE_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(45, 50, 70))
SECTION_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(46, 52, 72))
COUNT_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(47, 76, 120))
NAME_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=17, color=(48, 52, 68))
DESC_STYLE = TextStyle(font=DEFAULT_FONT, size=12, color=(92, 100, 122))
QTY_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(38, 50, 76))
ICON_FALLBACK_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(112, 122, 148))


# ======================= 数据组装 ======================= #

@dataclass
class InventoryItem:
    id: int
    name: str
    description: str = ""
    category: str = "other"
    resource_type: str = ""
    icon_key: str = ""
    quantity: int = 0
    seq: int = 0
    recovery_value: int | None = None


@dataclass
class InventorySection:
    key: str
    title: str
    items: List[InventoryItem] = field(default_factory=list)


# 图标获取key：用于渲染时按key去重并发加载
def _icon_key(resource_type: str, id: int = None, asset_name: str = "") -> str:
    if asset_name:
        return f"{resource_type}:{asset_name}"
    return f"{resource_type}:{id}"


async def _inventory_icon_image(ctx: SekaiHandlerContext, icon_key: str) -> Image.Image:
    """
    根据icon_key加载背包道具图标，失败返回UNKNOWN_IMG
    """
    try:
        resource_type, _, rest = icon_key.partition(":")
        if resource_type == "coin":
            return await ctx.rip.img("thumbnail/common_material_rip/coin.png", use_img_cache=True)
        if resource_type == "virtual_coin":
            return await ctx.rip.img("thumbnail/common_material_rip/virtual_coin.png", use_img_cache=True)
        if resource_type == "jewel":
            return await ctx.rip.img("thumbnail/common_material_rip/jewel.png", use_img_cache=True)
        if resource_type == "material" and rest:
            return await ctx.rip.img(f"thumbnail/material_rip/material{rest}.png", use_img_cache=True)
        if resource_type == "boost_item" and rest:
            return await ctx.rip.img(f"thumbnail/boost_item_rip/boost_item{rest}.png", use_img_cache=True)
        if resource_type == "practice_ticket" and rest:
            return await ctx.rip.img(f"thumbnail/practice_ticket_rip/ticket{rest}.png", use_img_cache=True)
        if resource_type == "skill_practice_ticket" and rest:
            return await ctx.rip.img(f"thumbnail/skill_practice_ticket_rip/ticket{rest}.png", use_img_cache=True)
        if resource_type == "gacha_ticket" and rest:
            return await ctx.rip.img(f"thumbnail/gacha_ticket/{rest}.png", use_img_cache=True)
        if resource_type == "gacha_ceil_item" and rest:
            return await ctx.rip.img(f"thumbnail/gacha_item/{rest}.png", use_img_cache=True)
        if resource_type == "mysekai_material" and rest:
            return await ctx.rip.img(f"mysekai/thumbnail/material/{rest}.png", use_img_cache=True)
    except Exception as e:
        logger.warning(f"获取背包道具图标失败 {icon_key}: {get_exc_desc(e)}")
    return UNKNOWN_IMG


def inventory_category_for_material(material_type: str, name: str) -> str:
    typ = (material_type or "").strip().lower()
    lower_name = (name or "").strip().lower()

    if typ in ("coin", "jewel", "virtual_coin"):
        return "currency"
    if "boost" in typ:
        return "boost"
    if "costume" in typ:
        return "costume"
    if "music" in typ or "vocal" in typ or "song" in typ:
        return "music"
    if "ticket" in typ or "券" in lower_name or "ticket" in lower_name:
        return "tickets"
    if "event" in typ or "活动" in lower_name or "交换所" in lower_name:
        return "event"
    if ("special_training" in typ or "master_lesson" in typ or "skill" in typ
            or "character_rank" in typ or "练习" in lower_name or "技能" in lower_name
            or "想法" in lower_name):
        return "training"
    if typ == "" or "material" in typ or "piece" in typ or "gem" in typ:
        return "basic"
    return "other"


def is_mysekai_memory(meta: dict) -> bool:
    typ = (meta.get('mysekaiMaterialType') or "").lower()
    icon = (meta.get('iconAssetbundleName') or "").lower()
    name = meta.get('name') or ""
    return typ == "game_character" or \
        "memoria" in icon or "memory" in icon or \
        "メモリア" in name or "记忆" in name or "記憶" in name


def _clean_inventory_description(description: str) -> str:
    return " ".join(str(description or "").split())


def _fallback_seq(seq, id) -> int:
    return seq if seq and seq > 0 else id


# 从suite组装背包道具列表
async def build_inventory_items(ctx: SekaiHandlerContext, profile: Suite) -> List[InventoryItem]:
    items: List[InventoryItem] = []

    gamedata = profile.userGamedata or {}
    items.append(InventoryItem(
        id=0,
        name="金币",
        description="游戏内基础货币，可用于成员育成等消耗。",
        category="currency",
        resource_type="coin",
        icon_key=_icon_key("coin"),
        quantity=int(gamedata.get('coin') or 0),
        seq=0,
    ))

    charged = profile.get('userChargedCurrency', {})
    if not isinstance(charged, dict):
        charged = {}
    free_jewel = int(charged.get('free') or 0)
    paid_jewel = int(charged.get('paid') or 0)
    if free_jewel > 0:
        items.append(InventoryItem(
            id=-1,
            name="免费水晶",
            description="免费获得的水晶，可用于招募等用途。",
            category="currency",
            resource_type="jewel",
            icon_key=_icon_key("jewel"),
            quantity=free_jewel,
            seq=1,
        ))
    if paid_jewel > 0:
        items.append(InventoryItem(
            id=-2,
            name="付费水晶",
            description="购买获得的付费水晶，可用于招募等用途。",
            category="currency",
            resource_type="jewel",
            icon_key=_icon_key("jewel"),
            quantity=paid_jewel,
            seq=2,
        ))

    virtual_coin = int(gamedata.get('virtualCoin') or 0)
    if virtual_coin > 0:
        items.append(InventoryItem(
            id=-3,
            name="虚拟币",
            description="虚拟演唱会等玩法中使用的货币。",
            category="currency",
            resource_type="virtual_coin",
            icon_key=_icon_key("virtual_coin"),
            quantity=virtual_coin,
            seq=3,
        ))

    # 基础材料
    for mat in profile.get('userMaterials', []):
        if not isinstance(mat, dict):
            continue
        material_id = int(mat.get('materialId') or 0)
        quantity = int(mat.get('quantity') or 0)
        if material_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.materials.find_by_id(material_id) or {}
        name = (meta.get('name') or "").strip() or f"材料 {material_id}"
        items.append(InventoryItem(
            id=material_id,
            name=name,
            description=_clean_inventory_description(meta.get('flavorText')),
            category=inventory_category_for_material(meta.get('materialType'), name),
            resource_type="material",
            icon_key=_icon_key("material", material_id),
            quantity=quantity,
            seq=_fallback_seq(meta.get('seq'), material_id),
        ))

    # 招募券
    for ticket in profile.get('userGachaTickets', []):
        if not isinstance(ticket, dict):
            continue
        ticket_id = int(ticket.get('gachaTicketId') or 0)
        quantity = int(ticket.get('quantity') or 0)
        if ticket_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.gacha_tickets.find_by_id(ticket_id) or {}
        name = (meta.get('name') or "").strip() or f"招募券 {ticket_id}"
        items.append(InventoryItem(
            id=ticket_id,
            name=name,
            description=_clean_inventory_description(meta.get('flavorText')),
            category="tickets",
            resource_type="gacha_ticket",
            icon_key=_icon_key("gacha_ticket", asset_name=meta.get('assetbundleName') or ""),
            quantity=quantity,
            seq=_fallback_seq(meta.get('seq'), ticket_id),
        ))

    # 练习乐谱
    for ticket in profile.get('userPracticeTickets', []):
        if not isinstance(ticket, dict):
            continue
        ticket_id = int(ticket.get('practiceTicketId') or 0)
        quantity = int(ticket.get('quantity') or 0)
        if ticket_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.practice_tickets.find_by_id(ticket_id) or {}
        name = (meta.get('name') or "").strip() or f"练习乐谱 {ticket_id}"
        items.append(InventoryItem(
            id=ticket_id,
            name=name,
            description=_clean_inventory_description(meta.get('flavorText')),
            category="training",
            resource_type="practice_ticket",
            icon_key=_icon_key("practice_ticket", ticket_id),
            quantity=quantity,
            seq=_fallback_seq(int(meta.get('characterId') or 0) * 1000 + int(meta.get('exp') or 0), ticket_id),
        ))

    # 技能升级乐谱
    for ticket in profile.get('userSkillPracticeTickets', []):
        if not isinstance(ticket, dict):
            continue
        ticket_id = int(ticket.get('skillPracticeTicketId') or 0)
        quantity = int(ticket.get('quantity') or 0)
        if ticket_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.skill_practice_tickets.find_by_id(ticket_id) or {}
        name = (meta.get('name') or "").strip() or f"技能升级乐谱 {ticket_id}"
        items.append(InventoryItem(
            id=ticket_id,
            name=name,
            description=_clean_inventory_description(meta.get('flavorText')),
            category="training",
            resource_type="skill_practice_ticket",
            icon_key=_icon_key("skill_practice_ticket", ticket_id),
            quantity=quantity,
            seq=_fallback_seq(int(meta.get('characterId') or 0) * 1000 + int(meta.get('exp') or 0), ticket_id),
        ))

    # 招募贴纸
    for item in profile.get('userGachaCeilItems', []):
        if not isinstance(item, dict):
            continue
        item_id = int(item.get('gachaCeilItemId') or 0)
        quantity = int(item.get('quantity') or 0)
        if item_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.gacha_ceil_items.find_by_id(item_id) or {}
        name = (meta.get('name') or "").strip() or f"招募贴纸 {item_id}"
        items.append(InventoryItem(
            id=item_id,
            name=name,
            description=_clean_inventory_description(meta.get('flavorText')),
            category="tickets",
            resource_type="gacha_ceil_item",
            icon_key=_icon_key("gacha_ceil_item", asset_name=meta.get('assetbundleName') or ""),
            quantity=quantity,
            seq=_fallback_seq(meta.get('seq'), item_id),
        ))

    # MySekai材料
    for material in profile.get('userMysekaiMaterials', []):
        if not isinstance(material, dict):
            continue
        material_id = int(material.get('mysekaiMaterialId') or 0)
        quantity = int(material.get('quantity') or 0)
        if material_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.mysekai_materials.find_by_id(material_id) or {}
        name = (meta.get('name') or "").strip() or f"MySekai 材料 {material_id}"
        category = "memory" if is_mysekai_memory(meta) else "mysekai"
        items.append(InventoryItem(
            id=material_id,
            name=name,
            description=_clean_inventory_description(meta.get('description')),
            category=category,
            resource_type="mysekai_material",
            icon_key=_icon_key("mysekai_material", asset_name=meta.get('iconAssetbundleName') or ""),
            quantity=quantity,
            seq=_fallback_seq(meta.get('seq'), material_id),
        ))

    # 火罐（演出能量）
    for boost in profile.get('userBoostItems', []):
        if not isinstance(boost, dict):
            continue
        boost_id = int(boost.get('boostItemId') or 0)
        quantity = int(boost.get('quantity') or 0)
        if boost_id <= 0 or quantity <= 0:
            continue
        meta = await ctx.md.boost_items.find_by_id(boost_id) or {}
        name = (meta.get('name') or "").strip() or f"演出能量道具 {boost_id}"
        recovery_value = int(meta.get('recoveryValue') or 0)
        items.append(InventoryItem(
            id=boost_id,
            name=name,
            description=_clean_inventory_description(meta.get('flavorText')),
            category="boost",
            resource_type="boost_item",
            icon_key=_icon_key("boost_item", boost_id),
            quantity=quantity,
            seq=_fallback_seq(meta.get('seq'), boost_id),
            recovery_value=recovery_value if recovery_value > 0 else None,
        ))

    return items


def _inventory_item_matches_filter(item: InventoryItem, filter: str) -> bool:
    if filter == INVENTORY_FILTER_JEWEL:
        return item.resource_type == "jewel"
    if filter == INVENTORY_FILTER_BOOST:
        return item.resource_type == "boost_item"
    if filter == INVENTORY_FILTER_MYSEKAI:
        return item.category == "mysekai"
    if filter == INVENTORY_FILTER_MEMORY:
        return item.category == "memory"
    # 默认视图不展示水晶、火罐、MySekai材料、记忆
    return not (
        item.resource_type == "jewel"
        or item.resource_type == "boost_item"
        or item.category == "mysekai"
        or item.category == "memory"
    )


def _build_inventory_sections(items: List[InventoryItem]) -> List[InventorySection]:
    grouped: Dict[str, List[InventoryItem]] = {}
    for item in items:
        if item.quantity < 0:
            continue
        key = item.category or "other"
        grouped.setdefault(key, []).append(item)

    sections = []
    for key, title in INVENTORY_SECTION_ORDER:
        group = grouped.get(key)
        if not group:
            continue
        group.sort(key=lambda x: (x.seq, x.id, x.name))
        sections.append(InventorySection(key=key, title=title, items=group))
    return sections


# ======================= 渲染 ======================= #

def _quantity_style(text: str) -> TextStyle:
    for size in range(QTY_STYLE.size, 9, -1):
        if get_text_size(get_font(QTY_STYLE.font, size), text)[0] <= ITEM_TEXT_WIDTH:
            return TextStyle(font=QTY_STYLE.font, size=size, color=QTY_STYLE.color)
    return TextStyle(font=QTY_STYLE.font, size=10, color=QTY_STYLE.color)


def _name_style(text: str) -> TextStyle:
    for size in range(NAME_STYLE.size, 10, -1):
        if _fits_lines(text, NAME_STYLE.font, size, ITEM_TEXT_WIDTH, 2):
            return TextStyle(font=NAME_STYLE.font, size=size, color=NAME_STYLE.color)
    return TextStyle(font=NAME_STYLE.font, size=11, color=NAME_STYLE.color)


def _fits_lines(text: str, font_path: str, size: int, width: int, line_count: int) -> bool:
    font = get_font(font_path, size)
    used_lines = 0
    for raw_line in str(text).split("\n"):
        line = raw_line
        while line:
            if used_lines >= line_count:
                return False
            if get_text_size(font, line)[0] <= width:
                used_lines += 1
                break
            clip_idx = _clip_text_to_width(line, font, width)
            if clip_idx <= 0:
                return False
            used_lines += 1
            line = line[clip_idx:]
    return used_lines <= line_count


def _clip_text_to_width(text: str, font, width: int) -> int:
    left_idx, right_idx = 0, len(text)
    while left_idx <= right_idx:
        mid_idx = (left_idx + right_idx) // 2
        measured_width = get_text_size(font, text[:mid_idx])[0]
        if measured_width < width:
            left_idx = mid_idx + 1
        elif measured_width > width:
            right_idx = mid_idx - 1
        else:
            return mid_idx
    return max(1, right_idx)


def _item_description_text(item: InventoryItem) -> str:
    description = " ".join((item.description or "").split())
    if description:
        return description
    if item.recovery_value:
        return f"+{item.recovery_value} 能量"
    if item.resource_type == "coin":
        return "金币"
    if item.resource_type == "jewel":
        return "水晶"
    if item.resource_type == "virtual_coin":
        return "虚拟币"
    if item.resource_type == "boost_item":
        return "火罐"
    if item.resource_type == "event_item":
        return "活动"
    if item.resource_type in {"gacha_ticket", "gacha_ceil_item"}:
        return "招募"
    if item.resource_type in {"practice_ticket", "skill_practice_ticket"}:
        return "育成"
    if item.resource_type == "mysekai_material":
        return "MySekai"
    return f"ID {item.id}"


def _draw_item_tile(item: InventoryItem, icon: Image.Image | None) -> None:
    with (
        HSplit()
        .set_size((TILE_WIDTH, TILE_HEIGHT))
        .set_content_align('lt')
        .set_item_align('t')
        .set_sep(6)
        .set_padding((8, 7))
        .set_bg(roundrect_bg(fill=(255, 255, 255, 92), radius=8, blurglass_kwargs={'blur': 6}))
    ):
        with Frame().set_size((ICON_SIZE, ICON_SIZE)).set_content_align('c'):
            if icon is not None:
                ImageBox(icon, size=(ICON_SIZE - 8, ICON_SIZE - 8), image_size_mode='fit').set_content_align('c')
            else:
                TextBox("?", ICON_FALLBACK_STYLE).set_w(ICON_SIZE - 8).set_content_align('c')

        with VSplit().set_w(ITEM_TEXT_WIDTH).set_content_align('lt').set_item_align('lt').set_sep(3):
            TextBox(item.name, _name_style(item.name), line_count=2, overflow='clip').set_w(
                ITEM_TEXT_WIDTH
            ).set_padding(0)
            TextBox(_item_description_text(item), DESC_STYLE, line_count=2, overflow='clip').set_w(
                ITEM_TEXT_WIDTH
            ).set_padding(0)
            quantity = _format_quantity(item.quantity)
            TextBox(quantity, _quantity_style(quantity), overflow='clip').set_w(ITEM_TEXT_WIDTH).set_content_align(
                'r'
            ).set_padding(0)


def _format_quantity(value: int) -> str:
    return f"{int(value or 0):,}"


def _draw_section(section: InventorySection, icon_cache: Dict[str, Image.Image]) -> None:
    with (
        VSplit()
        .set_w(PANEL_WIDTH)
        .set_content_align('lt')
        .set_item_align('lt')
        .set_sep(10)
        .set_padding(12)
        .set_bg(roundrect_bg(alpha=72, blurglass_kwargs={'blur': 8}))
    ):
        with HSplit().set_w(PANEL_WIDTH - 24).set_content_align('lt').set_item_align('c').set_sep(8):
            TextBox(section.title, SECTION_STYLE).set_padding(0)
            TextBox(f"{len(section.items)}", COUNT_STYLE).set_padding((8, 1)).set_bg(
                roundrect_bg(fill=(255, 255, 255, 96), radius=8)
            )

        with Grid(col_count=TILE_COL_COUNT).set_sep(TILE_GAP, TILE_GAP).set_item_align('lt'):
            for item in section.items:
                _draw_item_tile(item, icon_cache.get(item.icon_key))


async def compose_inventory_image(ctx: SekaiHandlerContext, qid: int, filter: str = INVENTORY_FILTER_DEFAULT) -> Image.Image:
    # 获取suite数据
    profile, pmsg = await get_detailed_profile(
        ctx,
        qid,
        filter=get_detailed_profile_card_filter(
            'userMaterials',
            'userBoostItems',
            'userEventItems',
            'userGachaTickets',
            'userPracticeTickets',
            'userSkillPracticeTickets',
            'userGachaCeilItems',
            'userMysekaiMaterials',
            'userChargedCurrency',
        ),
        strict=False,
        raise_exc=True,
    )
    assert_and_reply(profile, "未获取到Suite抓包数据")

    # 组装道具
    all_items = await build_inventory_items(ctx, profile)
    filtered = [item for item in all_items if _inventory_item_matches_filter(item, filter)]
    sections = _build_inventory_sections(filtered)
    assert_and_reply(sections, "背包中没有可展示的道具")

    # 并发加载图标
    icon_keys = list(dict.fromkeys(item.icon_key for section in sections for item in section.items))
    icon_results = await batch_gather_with_progress(
        *[_inventory_icon_image(ctx, key) for key in icon_keys],
        progress_name="加载背包图标",
    )
    icon_cache = {key: img for key, img in zip(icon_keys, icon_results)}

    # 渲染
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, pmsg, mode=get_user_data_mode(ctx, ctx.user_id))

            with VSplit().set_w(PANEL_WIDTH).set_content_align('lt').set_item_align('lt').set_sep(14):
                TextBox("背包一览", TITLE_STYLE).set_padding((8, 0))
                for section in sections:
                    _draw_section(section, icon_cache)

    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令 ======================= #

def parse_inventory_filter(args: str) -> str:
    args = "".join(str(args or "").split()).lower()
    if not args:
        return INVENTORY_FILTER_DEFAULT
    for filter_key, keywords in INVENTORY_FILTER_KEYWORDS.items():
        for kw in keywords:
            if args == kw:
                return filter_key
    raise ReplyException(
        f"未知的背包筛选参数：{args}\n可用参数：水晶、火罐、ms材料、记忆\n使用方式：/查背包 [水晶|火罐|ms材料|记忆]"
    )


def validate_inventory_filter_for_region(region: str, filter: str):
    # 国服 masterdata 已包含完整 MySekai 材料与记忆数据（62种/26种记忆），
    # 与日服结构一致，不再限制国服查询
    return


pjsk_inventory = SekaiCmdHandler([
    "/道具", "/查背包", "/持有物", "/查持有物",
    "/背包一览", "/inventory", "/pjsk inventory",
])
pjsk_inventory.check_cdrate(cd).check_wblist(gbl)


@pjsk_inventory.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    if args in ("help", "-help", "--help", "帮助"):
        return await ctx.asend_reply_msg(INVENTORY_HELP)

    filter = parse_inventory_filter(args)
    validate_inventory_filter_for_region(ctx.region, filter)

    image = await compose_inventory_image(ctx, ctx.user_id, filter)
    return await ctx.asend_reply_msg(await get_image_cq(image, low_quality=True))
