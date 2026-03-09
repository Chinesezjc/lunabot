from ...utils import *
from ...llm import ChatSession, translate_text, get_model_preset, ChatSessionResponse
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..gameapi import get_gameapi_config, request_gameapi
from .profile import (
    get_card_full_thumbnail, 
    get_player_bind_id,
    get_detailed_profile,
    get_detailed_profile_card,
    get_detailed_profile_card_filter,
    get_player_avatar_info_by_detailed_profile,
)

QUERY_SINGLE_EVENT_HELP = """
【查单个活动格式】
1. 活动ID：123
2. 倒数第几次活动：-1 -2
3. ban主昵称+序号：mnr1
""".strip()

QUERY_MULTI_EVENT_HELP = """
【查多个活动格式】
1. 活动类型：5v5 普活 wl
2. 颜色和团：紫 25h
3. 年份：25年 去年
4. 活动角色：mnr hrk 可以加多个
5. 活动ban主：mnr箱
""".strip()


@dataclass
class EventDetail:
    # detail info
    event: dict
    name: str
    eid: int
    etype: str
    etype_name: str
    asset_name: str
    start_time: datetime
    end_time: datetime
    event_cards: List[dict]
    bonus_attr: str
    bonus_cuids: List[int]
    bonus_cids: List[int]
    banner_cid: int
    unit: str
    # assets
    event_banner: Image.Image
    event_logo: Image.Image
    event_bg: Image.Image
    event_story_bg: Image.Image
    event_ban_chara_img: Image.Image
    event_card_thumbs: List[Image.Image]


EVENT_TYPE_NAMES = [
    ("marathon", "普活"),
    ("cheerful_carnival", "5v5"),
    ("world_bloom", "worldlink", "wl", "world link"),
]

EVENT_TYPE_SHOW_NAMES = {
    "marathon": "",
    "cheerful_carnival": "5v5",
    "world_bloom": "WorldLink",
}

@dataclass
class EventListFilter:
    attr: str = None
    event_type: str = None
    unit: str = None
    cids: List[int] = None
    banner_cid: int = None
    year: int = None
    leak: bool = None


# ======================= 处理逻辑 ======================= #

# 获取图片左侧全透明部分宽度（用于裁剪活动详情角色特写）
def get_left_transparent_width(img: Image.Image) -> int:
    alpha = np.array(img.getchannel('A'))
    col_has_pixel = np.any(alpha > 0, axis=0)
    if not np.any(col_has_pixel):
        return img.shape[1]
    left_width = np.argmax(col_has_pixel)
    return left_width

# 判断某个卡牌id的限定类型
async def get_card_supply_type(ctx: SekaiHandlerContext, cid: int) -> str:
    ctx = SekaiHandlerContext.from_region("jp")
    card = await ctx.md.cards.find_by_id(cid)
    if not card or 'cardSupplyId' not in card:
        return "normal"
    if card_supply := await ctx.md.card_supplies.find_by_id(card["cardSupplyId"]):
        return card_supply["cardSupplyType"]
    return "normal"

# 获取某个活动详情
async def get_event_detail(ctx: SekaiHandlerContext, event_or_event_id: Union[int, Dict], require_assets: List[str]) -> EventDetail:
    if isinstance(event_or_event_id, int):
        event_id = event_or_event_id
        event = await ctx.md.events.find_by_id(event_id)
        assert_and_reply(event, f"未找到ID为{event_id}的活动")
    else:
        event = event_or_event_id
        event_id = event['id']
    etype = event['eventType']
    name = event['name']
    etype_name = EVENT_TYPE_SHOW_NAMES.get(etype, "") or "马拉松"
    asset_name = event['assetbundleName']
    start_time = datetime.fromtimestamp(event['startAt'] / 1000)
    end_time = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)

    event_cards = await ctx.md.event_cards.find_by('eventId', event_id, mode="all")
    event_card_ids = [ec['cardId'] for ec in event_cards]
    event_cards = await ctx.md.cards.collect_by_ids(event_card_ids)

    bonus_attr = None
    bonus_cuids = set()
    for deck_bonus in await ctx.md.event_deck_bonuses.find_by('eventId', event_id, mode="all"):
        if 'cardAttr' in deck_bonus:
            bonus_attr = deck_bonus['cardAttr']
        if 'gameCharacterUnitId' in deck_bonus:
            bonus_cuids.add(deck_bonus['gameCharacterUnitId'])
    bonus_cuids = sorted(list(bonus_cuids))
    bonus_cids = [await get_chara_id_by_cuid(ctx, cuid) for cuid in bonus_cuids]

    banner_cid = await get_event_banner_chara_id(ctx, event)
    unit = None
    if banner_cid:
        unit = get_unit_by_chara_id(banner_cid)
    elif event['eventType'] == 'world_bloom':
        if len(event_cards) <= 6:
            unit = get_unit_by_chara_id(event_cards[0]['characterId'])
    
    assert not require_assets or all(a in ['banner', 'logo', 'bg', 'story_bg', 'ban_chara', 'card_thumbs'] for a in require_assets)

    event_banner = None
    if 'banner' in require_assets:
        event_banner = await get_event_banner_img(ctx, event)

    event_logo = None
    if 'logo' in require_assets:
        event_logo = await ctx.rip.img(f"event/{asset_name}/logo/logo.png")

    event_bg = None
    if 'bg' in require_assets:
        event_bg = await ctx.rip.img(f"event/{asset_name}/screen/bg.png", default=None)

    event_story_bg = None
    if 'story_bg' in require_assets and etype != 'world_bloom':
        event_story_bg = await ctx.rip.img(f"event_story/{asset_name}/screen_image/story_bg.png", default=None)

    event_ban_chara_img = None
    if 'ban_chara' in require_assets and etype != 'world_bloom':
        event_ban_chara_img = await ctx.rip.img(f"event/{asset_name}/screen/character.png", default=None)

    event_card_thumbs = []
    if 'card_thumbs' in require_assets:
        for card in event_cards:
            thumb = await get_card_full_thumbnail(ctx, card, after_training=False)
            event_card_thumbs.append(thumb)

    return EventDetail(
        event=event,
        name=name,
        eid=event_id,
        etype=etype,
        etype_name=etype_name,
        asset_name=asset_name,
        start_time=start_time,
        end_time=end_time,
        event_cards=event_cards,
        bonus_attr=bonus_attr,
        bonus_cuids=bonus_cuids,
        bonus_cids=bonus_cids,
        banner_cid=banner_cid,
        unit=unit,
        event_banner=event_banner,
        event_logo=event_logo,
        event_bg=event_bg,
        event_story_bg=event_story_bg,
        event_ban_chara_img=event_ban_chara_img,
        event_card_thumbs=event_card_thumbs,
    )

# 获取wl_id对应的角色cid，wl_id对应普通活动则返回None
async def get_wl_chapter_cid(ctx: SekaiHandlerContext, wl_id: int) -> Optional[int]:
    event_id = wl_id % 1000
    chapter_id = wl_id // 1000
    if chapter_id == 0:
        return None
    chapters = await ctx.md.world_blooms.find_by('eventId', event_id, mode='all')
    assert_and_reply(chapters, f"活动{ctx.region.upper()}-{event_id}并不是WorldLink活动")
    chapter = find_by(chapters, "chapterNo", chapter_id)
    assert_and_reply(chapter, f"活动{ctx.region.upper()}-{event_id}并没有章节{chapter_id}")
    cid = chapter.get('gameCharacterId', None)
    return cid

# 获取event_id对应的所有wl_event（时间顺序），如果不是wl则返回空列表
async def get_wl_events(ctx: SekaiHandlerContext, event_id: int) -> List[dict]:
    event = await ctx.md.events.find_by_id(event_id)
    chapters = await ctx.md.world_blooms.find_by('eventId', event['id'], mode='all')
    if not chapters:
        return []
    wl_events = []
    for chapter in chapters:
        wl_event = event.copy()
        wl_event['id'] = chapter['chapterNo'] * 1000 + event['id']
        wl_event['startAt'] = chapter['chapterStartAt']
        wl_event['aggregateAt'] = chapter['aggregateAt']
        wl_event['wl_cid'] = chapter.get('gameCharacterId', None)
        wl_events.append(wl_event)
    return sorted(wl_events, key=lambda x: x['startAt'])

# 从cuid获取cid
async def get_chara_id_by_cuid(ctx: SekaiHandlerContext, cuid: int) -> int:
    unit_chara = await ctx.md.game_character_units.find_by_id(cuid)
    assert_and_reply(unit_chara, f"找不到cuid={cuid}的角色")
    return unit_chara['gameCharacterId']

# 获取当前活动 当前无进行中活动时 fallback = None:返回None prev:选择上一个 next:选择下一个 prev_first:优先选择上一个 next_first: 优先选择下一个
async def get_current_event(ctx: SekaiHandlerContext, fallback: Optional[str] = None) -> dict:
    assert fallback is None or fallback in ("prev", "next", "prev_first", "next_first")
    events = sorted(await ctx.md.events.get(), key=lambda x: x['aggregateAt'], reverse=False)
    now = datetime.now()
    prev_event, cur_event, next_event = None, None, None
    for event in events:
        start_time = datetime.fromtimestamp(event['startAt'] / 1000)
        end_time = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
        if start_time <= now <= end_time:
            cur_event = event
        if end_time < now:
            prev_event = event
        if not next_event and start_time > now:
            next_event = event
    if fallback is None or cur_event:
        return cur_event
    if fallback == "prev" or (fallback == "prev_first" and prev_event):
        return prev_event
    if fallback == "next" or (fallback == "next_first" and next_event):
        return next_event
    return prev_event or next_event

# 获取活动banner图
async def get_event_banner_img(ctx: SekaiHandlerContext, event: dict) -> Image.Image:
    asset_name = event['assetbundleName']
    return await ctx.rip.img(f"home/banner/{asset_name}_rip/{asset_name}.png", use_img_cache=True)

# 从文本中提取箱活，返回 (活动，剩余文本）
async def extract_ban_event(ctx: SekaiHandlerContext, text: str) -> Tuple[Dict, str]:
    all_ban_event_texts = []
    for nickname, cid in get_character_nickname_data().nickname_ids:
        for i in range(1, 10):
            all_ban_event_texts.append(f"{nickname}{i}")
    for ban_event_text in all_ban_event_texts:
        if ban_event_text in text:
            nickname = ban_event_text[:-1]
            seq = int(ban_event_text[-1])
            ban_events = await get_chara_ban_events(ctx, get_cid_by_nickname(nickname))
            assert_and_reply(seq <= len(ban_events), f"角色{nickname}只有{len(ban_events)}次箱活")
            event = ban_events[seq - 1]
            text = text.replace(ban_event_text, "").strip()
            return event, text
    return None, text

# 从文本中提取活动类型，返回 (活动类型，剩余文本）
def extract_event_type(text: str, default: str = None) -> Tuple[str, str]:
    text = text.lower()
    for event_type in EVENT_TYPE_NAMES:
        for name in event_type:
            if name in text:
                text = text.replace(name, "").strip()
                return event_type[0], text
    return default, text

# 获取所有箱活id集合（往期通过书下曲判断，当期书下可能还没上线通过活动加成判断）
async def get_ban_events_id_set(ctx: SekaiHandlerContext) -> set[int]:
    # 获取已有书下的活动
    ret = set((await ctx.md.event_musics.get_indexed('eventId')).keys())
    # 查找书下未上线的活动进行判断
    for event in reversed(await ctx.md.events.get_sorted('startAt')):
        event_id = event['id']
        if datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1) < datetime.now():
            break    # 已结束的活动书下肯定已经上线，不重复判断
        if await ctx.md.event_musics.find_by('eventId', event_id):
            continue    # 已有书下，跳过
        if event['eventType'] not in ('marathon', 'cheerful_carnival'):
            continue    # 跳过非普通活动
        bonus_unit = set()
        for deck_bonus in await ctx.md.event_deck_bonuses.find_by('eventId', event_id, mode="all"):
            cuid = deck_bonus.get('gameCharacterUnitId')
            if cuid and cuid <= 20:
                bonus_unit.add((await ctx.md.game_character_units.find_by_id(cuid))['unit'])
        if len(bonus_unit) == 1:    # 所有角色加成都是同一个团
            ret.add(event_id)
    # 特判sdl3
    ret.add(74)
    return ret

# 判断是否是箱活
async def is_ban_event(ctx: SekaiHandlerContext, event: dict) -> bool:
    if event['eventType'] not in ('marathon', 'cheerful_carnival'):
        return False
    return event['id'] in await get_ban_events_id_set(ctx)

# 获取箱活ban主角色id 不是箱活返回None
async def get_event_banner_chara_id(ctx: SekaiHandlerContext, event: dict) -> int | None:
    if not await is_ban_event(ctx, event):
        return None
    event_cards = await ctx.md.event_cards.find_by('eventId', event['id'], mode="all")
    banner_card_id = min([
        ec['cardId'] for ec in event_cards
        if "festival_limited" not in await get_card_supply_type(ctx, ec['cardId'])
    ])
    banner_card = await ctx.md.cards.find_by_id(banner_card_id)
    return banner_card['characterId']

# 获取某个角色所有箱活（按时间顺序排列）
async def get_chara_ban_events(ctx: SekaiHandlerContext, cid: int) -> List[dict]:
    ban_events = await ctx.md.events.collect_by_ids(await get_ban_events_id_set(ctx))
    ban_events = [e for e in ban_events if await get_event_banner_chara_id(ctx, e) == cid]
    assert_and_reply(ban_events, f"角色{get_character_first_nickname(cid)}没有箱活")  
    ban_events.sort(key=lambda x: x['startAt'])
    for i, e in enumerate(ban_events, 1):
        e['ban_index'] = i
    return ban_events

# 合成活动列表图片
async def compose_event_list_image(ctx: SekaiHandlerContext, filter: EventListFilter) -> Image.Image:
    events = await ctx.md.events.get_sorted('startAt')
    details: List[EventDetail] = await batch_gather(*[get_event_detail(ctx, e, ['banner', 'card_thumbs']) for e in events])

    filtered_details: List[EventDetail] = []
    for d in details:
        if filter:
            if filter.leak is not None:
                if filter.leak and d.start_time <= datetime.now(): continue
                if not filter.leak and d.start_time > datetime.now(): continue
            if filter.attr and filter.attr != d.bonus_attr: continue
            if filter.cids and any(cid not in d.bonus_cids for cid in filter.cids): continue
            if filter.banner_cid and filter.banner_cid != d.banner_cid: continue
            if filter.year and filter.year != d.start_time.year: continue
            if filter.event_type and filter.event_type != d.etype: continue
            if filter.unit:
                if filter.unit == 'blend':
                    if d.unit: continue
                else:
                    if filter.unit != d.unit: continue
        filtered_details.append(d)

    assert_and_reply(filtered_details, "没有符合筛选条件的活动")

    row_count = math.ceil(math.sqrt(len(filtered_details)))

    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=10, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=10, color=(70, 70, 70))
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(4).set_content_align('lt').set_item_align('lt'):
            TextBox(
                f"活动按时间顺序排列，黄色为当期活动，灰色为过去活动",
                TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 100))
            ).set_bg(roundrect_bg(radius=4)).set_padding(4)
            with Grid(row_count=row_count, vertical=True).set_sep(8, 2).set_item_align('lt').set_content_align('lt'):
                for d in filtered_details:
                    now = datetime.now()
                    bg_color = WIDGET_BG_COLOR_CFG.get()
                    if d.start_time <= now <= d.end_time:
                        bg_color = (255, 250, 220, 200)
                    elif now > d.end_time:
                        bg_color = (220, 220, 220, 200)
                    bg = roundrect_bg(bg_color, 5)

                    with HSplit().set_padding(4).set_sep(4).set_item_align('lt').set_content_align('lt').set_bg(bg):
                        with VSplit().set_padding(0).set_sep(2).set_item_align('lt').set_content_align('lt'):
                            ImageBox(d.event_banner, size=(None, 40))
                            with Grid(col_count=3).set_padding(0).set_sep(1, 1):
                                for thumb in d.event_card_thumbs[:6]:
                                    ImageBox(thumb, size=(30, 30))
                        with VSplit().set_padding(0).set_sep(2).set_item_align('lt').set_content_align('lt'):
                            TextBox(f"{d.name}", style1, line_count=2, use_real_line_count=False).set_w(100)
                            TextBox(f"ID: {d.eid} {d.etype_name}", style2)
                            TextBox(f"S {d.start_time.strftime('%Y-%m-%d %H:%M')}", style2)
                            TextBox(f"T {d.end_time.strftime('%Y-%m-%d %H:%M')}", style2)
                            with HSplit().set_padding(0).set_sep(4):
                                if d.bonus_attr: ImageBox(get_attr_icon(d.bonus_attr), size=(None, 24))
                                if d.unit:  ImageBox(get_unit_icon(d.unit), size=(None, 24))
                                if d.banner_cid: ImageBox(get_chara_icon_by_chara_id(d.banner_cid), size=(None, 24))
                                if not any([d.unit, d.banner_cid, d.bonus_attr]): Spacer(24, 24)

    add_watermark(canvas)

    # 缓存无筛选的活动列表图片
    cache_key = None
    if not any([
        filter.attr, 
        filter.event_type, 
        filter.unit, 
        filter.cids, 
        filter.banner_cid, 
        filter.year
    ]):
        cache_key = f"{ctx.region}_events"

    return await canvas.get_img(cache_key=cache_key)

# 根据"昵称箱数"（比如saki1）获取活动，不存在返回None
async def get_event_by_ban_name(ctx: SekaiHandlerContext, ban_name: str) -> Optional[dict]:
    idx = None
    for nickname, cid in get_character_nickname_data().nickname_ids:
        if nickname in ban_name:
            try:
                idx = int(ban_name.replace(nickname, "", 1))
                break
            except: 
                pass
    if not idx: return None
    assert_and_reply(idx >= 1, "箱数必须大于等于1")
    ban_event_id_set = await get_ban_events_id_set(ctx)
    events = []
    for eid in ban_event_id_set:
        event = await ctx.md.events.find_by_id(eid)
        banner_cid = await get_event_banner_chara_id(ctx, event)
        if banner_cid == cid:
            events.append(event)
    assert_and_reply(events, f"角色{nickname}没有箱活")
    assert_and_reply(idx <= len(events), f"角色{nickname}只有{len(events)}个箱活")
    events.sort(key=lambda x: x['startAt'])
    return events[idx-1]
                                
# 解析查单个活动参数，返回活动或抛出异常
async def parse_search_single_event_args(ctx: SekaiHandlerContext, args: str, fallback: str="next_first") -> dict:
    if args.startswith(('-', '+')) and args[1:].isdigit():
        events = await ctx.md.events.get_sorted('startAt')
        cur_event = await get_current_event(ctx, fallback=fallback)
        cur_idx = len(events) - 1
        for i, event in enumerate(reversed(events)):
            if event['id'] == cur_event['id']:
                cur_idx = len(events) - 1 - i
                break
        offset = int(args.removeprefix('+'))
        if offset < 0:
            index = cur_idx + offset + 1
            assert_and_reply(index >= 0, "倒数索引超出范围")
            return events[index]
        elif offset > 0:
            index = cur_idx + offset
            assert_and_reply(index < len(events), "正数索引超出范围")
            return events[index]
        else:
            return cur_event
    elif args.isdigit():
        event_id = int(args)
        event = await ctx.md.events.find_by_id(event_id)
        assert_and_reply(event, f"活动{ctx.region.upper()}-{event_id}不存在")
        return event
    elif event := await get_event_by_ban_name(ctx, args):
        return event
    else:
        raise ReplyException(f"查单个活动参数错误")

# 合成活动剧情总结文本版
async def compose_event_story_summary_msg_list(
    ctx: SekaiHandlerContext, 
    event: dict, 
    eps: list[dict], 
    no_snippet_eps: list[dict], 
    summary: dict,
    chara_talk_count: list[tuple[str, int]],
) -> List[str]:
    eid = event['id']
    title = event['name']
    banner_img_cq = await get_image_cq(await get_event_banner_img(ctx, event))

    msg_lists = []

    msg_lists.append(f"""
【{eid}】{title} - {summary.get('title', '')} 
{banner_img_cq}
!! 剧透警告 !!
!! 内容由AI生成，不保证完全准确 !!
!! 请勿转载到其他地方 !!
""".strip() + "\n" * 16)

    text = summary.get('outline', '').strip()
    msg_lists.append(f"【剧情概要】\n{text}")
    
    for i, ep in enumerate(eps + no_snippet_eps, 1):
        with Canvas(bg=SEKAI_BLUE_BG).set_padding(8) as canvas:
            with VSplit().set_sep(8):
                ImageBox(ep['image'], size=(None, 80))
                with Grid(col_count=5).set_sep(2, 2):
                    for cid in ep['cids']:
                        if not cid: continue
                        icon = get_chara_icon_by_chara_id(cid, raise_exc=False)
                        if not icon: continue
                        ImageBox(icon, size=(32, 32), use_alphablend=True)

        if i <= len(eps):
            text = summary.get(f'ep_{i}_summary', '')
            text = add_watermark_to_text(text, STORYSUMMARY_WATERMARK)
        else:
            text = "(章节剧情未实装)"

        msg_lists.append(f"""
【第{i}章】{summary.get(f'ep_{i}_title', ep['title'])}
{await get_image_cq(await canvas.get_img())}
{text}
""".strip())
    
    text = summary.get('summary', '').strip()
    msg_lists.append(f"【剧情总结】\n{text}")

    chara_talk_count_text = "【角色对话次数】\n"
    for name, count in chara_talk_count:
        chara_talk_count_text += f"{name}: {count}\n"
    msg_lists.append(chara_talk_count_text.strip())

    additional_info = "以上内容由LunaBot生成\n请勿转载\n"
    for phase in ['start', *[f'ep{i}' for i in range(1, len(eps) + 1)], 'end']:
        phase_info = summary.get(f'{phase}_additional_info', '')
        if phase_info:
            additional_info += f"{phase}: {phase_info}\n"
    additional_info += "使用\"/活动剧情 活动id\"查询对应活动总结\n"
    additional_info += "使用\"/活动剧情 活动id refresh\"可刷新AI活动总结"
    msg_lists.append(additional_info.strip())
        
    return msg_lists

# 合成活动剧情总结图片版
async def compose_event_story_summary_image(
    ctx: SekaiHandlerContext, 
    event: dict, 
    eps: list[dict], 
    no_snippet_eps: list[dict], 
    summary: dict,
    chara_talk_count: list[tuple[str, int]],
) -> Image.Image:
    eid = event['id']
    title = event['name']
    banner_img = await get_event_banner_img(ctx, event)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=25, color=(0, 0, 0))
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0))

    w = 720
    line_sep = 5

    with ProfileTimer("event_story.layout"):
        with Canvas(bg=SEKAI_BLUE_BG_DAY).set_padding(BG_PADDING) as canvas:
            with VSplit().set_sep(8).set_item_align('lt').set_content_align('lt').set_item_bg(roundrect_bg()):
                with HSplit().set_padding(16).set_sep(16).set_item_align('l').set_content_align('l'):
                    ImageBox(banner_img, size=(None, 100))
                    with VSplit().set_padding(0).set_sep(4).set_item_align('l').set_content_align('l'):
                        TextBox(f"#{eid} {title}", style1)
                        TextBox(f"{summary.get('title', '')} - 剧情总结", style1)
                        TextBox("剧透内容由AI生成，禁止盗转到【小红书】等社交平台", style2)
                        TextBox("盗转蛆你🐴坟头上刻着的是你全家惨死的剧透", style2.replace(color=(200, 0, 0)))
                        
                with VSplit().set_padding(16).set_sep(8).set_item_align('lt').set_content_align('lt'):
                    TextBox(f"剧情概要", style1)
                    text = summary.get('outline', '').strip()
                    TextBox(text, style2, use_real_line_count=True, line_sep=line_sep).set_w(w)

                for i, ep in enumerate(eps + no_snippet_eps, 1):
                    with VSplit().set_padding(16).set_sep(16).set_item_align('lt').set_content_align('lt'):
                        TextBox(f"第{i}章 {summary.get(f'ep_{i}_title', ep['title'])}", style1)
                        with HSplit().set_padding(0).set_sep(16).set_item_align('lt').set_content_align('lt'):
                            with VSplit().set_sep(8):
                                ImageBox(ep['image'], size=(160, None))
                                with Grid(col_count=5).set_sep(2, 2):
                                    for cid in ep['cids']:
                                        if not cid: continue
                                        icon = get_chara_icon_by_chara_id(cid, raise_exc=False)
                                        if not icon: continue
                                        ImageBox(icon, size=(32, 32), use_alphablend=True)
                            if i <= len(eps):
                                text = summary.get(f'ep_{i}_summary', '')
                                text = add_watermark_to_text(text, STORYSUMMARY_WATERMARK)
                            else:
                                text = "(章节剧情未实装)"
                            TextBox(text, style2, use_real_line_count=True, line_sep=line_sep).set_w(w - 176)

                with VSplit().set_padding(16).set_sep(8).set_item_align('lt').set_content_align('lt'):
                    TextBox(f"剧情总结", style1)
                    text = summary.get('summary', '').strip()
                    TextBox(add_watermark_to_text(text, STORYSUMMARY_WATERMARK), style2, use_real_line_count=True, line_sep=line_sep).set_w(w)

                with VSplit().set_padding(16).set_sep(8).set_item_align('lt').set_content_align('lt'):
                    TextBox(f"角色对话次数", style1)
                    chara_talk_count_text = ""
                    for name, count in chara_talk_count:
                        chara_talk_count_text += f"{name}: {count} | "
                    chara_talk_count_text = chara_talk_count_text.strip().rstrip('|')
                    TextBox(chara_talk_count_text, style2, use_real_line_count=True, line_sep=line_sep).set_w(w)
                    
        add_watermark(canvas)
    
    with ProfileTimer("event_story.get_img"): 
        return await canvas.get_img(cache_key=f"event_story_{ctx.region}_{eid}")

# 获取活动剧情总结，返回待发送的消息列表或图片
async def get_event_story_summary(ctx: SekaiHandlerContext, event: dict, refresh: bool, summary_model: List[str], save: bool) -> list[str] | Image.Image:
    eid = event['id']
    title = event['name']
    summary_db = get_file_db(f"{SEKAI_DATA_DIR}/story_summary/event/{ctx.region}/{eid}.json", logger)
    summary = summary_db.get_copy("summary", {})
    banner_img_cq = await get_image_cq(await get_event_banner_img(ctx, event))

    ## 读取数据
    story = await ctx.md.event_stories.find_by('eventId', eid)
    assert_and_reply(story, f"找不到活动{eid}的剧情数据")
    outline = story['outline']
    asset_name = story['assetbundleName']
    eps = []
    no_snippet_eps = []
    chara_talk_count: Dict[str, int] = {}

    with ProfileTimer("event_story.load_eps"):
        for i, ep in enumerate(story['eventStoryEpisodes'], 1):
            ep_id = ep['scenarioId']
            ep_title = ep['title']
            ep_image = await ctx.rip.img(f"event_story/{asset_name}/episode_image_rip/{asset_name}_{i:02d}.png")
            ep_data = await ctx.rip.json(
                f"event_story/{asset_name}/scenario_rip/{ep_id}.asset", 
                allow_error=False, 
                use_cache=True,
                cache_expire_secs=0 if refresh else 60 * 60 * 24,    # refresh时读取最新的，否则一天更新一次
            )
            cids = set([
                (await ctx.md.characters_2ds.find_by_id(item['Character2dId'])).get('characterId', None)
                for item in ep_data['AppearCharacters']
            ])

            snippets = []
            for snippet in ep_data['Snippets']:
                action = snippet['Action']
                ref_idx = snippet['ReferenceIndex']
                if action == 1:     # 对话
                    talk = ep_data['TalkData'][ref_idx]
                    names = talk['WindowDisplayName'].split('・')
                    snippets.append((names, talk['Body']))
                    for name in names:
                        chara_talk_count[name] = chara_talk_count.get(name, 0) + 1
                elif action == 6:   # 标题特效
                    effect = ep_data['SpecialEffectData'][ref_idx]
                    if effect['EffectType'] == 8:
                        snippets.append((None, effect['StringVal']))

            if snippets:
                eps.append({
                    'title': ep_title,
                    'image': ep_image,
                    'cids': cids,
                    'snippets': snippets,
                })
            else:
                no_snippet_eps.append({
                    'title': ep_title,
                    'image': ep_image,
                    'cids': cids,
                })

    chara_talk_count = sorted(chara_talk_count.items(), key=lambda x: x[1], reverse=True)

    last_chapter_num = summary.get("chapter_num", 0)
    story_has_update = len(eps) > last_chapter_num

    ## 获取总结
    if not summary or refresh or story_has_update:
        await ctx.asend_reply_msg(f"{banner_img_cq}正在生成活动{eid}剧情总结...")

        # 获取剧情文本
        raw_stories = []
        for i, ep in enumerate(eps, 1):
            ep_raw_story = f"【EP{i}: {ep['title']}】\n"
            for names, text in ep['snippets']:
                if names:
                    ep_raw_story += f"---\n{' & '.join(names)}:\n{text}\n"
                else:
                    ep_raw_story += f"---\n({text})\n"
            ep_raw_story += "\n"
            raw_stories.append(ep_raw_story)

        prompt_head = Path(f"{SEKAI_CONFIG_DIR}/story_summary/event_story_summary_prompt_head.txt").read_text()
        prompt_start_template = Path(f"{SEKAI_CONFIG_DIR}/story_summary/event_story_summary_prompt_start.txt").read_text()
        prompt_ep_template = Path(f"{SEKAI_CONFIG_DIR}/story_summary/event_story_summary_prompt_ep.txt").read_text()
        prompt_end_template = Path(f"{SEKAI_CONFIG_DIR}/story_summary/event_story_summary_prompt_end.txt").read_text()

        timeout = config.get('story_summary.event.timeout')
        retry_num = config.get('story_summary.event.retry')
        output_len_limit = config.get('story_summary.event.output_len_limit')
        limit = config.get('story_summary.event.target_len_short') if len(eps) >= 10 else config.get('story_summary.event.target_len_long')
        output_progress = config.get('story_summary.event.output_progress')

        @retry(stop=stop_after_attempt(retry_num), wait=wait_fixed(1), reraise=True)
        async def do_summary():
            try:
                summary = {}

                def get_process_func(phase: str):
                    def process(resp: ChatSessionResponse):
                        resp_text = resp.result
                        if len(resp_text) > output_len_limit:
                            raise Exception(f"生成文本超过长度限制({len(resp_text)}>{output_len_limit})")
                    
                        start_idx = resp_text.find("{")
                        end_idx = resp_text.rfind("}") + 1
                        data = loads_json(resp_text[start_idx:end_idx])

                        ep_idx = None
                        if phase == 'start':
                            ep_idx = 1
                        elif phase.startswith('ep'):
                            ep_idx = int(phase[2:])

                        if phase == 'start':
                            summary['title'] = data['title']
                            summary['outline'] = data['outline']
                            summary['previous'] = f"标题: {data['title']}\n"
                            summary['previous'] += f"简介: {data['outline']}\n\n"
                        if ep_idx is not None:
                            summary[f'ep_{ep_idx}_title'] = data[f'ep_{ep_idx}_title']
                            summary[f'ep_{ep_idx}_summary'] = data[f'ep_{ep_idx}_summary']
                            summary['previous'] += f"第{ep_idx}章标题: {data[f'ep_{ep_idx}_title']}\n"
                            summary['previous'] += f"第{ep_idx}章剧情: {data[f'ep_{ep_idx}_summary']}\n\n"
                        if phase == 'end':
                            summary['summary'] = data['summary']

                        additional_info = f"{resp.model.get_full_name()} | {resp.prompt_tokens}+{resp.completion_tokens} tokens"
                        if resp.quota > 0:
                            price_unit = resp.model.get_price_unit()
                            if resp.cost == 0.0:
                                additional_info += f" | 0/{resp.quota:.2f}{price_unit}"
                            elif resp.cost >= 0.0001:
                                additional_info += f" | {resp.cost:.4f}/{resp.quota:.2f}{price_unit}"
                            else:
                                additional_info += f" | <0.0001/{resp.quota:.2f}{price_unit}"
                        summary[f'{phase}_additional_info'] = additional_info
                    return process
                
                progress = "第1章"
                prompt_start = prompt_head + prompt_start_template.format(title=title, outline=outline, raw_story=raw_stories[0], limit=limit)
                session = ChatSession()
                session.append_user_content(prompt_start, verbose=False)
                await session.get_response(summary_model, process_func=get_process_func('start'), timeout=timeout)

                for i in range(2, len(eps) + 1):
                    progress = f"第{i}章"
                    prompt_ep = prompt_head + prompt_ep_template.format(ep=i, raw_story=raw_stories[i-1], limit=limit, prev_summary=summary['previous'])
                    session = ChatSession()
                    session.append_user_content(prompt_ep, verbose=False)
                    await session.get_response(summary_model, process_func=get_process_func(f'ep{i}'), timeout=timeout)
                    if i % 3 == 0:
                        await asyncio.sleep(3) 
                    if i == len(eps) // 2 and output_progress:
                        await ctx.asend_reply_msg(f"已生成50%...")

                progress = f"最终"
                prompt_end = prompt_head + prompt_end_template.format(limit=limit, prev_summary=summary['previous'])
                session = ChatSession()
                session.append_user_content(prompt_end, verbose=False)
                await session.get_response(summary_model, process_func=get_process_func('end'), timeout=timeout)

                summary['chapter_num'] = len(eps)
                del summary['previous']
                return summary
            
            except Exception as e:
                logger.warning(f"生成{progress}剧情总结失败: {e}")
                # await ctx.asend_reply_msg(f"生成剧情总结失败, 重新生成中...")
                raise ReplyException(f"生成{progress}剧情总结失败: {e}")

        summary = await do_summary()
        if save:
            summary_db.set("summary", summary)
    
    ## 生成回复
    with ProfileTimer("event_story.compose_summary"):
        compose_method = compose_event_story_summary_image if config.get('story_summary.output_as_image') \
                        else compose_event_story_summary_msg_list
        return await compose_method(
            ctx, event, 
            eps, no_snippet_eps, 
            summary, chara_talk_count
        )

# 5v5自动送火
async def send_boost(ctx: SekaiHandlerContext, qid: int) -> str:
    uid = get_player_bind_id(ctx)
    event = await get_current_event(ctx)
    assert_and_reply(event and event['eventType'] == 'cheerful_carnival', "当前没有进行中的5v5活动")
    url = get_gameapi_config(ctx).send_boost_api_url
    assert_and_reply(url, "该区服不支持自动送火")
    result = await request_gameapi(url.format(uid=uid), method='POST')
    ok_times = result['ok_times']
    failed_reason = result.get('failed_reason', '未知错误')
    ret_msg = f"成功送火{ok_times}次"
    if ok_times < 3:
        if 'opponent_user_receivable_count_max' in failed_reason:
            ret_msg += f"（达到送火上限）"
        else:
            ret_msg += f"，失败{3-ok_times}次，错误信息: \n{failed_reason}"
    return ret_msg

# 合成活动详情图片
async def compose_event_detail_image(ctx: SekaiHandlerContext, event: dict) -> Image.Image:
    detail = await get_event_detail(ctx, event, ['logo', 'bg', 'story_bg', 'ban_chara', 'card_thumbs'])
    now = datetime.now()

    if detail.banner_cid:
        banner_index = find_by(await get_chara_ban_events(ctx, detail.banner_cid), "id", detail.eid)['ban_index']

    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))

    wl_chapters = await get_wl_events(ctx, detail.eid)
    for chapter in wl_chapters:
        chapter['start_time'] = datetime.fromtimestamp(chapter['startAt'] / 1000)
        chapter['end_time'] = datetime.fromtimestamp(chapter['aggregateAt'] / 1000 + 1)

    use_story_bg = detail.event_story_bg and detail.event_ban_chara_img and detail.etype != "world_bloom"
    event_bg = detail.event_story_bg if use_story_bg else detail.event_bg
    h = 1024
    w = min(int(h * 1.6), event_bg.size[0] * h // event_bg.size[1] if event_bg else int(h * 1.6))
    bg = ImageBg(event_bg, blur=False) if event_bg else SEKAI_BLUE_BG
    
    async def draw(w, h):
        with Canvas(bg=bg, w=w, h=h).set_padding(BG_PADDING).set_content_align('r') as canvas:
            with Frame().set_size((w-BG_PADDING*2, h-BG_PADDING*2)).set_content_align('lb').set_padding((64, 0)):
                if use_story_bg:
                    chara_img = detail.event_ban_chara_img
                    chara_img = chara_img.crop((get_left_transparent_width(chara_img), 0, chara_img.width, chara_img.height))
                    ImageBox(chara_img, size=(None, int(h * 0.9)), use_alphablend=True).set_offset((0, BG_PADDING))

            with VSplit().set_padding(16).set_sep(16).set_item_align('t').set_content_align('t').set_item_bg(roundrect_bg()):
                # logo
                ImageBox(detail.event_logo, size=(None, 150)).set_omit_parent_bg(True)

                # 活动ID和类型和箱活
                with VSplit().set_padding(16).set_sep(12).set_item_align('l').set_content_align('l'):
                    with HSplit().set_padding(0).set_sep(8).set_item_align('l').set_content_align('l'):
                        TextBox(ctx.region.upper(), label_style)
                        TextBox(f"{detail.eid}", text_style)
                        Spacer(w=8)
                        TextBox(f"类型", label_style)
                        TextBox(f"{detail.etype_name}", text_style)
                        if detail.banner_cid:
                            Spacer(w=8)
                            ImageBox(get_chara_icon_by_chara_id(detail.banner_cid), size=(30, 30))
                            TextBox(f"{banner_index}箱", label_style)

                # 活动时间
                with VSplit().set_padding(16).set_sep(12).set_item_align('c').set_content_align('c'):
                    with HSplit().set_padding(0).set_sep(8).set_item_align('lb').set_content_align('lb'):
                        TextBox("开始时间", label_style)
                        TextBox(detail.start_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_item_align('lb').set_content_align('lb'):
                        TextBox("结束时间", label_style)
                        TextBox(detail.end_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    with HSplit().set_padding(0).set_sep(8).set_item_align('lb').set_content_align('lb'):
                        if detail.start_time <= now <= detail.end_time:
                            TextBox(f"距结束还有{get_readable_timedelta(detail.end_time - now)}", text_style)
                        elif now > detail.end_time:
                            TextBox(f"活动已结束", text_style)
                        else:
                            TextBox(f"距开始还有{get_readable_timedelta(detail.start_time - now)}", text_style)

                    if detail.etype == 'world_bloom':
                        cur_chapter = None
                        for chapter in wl_chapters:
                            if chapter['start_time'] <= now <= chapter['end_time']:
                                cur_chapter = chapter
                                break
                        if cur_chapter:
                            TextBox(f"距章节结束还有{get_readable_timedelta(cur_chapter['end_time'] - now)}", text_style)
                        
                    # 进度条
                    progress = (datetime.now() - detail.start_time) / (detail.end_time - detail.start_time)
                    progress = min(max(progress, 0), 1)
                    progress_w, progress_h, border = 320, 8, 1
                    if detail.etype == 'world_bloom' and len(wl_chapters) > 1:
                        with Frame().set_padding(8).set_content_align('lt'):
                            Spacer(w=progress_w+border*2, h=progress_h+border*2).set_bg(RoundRectBg((75, 75, 75, 255), 4))
                            for i, chapter in enumerate(wl_chapters):
                                cprogress_start = (chapter['start_time'] - detail.start_time) / (detail.end_time - detail.start_time)
                                cprogress_end = (chapter['end_time'] - detail.start_time) / (detail.end_time - detail.start_time)
                                chapter_cid = chapter['wl_cid']
                                chara_color = color_code_to_rgb((await ctx.md.game_character_units.find_by_id(chapter_cid))['colorCode'])
                                corners = (False, False, False, False)
                                if i == 0:
                                    corners = (True, False, False, True)
                                if i == len(wl_chapters) - 1:
                                    corners = (False, True, True, False)
                                Spacer(w=int(progress_w * (cprogress_end - cprogress_start)), h=progress_h).set_bg(RoundRectBg(chara_color, 4, corners=corners)) \
                                    .set_offset((border + int(progress_w * cprogress_start), border))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 200), 4)).set_offset((border, border))
                    else:
                        with Frame().set_padding(8).set_content_align('lt'):
                            Spacer(w=progress_w+border*2, h=progress_h+border*2).set_bg(RoundRectBg((75, 75, 75, 255), 4))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 255), 4)).set_offset((border, border))

                # 活动卡片
                if detail.event_cards:
                    with HSplit().set_padding(16).set_sep(16).set_item_align('c').set_content_align('c'):
                        TextBox("活动卡片", label_style)
                        detail.event_cards = detail.event_cards[:8]
                        card_num = len(detail.event_cards)
                        if card_num <= 4: col_count = card_num
                        elif card_num <= 6: col_count = 3
                        else: col_count = 4
                        with Grid(col_count=col_count).set_sep(4, 4):
                            for card, thumb in zip(detail.event_cards, detail.event_card_thumbs):
                                with VSplit().set_padding(0).set_sep(2).set_item_align('c').set_content_align('c'):
                                    ImageBox(thumb, size=(80, 80))
                                    TextBox(f"ID:{card['id']}", TextStyle(font=DEFAULT_FONT, size=16, color=(75, 75, 75)), overflow='clip')
                
                # 加成
                if detail.bonus_attr or detail.bonus_cuids:
                    with HSplit().set_padding(16).set_sep(8).set_item_align('c').set_content_align('c'):
                        if detail.bonus_attr:
                            TextBox("加成属性", label_style)
                            ImageBox(get_attr_icon(detail.bonus_attr), size=(None, 40))
                        if detail.bonus_cuids:
                            bonus_cids = set([await get_chara_id_by_cuid(ctx, cuid) for cuid in detail.bonus_cuids])
                            bonus_cids = sorted(list(bonus_cids))
                            TextBox("加成角色", label_style)
                            with Grid(col_count=5 if len(bonus_cids) < 20 else 7).set_sep(4, 4):
                                for cid in bonus_cids:
                                    ImageBox(get_chara_icon_by_chara_id(cid), size=(None, 40))

        add_watermark(canvas)
        return await canvas.get_img()

    return await draw(w, h)

# 合成活动记录图片
async def compose_event_record_image(ctx: SekaiHandlerContext, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userEvents','userWorldBlooms'), 
        raise_exc=True)
    user_events: List[Dict[str, Any]] = profile.userEvents
    user_worldblooms: List[Dict[str, Any]] = profile.userWorldBlooms
    for item in user_worldblooms:
        if 'worldBloomChapterPoint' in item:
            item['eventPoint'] = item['worldBloomChapterPoint']

    assert_and_reply(user_events or user_worldblooms, "找不到你的活动记录，可能是未参加过活动，或数据来源未提供userEvents字段")

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))
    style4 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))
    
    async def draw_events(name, user_events):
        topk = 30
        if any('rank' in item for item in user_events):
            has_rank = True
            title = f"排名前{topk}的{name}记录"
            user_events.sort(key=lambda x: (x.get('rank', 1e9), -x.get('eventPoint', 0)))
        else:
            has_rank = False
            title = f"活动点数前{topk}的{name}记录"
            user_events.sort(key=lambda x: -x['eventPoint'])

        user_events = [item for item in user_events if await ctx.md.events.find_by_id(item['eventId'])]
        user_events = user_events[:topk]

        for i, item in enumerate(user_events):
            item['no'] = i + 1
            event = await ctx.md.events.find_by_id(item['eventId'])
            item['banner'] = await get_event_banner_img(ctx, event)
            item['eventName'] = event['name']
            item['startAt'] = datetime.fromtimestamp(event['startAt'] / 1000)
            item['endAt'] = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
            if 'gameCharacterId' in item:
                from .card import get_character_sd_image
                item['charaIcon'] = await get_character_sd_image(item['gameCharacterId'])

        with VSplit().set_padding(16).set_sep(16).set_item_align('lt').set_content_align('lt').set_bg(roundrect_bg()):
            TextBox(title, style1)

            th, sh, gh = 28, 40, 80
            with HSplit().set_padding(16).set_sep(16).set_item_align('lt').set_content_align('lt').set_bg(roundrect_bg()):
                # 活动信息
                with VSplit().set_padding(0).set_sep(sh).set_item_align('c').set_content_align('c'):
                    TextBox("活动", style1).set_h(th).set_content_align('c')
                    for item in user_events:
                        with HSplit().set_padding(0).set_sep(4).set_item_align('l').set_content_align('l').set_h(gh):
                            if 'charaIcon' in item:
                                ImageBox(item['charaIcon'], size=(None, gh))
                            ImageBox(item['banner'], size=(None, gh))
                            with VSplit().set_padding(0).set_sep(2).set_item_align('l').set_content_align('l'):
                                TextBox(f"【{item['eventId']}】{item['eventName']}", style2).set_w(150)
                                TextBox(f"S {item['startAt'].strftime('%Y-%m-%d %H:%M')}", style2)
                                TextBox(f"T {item['endAt'].strftime('%Y-%m-%d %H:%M')}", style2)
                # 排名
                if has_rank:
                    with VSplit().set_padding(0).set_sep(sh).set_item_align('c').set_content_align('c'):
                        TextBox("排名", style1).set_h(th).set_content_align('c')
                        for item in user_events:
                            TextBox(f"#{item.get('rank', '-')}", style3, overflow='clip').set_h(gh).set_content_align('c')
                # 活动点数
                with VSplit().set_padding(0).set_sep(sh).set_item_align('c').set_content_align('c'):
                    TextBox("PT", style1).set_h(th).set_content_align('c')
                    for item in user_events:
                        TextBox(f"{item.get('eventPoint', '-')}", style3, overflow='clip').set_h(gh).set_content_align('c')

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, err_msg)
            TextBox("每次抓包仅包含最近一次活动的记录\n上传时进行增量更新，未上传过的记录将会丢失\n领取活动牌子后上传数据才能记录排名", style4, use_real_line_count=True) \
                .set_bg(roundrect_bg()).set_padding(12)
            with HSplit().set_sep(16).set_item_align('lt').set_content_align('lt'):
                if user_events:
                    await draw_events("活动", user_events)
                if user_worldblooms:
                    await draw_events("WL单榜", user_worldblooms)
    
    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令处理 ======================= #

# 查活动（单个/多个）
MULTI_EVENT_CMDS = ["/pjsk events", "/pjsk_events", "/events", "/活动列表", "/活动一览",]
SINGLE_EVENT_CMDS = ["/pjsk event", "/pjsk_event", "/event", "/活动", "/查活动",]
pjsk_event = SekaiCmdHandler(SINGLE_EVENT_CMDS + MULTI_EVENT_CMDS)
pjsk_event.check_cdrate(cd).check_wblist(gbl)
@pjsk_event.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    async def query_multi(args: str):
        filter = EventListFilter()

        if 'leak' in args:
            filter.leak = True
            args = args.replace('leak', '', 1).strip()

        filter.year, args = extract_year(args)
        filter.attr, args = extract_card_attr(args)
        filter.event_type, args = extract_event_type(args)
        filter.unit, args = extract_unit(args)

        if any([x in args for x in ['混活', '混']]):
            assert_and_reply(not filter.unit, "查混活不能指定团名")
            filter.unit = "blend"
            args = args.replace('混活', "").replace('混', "").strip()

        filter.cids = []
        for seg in args.strip().split():
            if 'ban' in seg or '箱' in seg:
                seg = seg.replace('ban', '').replace('箱', '').strip()
                filter.banner_cid = get_cid_by_nickname(seg)
                assert_and_reply(filter.banner_cid, f"无效的角色昵称\"{seg}\"")
            else:
                cid = get_cid_by_nickname(seg)
                assert_and_reply(cid, f"无效的角色昵称\"{seg}\"")
                filter.cids.append(cid)

        logger.info(f"查询活动列表，筛选条件={filter}")
        return await ctx.asend_reply_msg(await get_image_cq(
            await compose_event_list_image(ctx, filter),
            low_quality=True,
        ))
    
    async def query_single(args: str):
        if args:
            event = await parse_search_single_event_args(ctx, args)
        else:
            event = await get_current_event(ctx, fallback='next_first')
        return await ctx.asend_reply_msg(await get_image_cq(
            await compose_event_detail_image(ctx, event),
            low_quality=True,
        ))

    # 如果参数为空，根据命令区分查询单个还是多个活动
    if not args:
        if ctx.trigger_cmd in MULTI_EVENT_CMDS:
            return await query_multi(args)
        if ctx.trigger_cmd in SINGLE_EVENT_CMDS:
            return await query_single(args)
            
    # 优先查询单个活动
    try:
        return await query_single(args)
    except ReplyException as single_e:
        try:
            return await query_multi(args)
        except ReplyException as multi_e:
            raise ReplyException(f"{get_exc_desc(single_e)}\n{get_exc_desc(multi_e)}\n{QUERY_SINGLE_EVENT_HELP}\n{QUERY_MULTI_EVENT_HELP}")


# 活动剧情总结
pjsk_event_story = SekaiCmdHandler([
    "/pjsk event story", "/pjsk_event_story", 
    "/活动剧情", "/活动故事", "/活动总结",
], regions=['jp'])
pjsk_event_story.check_cdrate(cd).check_wblist(gbl)
@pjsk_event_story.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    refresh = False
    save = True
    if 'refresh' in args:
        refresh = True
        args = args.replace('refresh', '').strip()

    model = get_model_preset("sekai.story_summary.event")
    if 'model:' in args:
        assert_and_reply(check_superuser(ctx.event), "仅超级用户可指定模型")
        model = args.split('model:')[1].strip()
        args = args.split('model:')[0].strip()
        refresh = True
        save = False
        
    if args:
        event = await parse_search_single_event_args(ctx, args)
    else:
        event = await get_current_event(ctx, fallback='next_first')
    await ctx.block_region(str(event['id']))

    resp = await get_event_story_summary(ctx, event, refresh, model, save)
    if isinstance(resp, Image.Image):
        return await ctx.asend_reply_msg(await get_image_cq(resp, low_quality=True))
    else:
        return await ctx.asend_fold_msg(resp)


# 5v5自动送火
pjsk_send_boost = SekaiCmdHandler([
    "/pjsk send boost", "/pjsk_send_boost", "/pjsk grant boost", "/pjsk_grant_boost",
    "/自动送火", "/送火",
], regions=['jp'])
pjsk_send_boost.check_cdrate(cd).check_wblist(gbl)
@pjsk_send_boost.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await send_boost(ctx, ctx.user_id))


# 活动记录
pjsk_event_record = SekaiCmdHandler([
    "/pjsk event record", "/pjsk_event_record", 
    "/活动记录", "/冲榜记录",
])
pjsk_event_record.check_cdrate(cd).check_wblist(gbl)
@pjsk_event_record.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_event_record_image(ctx, ctx.user_id),
        low_quality=True,
    ))
