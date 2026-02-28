from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from .event import get_event_banner_img, get_current_event
from .sk import get_wl_events
from .profile import (
    get_player_bind_id,
    get_basic_profile,
    get_detailed_profile, 
    get_detailed_profile_card, 
    get_detailed_profile_card_filter,
    get_card_full_thumbnail,
)
from .education import get_user_challenge_live_info
from .card import get_unit_by_card_id, has_after_training
from .music import (
    search_music,
    MusicSearchOptions, 
    extract_diff, 
    is_valid_music, 
    get_music_cover_thumb,
    get_valid_musics,
    get_music_diff_info,
    get_musicmetas_json,
)
from .mysekai import MYSEKAI_REGIONS
from sekai_deck_recommend_cpp import (
    DeckRecommendOptions, 
    DeckRecommendCardConfig, 
    DeckRecommendSingleCardConfig,
    DeckRecommendResult,
    DeckRecommendSaOptions,
    RecommendDeck,
)


BOOST_BONUS_DICT: Dict[int, int] = {
    0: 1,
    1: 5,
    2: 10,
    3: 15,
    4: 20,
    5: 25,
    6: 27,
    7: 29,
    8: 31,
    9: 33,
    10: 35,
}

RECOMMEND_TIMEOUT_CFG = config.item('deck.timeout.default')
NO_EVENT_RECOMMEND_TIMEOUT_CFG = config.item('deck.timeout.no_event')
SINGLE_ALG_RECOMMEND_TIMEOUT_CFG = config.item('deck.timeout.single_alg')
BONUS_RECOMMEND_TIMEOUT_CFG = config.item('deck.timeout.bonus_target')
RECOMMEND_ALGS_CFG = config.item('deck.default_algs')
RECOMMEND_ALG_NAMES = {
    'dfs': '暴力搜索',
    'sa': '模拟退火',
    'ga': '遗传算法',
}


OMAKASE_MUSIC_ID = 10000
OMAKASE_MUSIC_DIFFS = ["master", "expert", "hard"]


# ======================= 默认配置 ======================= #

POWER_TARGET_KEYWORDS = ('综合力', '综合', '总合力', '总和', 'power')
SKILL_TARGET_KEYWORDS = ('倍率', '实效', 'skill', '时效')

SKILL_MAX_KEYWORDS = ("满技能", "满技", "skillmax", "技能满级", "slv4")
MASTER_MAX_KEYWORDS = ("满突破", "满破", "rankmax", "mastermax", "5破", "五破")
EPISODE_READ_KEYWORDS = ("剧情已读", "满剧情", "前后篇已读", "前后篇", "已读")
CANVAS_KEYWORDS = ("满画布", "全画布", "画布", "满画板", "全画板", "画板")
DISABLE_KEYWORDS = ("禁用", "disable")
TEAMMATE_POWER_KEYWORDS = ("队友综合力", "队友总合力", "队友综合", "队友总和")
TEAMMATE_SCOREUP_KEYWORDS = ("队友实效", "队友技能", "队友时效")
KEEP_AFTERTRAINING_STATE_KEYWORDS = ("bfes不变", "bf不变")

UNIT_FILTER_KEYWORDS = {
    "light_sound": ["纯ln", "仅ln"],
    "idol": ["纯mmj", "仅mmj"],
    "street": ["纯vbs", "仅vbs"],
    "theme_park": ["纯ws", "仅ws"],
    "school_refusal": ["纯25h", "纯25时", "纯25", "仅25h", "仅25时", "仅25"],
    "piapro": ["纯vs", "纯v", "仅vs", "仅v"],
}
MAX_PROFILE_KEYWORDS = ('顶配', '满配',)
SUB_MAX_PROFILE_KEYWORDS = ('次顶配', '次满配', '中配',)
CURRENT_DECK_KEYWORDS = ('当前', '目前')

MUSIC_COMPARE_KEYWORDS = ('歌曲比较', '歌曲排行', '歌曲排名', '歌曲推荐',)
MUSIC_COMPARE_DEFAULT_MUSIC_NUM = 8
MUSIC_COMPARE_CANDIDATE_MUSIC_NUM = 40
MUSCI_COMPARE_MAX_MUSIC_NUM = 5

MAX_KEYWORDS = ('最高', '最大', '最优', '最强', '最佳')
MIN_KEYWORDS = ('最低', '最小', '最差', '最弱', '最烂')
AVG_KEYWORDS = ('平均', '均值', '期望')

SKILL_ORDER_KEYWORDS = ('技能顺序', '技能排列')
SKILL_REF_KEYWORDS = ('技能抽取', '技能吸取')

BOOST_KEYWORDS = ('boost', '火', '体力', '体',)
AREA_ITEM_KEYWORDS = ('区域道具', '道具', 'areaitem', )

DEFAULT_CARD_CONFIG_12 = DeckRecommendCardConfig()
DEFAULT_CARD_CONFIG_12.disable = False
DEFAULT_CARD_CONFIG_12.level_max = True
DEFAULT_CARD_CONFIG_12.episode_read = True
DEFAULT_CARD_CONFIG_12.master_max = True
DEFAULT_CARD_CONFIG_12.skill_max = True
DEFAULT_CARD_CONFIG_12.canvas = False

DEFAULT_CARD_CONFIG_34bd = DeckRecommendCardConfig()
DEFAULT_CARD_CONFIG_34bd.disable = False
DEFAULT_CARD_CONFIG_34bd.level_max = True
DEFAULT_CARD_CONFIG_34bd.episode_read = False
DEFAULT_CARD_CONFIG_34bd.master_max = False
DEFAULT_CARD_CONFIG_34bd.skill_max = False
DEFAULT_CARD_CONFIG_34bd.canvas = False

NOCHANGE_CARD_CONFIG = DeckRecommendCardConfig()
NOCHANGE_CARD_CONFIG.disable = False
NOCHANGE_CARD_CONFIG.level_max = False
NOCHANGE_CARD_CONFIG.episode_read = False
NOCHANGE_CARD_CONFIG.master_max = False
NOCHANGE_CARD_CONFIG.skill_max = False
NOCHANGE_CARD_CONFIG.canvas = False

DEFAULT_TEAMMATE_POWER = 250000
DEFAULT_TEAMMATE_SCOREUP = 200


def add_payload_segment(payloads: list[bytes], data: bytes):
    payloads.append(len(data).to_bytes(4, 'big'))
    payloads.append(data)

def build_multiparts_payload(payloads: list[bytes]) -> bytes:
    payload = b''.join(payloads)
    return compress_zstd(payload)


# ======================= 参数获取 ======================= #

# 从args中提取live类型 直接修改options 返回剩余参数
def extract_live_type(args: str, options: DeckRecommendOptions) -> str:
    if "多人" in args or '协力' in args: 
        options.live_type = "multi"
        args = args.replace("多人", "").replace("协力", "").strip()
    elif "单人" in args: 
        options.live_type = "solo"
        args = args.replace("单人", "").strip()
    elif "自动" in args or "auto" in args: 
        options.live_type = "auto"
        args = args.replace("自动", "").replace("auto", "").strip()
    else:
        options.live_type = "multi"
    return args.strip()

# 从args获取组卡目标活动（如果是wl则会同时返回cid）返回 (活动, cid, 剩余参数)
async def extract_target_event(
    ctx: SekaiHandlerContext, 
    args: str,
    match_type: str,
    default_return_current: bool,
    raise_if_not_found: bool,
) -> Tuple[Optional[dict], Optional[int], str]:
    def assert_and_reply_or_return(condition: bool, msg: str):
        if raise_if_not_found:
            assert_and_reply(condition, msg)
        else:
            if not condition:
                return (None, None, args)

    # match_type为 simple/full/all 分别对应匹配 123/event123或者活动123/两者皆可
    match_simple = match_type in ('simple', 'all')
    match_full = match_type in ('full', 'all')
    
    # 总是替换终章
    for keyword in ('终章', ):
        if keyword in args:
            args = args.replace(keyword, " event180 " if match_full else " 180 ").strip()

    # 解析成功后需要移除的文本
    event_matched_texts: list[str] = []
    wl_matched_texts: list[str] = []

    # 解析WL 章节序号或角色名
    chapter_id, chapter_nickname = None, None
    for i in range(1, 10):
        if f"wl{i}" in args:
            chapter_id = i
            wl_matched_texts.append(f"wl{i}")
            break
        
    for nickname, cid in get_character_nickname_data().nickname_ids:
        if nickname in args:
            chapter_nickname = nickname
            wl_matched_texts.append(nickname)
            break
    
    # 解析活动id
    event_id = None
    if match_simple:
        simple_match = re.search(r"\b(\d{1,3})\b", args)
        if simple_match:
            event_id = int(simple_match.group(1))
            event_matched_texts.append(simple_match.group(0))
    if match_full:
        full_match = re.search(r"活动(\d+)|event(\d+)", args)
        if full_match:
            event_id = int(full_match.group(1) or full_match.group(2))
            event_matched_texts.append(full_match.group(0))

    if event_id == 0:
        event_id = None
        event_matched_texts = []

    # 获取活动
    if event_id is None:
        # 没有指定活动的情况：寻找当前活动，如果没有就下一个活动
        if not default_return_current:
            assert_and_reply_or_return(False, "请指定一个要查询的活动，例如\"event140\"或\"活动140\"")

        event = await get_current_event(ctx, "next")
        assert_and_reply_or_return(event, """
找不到正在进行或即将开始的活动，指定团队+颜色进行模拟活动组卡，或使用\"/组卡help\"查看如何组往期活动
""".strip())
    else:
        event = await ctx.md.events.find_by_id(event_id)

        # 填充模拟终章
        if event_id == 180 and not event:
            event = { 'id': 180 }
        else:
            assert_and_reply_or_return(event, f"""
    活动{ctx.region}-{event_id}不存在，可以指定团队+颜色进行模拟活动组卡
    """.strip())

    # 获取WL章节
    wl_cid = None
    wl_events = await get_wl_events(ctx, event['id']) if event_id != 180 else []
    if wl_events:
        if not chapter_id and not chapter_nickname:
            # 获取默认章节
            if len(wl_events) == 1:
                # 只有一个章节就直接用
                chapter = wl_events[0]
            elif datetime.now() > datetime.fromtimestamp(event['aggregateAt'] / 1000):
                # 活动已经结束，默认使用最后一个章节
                wl_events.sort(key=lambda x: x['startAt'], reverse=True)
                chapter = wl_events[0]
            elif datetime.now() < datetime.fromtimestamp(event['startAt'] / 1000):
                # 活动还没开始，默认使用第一个章节
                wl_events.sort(key=lambda x: x['startAt'])
                chapter = wl_events[0]
            else:
                # 否则寻找 开始时间 <= 当前 <= 结束时间 的最晚的章节
                ok_chapters = []
                for chapter in wl_events:
                    start_time = datetime.fromtimestamp(chapter['startAt'] / 1000)
                    end_time = datetime.fromtimestamp(chapter['aggregateAt'] / 1000 + 1)
                    if start_time <= datetime.now() <= end_time:
                        ok_chapters.append(chapter)
                assert_and_reply_or_return(ok_chapters, f"请指定一个要查询的WL章节，例如\"event140 wl1\"或\"event140 miku\"")
                ok_chapters.sort(key=lambda x: x['startAt'], reverse=True)
                chapter = ok_chapters[0]
        elif chapter_id:
            chapter = find_by(wl_events, "id", 1000 * chapter_id + event['id'])
            assert_and_reply_or_return(chapter, f"活动 {ctx.region}-{event['id']} 没有章节 {chapter_id}")
        else: 
            cid = get_cid_by_nickname(chapter_nickname)
            chapter = find_by(wl_events, "wl_cid", cid)
            assert_and_reply_or_return(chapter, f"活动 {ctx.region}-{event['id']} 没有 {chapter_nickname} 的章节 ")

        wl_cid = chapter['wl_cid']

    else:
        # 指定 wlx 的情况报错
        assert_and_reply_or_return(not chapter_id, f"活动 {ctx.region}-{event['id']} 不是WL活动，无法指定章节")
        # 指定角色昵称的情况不报错，直接忽略
        wl_matched_texts = []

    # 确认匹配到活动
    for text in event_matched_texts:
        args = args.replace(text, "", 1).strip()
    for text in wl_matched_texts:
        args = args.replace(text, "", 1).strip()
    
    return event, wl_cid, args

# 从args同时获取组卡目标活动或者指定属性&团模拟活动 直接修改options 返回剩余参数
async def extract_target_event_or_simulate_event(
    ctx: SekaiHandlerContext, 
    args: str,
    options: DeckRecommendOptions,
) -> str:
    # 匹配模拟WL活动（角色名+wl1 / wl2）
    for turn in (1, 2):
        if f"wl{turn}" in args:
            for nickname, cid in get_character_nickname_data().nickname_ids:
                if nickname in args:
                    args = args.replace(f"wl{turn}", "", 1).replace(nickname, "", 1).strip()
                    unit = get_unit_by_chara_id(cid)
                    options.event_unit = unit
                    options.world_bloom_event_turn = turn
                    options.world_bloom_character_id = cid
                    return args

    # 25需要优先匹配团队，对于活动id内包含25的情况，必须让团队的25两边不能有数字或者"event"、"活动"
    # 这样只有想以单独数字25指定25期活动时可能产生歧义，为该情况额外添加用户提示
    # 匹配团名和属性名，并检查25的情况
    attr, args = extract_card_attr(args, default=None)
    unit, new_args = extract_unit(args, default=None)
    index_25 = args.find('25')
    if unit == "piapro" and 'vs' not in args:
        # vs加成活动只能匹配vs，避免v的误匹配
        unit = None
    elif unit == "school_refusal" and index_25 != -1:
        left = args[index_25 - 1] if index_25 - 1 >= 0 else ' '
        right = args[index_25 + 2] if index_25 + 2 < len(args) else ' '
        if left.isdigit() or right.isdigit() or left in ('t', '活'):
            # 取消匹配，把参数让给活动匹配
            unit = None
        else:
            args = new_args
    else:
        args = new_args

    if unit and attr:
        options.event_unit = unit
        options.event_attr = attr
        return args
    if unit or attr:
        hint = f"你在参数中指定了{'团名' if unit else '颜色'}，"
        hint += f"这意味着你需要加成是指定团+颜色的模拟活动组卡，"
        hint += f"请再指定{'颜色' if unit else '团名'}\n"
        hint += f"如果想限制仅某团/某颜色卡牌上场，请使用"
        if unit: hint += f" \"仅{UNIT_ABBRS[unit]}\""
        if attr: hint += f" \"仅{CARD_ATTR_ABBR[attr]}\""
        hint += "\n"
        if index_25 != -1:
            hint += "如果你想指定是25期活动而不是25时，请使用 \"event25\""
        raise ReplyException(hint.strip())

    # 匹配活动
    event, wl_cid, args = await extract_target_event(
        ctx, 
        args, 
        match_type="all", 
        default_return_current=True, 
        raise_if_not_found=True,
    )
    options.event_id = event['id']
    options.world_bloom_character_id = wl_cid
    return args

# 从args中提取组卡目标
def extract_target(args: str, options: DeckRecommendOptions) -> str:
    options.target = "score"

    for keyword in POWER_TARGET_KEYWORDS:
        if keyword in args:
            args = args.replace(keyword, "").strip()
            options.target = "power"
            break

    for keyword in SKILL_TARGET_KEYWORDS:
        if keyword in args:
            args = args.replace(keyword, "").strip()
            options.target = "skill"
            break
    
    return args.strip()

# 从args中提取随机因素选择策略
def extract_random_strategy(
    args: str, 
    options: DeckRecommendOptions,
    default_skill_order_strategy: str,
    default_skill_reference_strategy: str,
) -> str:
    for seg in args.split():
        # 技能顺序选择
        for keyword in SKILL_ORDER_KEYWORDS:
            if keyword in seg:
                if any(kw in seg for kw in MAX_KEYWORDS):
                    options.skill_order_choose_strategy = "max"
                elif any(kw in seg for kw in MIN_KEYWORDS):
                    options.skill_order_choose_strategy = "min"
                elif any(kw in seg for kw in AVG_KEYWORDS):
                    options.skill_order_choose_strategy = "average"
                else:
                    # 指定顺序
                    options.skill_order_choose_strategy = "specific"
                    try:
                        order = seg.replace(keyword, "").strip()
                        order = [int(c) - 1 for c in order]
                        assert set(order) == set(range(5))
                        options.specific_skill_order = order
                    except:
                        raise ReplyException("""
指定技能顺序方式:
最优顺序: /指令 ... 技能顺序最优
最差顺序: /指令 ... 技能顺序最差
平均顺序: /指令 ... 技能顺序平均
特定顺序: /指令 ... 技能顺序12345
""".strip())
                args = args.replace(seg, "", 1).strip()
        # 技能吸取选择
        for keyword in SKILL_REF_KEYWORDS:
            if keyword in seg:
                if any(kw in seg for kw in MAX_KEYWORDS):
                    options.skill_reference_choose_strategy = "max"
                elif any(kw in seg for kw in MIN_KEYWORDS):
                    options.skill_reference_choose_strategy = "min"
                elif any(kw in seg for kw in AVG_KEYWORDS):
                    options.skill_reference_choose_strategy = "average"
                args = args.replace(seg, "", 1).strip()
    
    if options.skill_order_choose_strategy is None:
        options.skill_order_choose_strategy = default_skill_order_strategy
    if options.skill_reference_choose_strategy is None:
        options.skill_reference_choose_strategy = default_skill_reference_strategy

    return args.strip()
                
# 从args中提取固定卡牌
def extract_fixed_cards_and_characters(args: str, options: DeckRecommendOptions) -> str:
    args = args.replace('＃', '#')
    if '#' in args:
        args, fixed_args = args.split('#', 1)
        fixed_cards, fixed_characters = [], []
        try:
            # 固定卡牌
            fixed_cards = list(map(int, fixed_args.strip().split()))
        except:
            try:
                # 固定角色
                for seg in fixed_args.strip().split():
                    nickname, _ = extract_nickname_from_args(seg)
                    assert nickname
                    fixed_characters.append(get_cid_by_nickname(nickname))
                assert fixed_characters
            except:
                raise ReplyException("""
格式错误，#固定卡牌 或 #固定角色 必须放在最后，示例:
/组卡指令 其他参数 #123 456 789...
/组卡指令 其他参数 #miku rin...
如果你想在固定卡牌时同时指定该卡牌的状态，示例:
/组卡指令 123满技能满破 #123
""".strip())

        if fixed_cards:
            assert_and_reply(len(fixed_cards) <= 5, f"固定卡牌数量不能超过5张")
            assert_and_reply(len(set(fixed_cards)) == len(fixed_cards), "固定卡牌不能重复")
            options.fixed_cards = fixed_cards

        elif fixed_characters:
            assert_and_reply(len(fixed_characters) <= 5, f"固定角色数量不能超过5个")
            assert_and_reply(len(set(fixed_characters)) == len(fixed_characters), "固定角色不能重复")
            options.fixed_characters = fixed_characters

    return args.strip()

# 从args中提取卡牌设置
def extract_card_config(args: str, options: DeckRecommendOptions, default_nochange=False) -> str:
    def get_prefix_digit(s: str) -> Optional[int]:
        d = ""
        for c in s:
            if c.isdigit():
                d += c
            else:
                break
        return int(d) if d else None

    def has_config_keyword(s: str):
        return any(
            keyword in s for keyword 
            in DISABLE_KEYWORDS + SKILL_MAX_KEYWORDS + MASTER_MAX_KEYWORDS + EPISODE_READ_KEYWORDS + CANVAS_KEYWORDS
        )

    def apply_card_config(args: str, cfgs: List[DeckRecommendCardConfig]) -> str:
        for keyword in DISABLE_KEYWORDS:
            if keyword in args:
                for cfg in cfgs:
                    cfg.disable = True
                args = args.replace(keyword, "").strip()
                break
        for keyword in SKILL_MAX_KEYWORDS:
            if keyword in args:
                for cfg in cfgs:
                    cfg.skill_max = True
                args = args.replace(keyword, "").strip()
                break
        for keyword in MASTER_MAX_KEYWORDS:
            if keyword in args:
                for cfg in cfgs:
                    cfg.master_max = True
                args = args.replace(keyword, "").strip()
                break
        for keyword in EPISODE_READ_KEYWORDS:
            if keyword in args:
                for cfg in cfgs:
                    cfg.episode_read = True
                args = args.replace(keyword, "").strip()
                break
        for keyword in CANVAS_KEYWORDS:
            if keyword in args:
                for cfg in cfgs:
                    cfg.canvas = True
                args = args.replace(keyword, "").strip()
                break
        return args.strip()

    if default_nochange:
        options.rarity_1_config = NOCHANGE_CARD_CONFIG
        options.rarity_2_config = NOCHANGE_CARD_CONFIG
        options.rarity_3_config = NOCHANGE_CARD_CONFIG
        options.rarity_4_config = NOCHANGE_CARD_CONFIG
        options.rarity_birthday_config = NOCHANGE_CARD_CONFIG
    else:
        options.rarity_1_config = DEFAULT_CARD_CONFIG_12
        options.rarity_2_config = DEFAULT_CARD_CONFIG_12
        options.rarity_3_config = DEFAULT_CARD_CONFIG_34bd
        options.rarity_4_config = DEFAULT_CARD_CONFIG_34bd
        options.rarity_birthday_config = DEFAULT_CARD_CONFIG_34bd

    segs = args.split()

    # 稀有度单独设置
    for rarity, cfg in [
        ('一星', options.rarity_1_config),
        ('二星', options.rarity_2_config),
        ('三星', options.rarity_3_config),
        ('四星', options.rarity_4_config),
        ('生日', options.rarity_birthday_config),
    ]:
        for seg in segs:
            if seg.startswith(rarity) and has_config_keyword(seg):
                apply_card_config(seg, [cfg])
                args = args.replace(seg, "").strip()
    
    # 卡牌单独设置
    single_card_configs = []
    for seg in segs:
        card_id = get_prefix_digit(seg)
        if card_id is not None and has_config_keyword(seg):
            cfg = DeckRecommendSingleCardConfig()
            cfg.card_id = card_id
            cfg.level_max = True
            apply_card_config(seg, [cfg])
            single_card_configs.append(cfg)
            args = args.replace(seg, "").strip()
    options.single_card_configs = single_card_configs

    # 全体设置
    args = apply_card_config(args, [
        options.rarity_1_config,
        options.rarity_2_config,
        options.rarity_3_config,
        options.rarity_4_config,
        options.rarity_birthday_config,
    ])

    # bfes不变设置
    options.keep_after_training_state = False
    for keyword in KEEP_AFTERTRAINING_STATE_KEYWORDS:
        if keyword in args:
            options.keep_after_training_state = True
            args = args.replace(keyword, "").strip()
            break

    return args

# 从args中提取多人live相关设置
def extract_multilive_options(args: str, options: DeckRecommendOptions) -> str:
    if options.live_type != "multi":
        return args.strip()

    options.multi_live_teammate_power = DEFAULT_TEAMMATE_POWER
    options.multi_live_teammate_score_up = DEFAULT_TEAMMATE_SCOREUP

    segs = args.split()
    for seg in segs:
        for keyword in TEAMMATE_POWER_KEYWORDS:
            if keyword in seg:
                value = seg.replace(keyword, "").strip()
                try:
                    options.multi_live_teammate_power = parse_large_number(value)
                    args = args.replace(seg, "", 1).strip()
                    break
                except:
                    raise ReplyException(f"无法解析指定的队友综合力\"{value}\"")
        for keyword in TEAMMATE_SCOREUP_KEYWORDS:
            if keyword in seg:
                value = seg.replace(keyword, "").strip()
                try:
                    options.multi_live_teammate_score_up = int(value)
                    args = args.replace(seg, "", 1).strip()
                    break
                except:
                    raise ReplyException(f"无法解析指定的队友实效\"{value}\"")
        for keyword in SKILL_TARGET_KEYWORDS:
            if keyword in seg:
                value = seg.replace(keyword, "").strip()
                if value.isdigit():
                    options.multi_live_score_up_lower_bound = int(value)
                    options.multi_live_teammate_score_up = int(value)
                    args = args.replace(seg, "", 1).strip()
                    break

    return args.strip()

# 从args中提取歌曲和难度，返回用于匹配歌曲的参数
async def extract_music_and_diff(
    ctx: SekaiHandlerContext, 
    args: str, 
    options: DeckRecommendOptions, 
    rec_type: str, 
    live_type: str, 
    additional_args: dict,
) -> str:
    # jp_ctx = SekaiHandlerContext.from_region('jp')
    search_options = MusicSearchOptions(
        use_emb=False,
        use_id=True,
        use_nidx=True,
        raise_when_err=False,
    )

    # 歌曲比较模式匹配（多首歌曲）
    if additional_args.get('music_compare'):
        segs = args.split()
        assert_and_reply(len(segs) <= MUSCI_COMPARE_MAX_MUSIC_NUM, f"最多只能指定 {MUSCI_COMPARE_MAX_MUSIC_NUM} 首歌曲进行比较")
        additional_args['music_diffs_to_compare'] = []
        for seg in segs:
            music_diff, seg = extract_diff(seg, default='master')
            search_options.diff = music_diff
            music = (await search_music(ctx, seg, search_options)).music
            assert_and_reply(music, f"在组卡支持的所有歌曲中找不到\"{seg}\"")
            additional_args['music_diffs_to_compare'].append((music['id'], music_diff, seg))
        return ""

    # 一般歌曲匹配（单首歌曲&默认判断）
    options.music_diff, args = extract_diff(args, default=None)
    args = args.strip()
    if args:
        search_options.diff = options.music_diff
        music = (await search_music(ctx, args, search_options)).music
        err_msg = f"在组卡支持的所有歌曲中找不到\"{args}\""
        if len(args.split()) > 1:
            err_msg += "，如果你要对多首歌曲进行比较，请加上\"歌曲比较\""
        err_msg += f"，发送\"{ctx.trigger_cmd}help\"查看帮助"
        assert_and_reply(music, err_msg)
        options.music_id = music['id']

    # 已指定歌曲和难度
    if options.music_id is not None and options.music_diff is not None:
        return args

    # 只指定歌曲未指定难度：默认使用master
    if options.music_id is not None and options.music_diff is None:
        options.music_diff = 'master'
        return args
    
    # 未指定歌曲：查找默认歌曲
    default_musicdiffs = config.get('deck.default_musicdiffs')[rec_type]
    if isinstance(default_musicdiffs, dict):
        default_musicdiffs = default_musicdiffs[live_type]
    for mid, diff in default_musicdiffs:
        if mid == 'omakase': mid = OMAKASE_MUSIC_ID
        if mid == OMAKASE_MUSIC_ID or await is_valid_music(ctx, mid, leak=False, diff=diff):
            options.music_id = mid
            options.music_diff = diff
            additional_args['use_default_music'] = True
            return args
        
    raise Exception("组卡未正确配置默认歌曲")

# 从args中提取不在options中的参数
def extract_addtional_options(args: str) -> Tuple[dict, str]:
    ret = {}

    for boost in reversed(BOOST_BONUS_DICT.keys()):
        for keyword in BOOST_KEYWORDS:
            kw = f"{boost}{keyword}"
            if kw in args:
                ret['boost'] = boost
                args = args.replace(kw, "", 1).strip()
                break

    for level in reversed(range(1, 21)):
        for keyword in AREA_ITEM_KEYWORDS:
            if keyword not in args:
                continue
            for kw in (
                f"{keyword}{level}级",
                f"{level}级{keyword}",
                f"{keyword}{level}",
                f"{level}{keyword}",
            ):
                if kw in args:
                    ret['area_item_level'] = level
                    args = args.replace(kw, "", 1).strip()
                    break

    for unit, keywords in UNIT_FILTER_KEYWORDS.items():
        for keyword in keywords:
            if keyword in args:
                ret['unit_filter'] = unit
                args = args.replace(keyword, "", 1).strip()
                break

    for names in CARD_ATTR_NAMES:
        for name in names:
            keyword = '纯' + name
            if keyword in args:
                ret['attr_filter'] = names[0]
                args = args.replace(keyword, "", 1).strip()
                break
            keyword = '仅' + name
            if keyword in args:
                ret['attr_filter'] = names[0]
                args = args.replace(keyword, "", 1).strip()
                break

    for keyword in SUB_MAX_PROFILE_KEYWORDS:
        if keyword in args:
            ret['sub_max_profile'] = True
            args = args.replace(keyword, "", 1).strip()
            break
    
    for keyword in MAX_PROFILE_KEYWORDS:
        if keyword in args:
            ret['max_profile'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    for keyword in CURRENT_DECK_KEYWORDS:
        if keyword in args:
            ret['use_current_deck'] = True
            args = args.replace(keyword, "", 1).strip()
            break

    for keyword in MUSIC_COMPARE_KEYWORDS:
        if keyword in args:
            ret['music_compare'] = True
            args = args.replace(keyword, "", 1).strip()

    ret['excluded_cards'] = []
    segs = args.split()
    for seg in segs:
        if seg[0] == '-' and seg[1:].isdigit():
            try:
                x = int(seg[1:])
                if 0 < x < 5000:
                    ret['excluded_cards'].append(x)
                    args = args.replace(seg, "", 1).strip()
            except ValueError:
                pass

    return ret, args.strip()


# 从args中提取活动组卡参数
async def extract_event_options(ctx: SekaiHandlerContext, args: str) -> Dict:
    args = ctx.get_args().strip().lower()
    options = DeckRecommendOptions()

    additional, args = extract_addtional_options(args)

    args = extract_live_type(args, options)
    args = extract_random_strategy(args, options, "average", "average")
    args = extract_multilive_options(args, options)
    args = extract_fixed_cards_and_characters(args, options)
    args = extract_card_config(args, options)
    args = extract_target(args, options)

    # 算法
    options.algorithm = "all"
    options.timeout_ms = int(RECOMMEND_TIMEOUT_CFG.get() * 1000)
    if "dfs" in args:
        options.algorithm = "dfs"
        args = args.replace("dfs", "").strip()
        options.timeout_ms = int(SINGLE_ALG_RECOMMEND_TIMEOUT_CFG.get() * 1000)

    # 活动id
    args = await extract_target_event_or_simulate_event(ctx, args, options)
        
    # 歌曲id和难度
    args = await extract_music_and_diff(ctx, args, options, "event", options.live_type, additional)

    # 组卡限制
    options.limit = config.get('deck.return_deck_num.multi')

    # 模拟退火设置
    options.sa_options = DeckRecommendSaOptions()
    options.sa_options.max_no_improve_iter = 10000

    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }

# 从args中提取挑战组卡参数
async def extract_challenge_options(ctx: SekaiHandlerContext, args: str) -> Dict:
    args = ctx.get_args().strip().lower()
    options = DeckRecommendOptions()

    additional, args = extract_addtional_options(args)

    args = extract_live_type(args, options)
    options.live_type = 'challenge_auto' if options.live_type == 'auto' else 'challenge'
    random_strategy = 'average' if 'auto' in options.live_type else 'max'
    args = extract_random_strategy(args, options, random_strategy, random_strategy)
    args = extract_fixed_cards_and_characters(args, options)
    args = extract_card_config(args, options)
    args = extract_target(args, options)

    # 算法
    options.algorithm = "all"
    options.timeout_ms = int(RECOMMEND_TIMEOUT_CFG.get() * 1000)
    if "dfs" in args:
        options.algorithm = "dfs"
        args = args.replace("dfs", "").strip()
        options.timeout_ms = int(SINGLE_ALG_RECOMMEND_TIMEOUT_CFG.get() * 1000)
    
    # 指定角色
    options.challenge_live_character_id = None
    segs = args.split()
    full_nickname, part_nickname = None, None
    for seg in segs:
        nickname, rest = extract_nickname_from_args(seg)
        if rest.isdigit():  # 不匹配角色名+数字
            continue
        if not full_nickname and nickname and not rest:
            full_nickname = nickname
        if not part_nickname and nickname:
            part_nickname = nickname 
    # 优先使用完全匹配的昵称
    if full_nickname:
        options.challenge_live_character_id = get_cid_by_nickname(full_nickname)
        args = args.replace(full_nickname, "", 1).strip()
    elif part_nickname:
        options.challenge_live_character_id = get_cid_by_nickname(part_nickname)
        args = args.replace(part_nickname, "", 1).strip()
    ## 不指定角色情况下每个角色都组1个最强卡

    # 歌曲id和难度
    args = await extract_music_and_diff(ctx, args, options, "challenge", options.live_type, additional)

    # 组卡限制
    options.limit = config.get('deck.return_deck_num.challenge')

    # 模拟退火设置
    options.sa_options = DeckRecommendSaOptions()
    if options.challenge_live_character_id is None:
        options.sa_options.run_num = 5  # 不指定角色情况下适当减少模拟退火次数

    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }

# 从args中提取长草组卡参数
async def extract_no_event_options(ctx: SekaiHandlerContext, args: str) -> Dict:
    args = ctx.get_args().strip().lower()
    options = DeckRecommendOptions()

    additional, args = extract_addtional_options(args)

    args = extract_live_type(args, options)
    args = extract_random_strategy(args, options, "average", "average")
    args = extract_multilive_options(args, options)
    args = extract_fixed_cards_and_characters(args, options)
    args = extract_card_config(args, options)
    args = extract_target(args, options)

    # 算法
    options.algorithm = "all"
    options.timeout_ms = int(NO_EVENT_RECOMMEND_TIMEOUT_CFG.get() * 1000)
    if "dfs" in args:
        options.algorithm = "dfs"
        args = args.replace("dfs", "").strip()
        options.timeout_ms = int(SINGLE_ALG_RECOMMEND_TIMEOUT_CFG.get() * 1000)

    # 活动id
    options.event_id = None
        
    # 歌曲id和难度
    args = await extract_music_and_diff(ctx, args, options, "event", options.live_type, additional)

    # 组卡限制
    options.limit = config.get('deck.return_deck_num.multi')

    # 模拟退火设置
    options.sa_options = DeckRecommendSaOptions()
    options.sa_options.max_no_improve_iter = 50000

    return {
        'options': options,
        'last_args': args.strip(),
        'additional': additional,
    }

# 从args中提取加成组卡参数
async def extract_bonus_options(ctx: SekaiHandlerContext, args: str) -> Dict:
    args = ctx.get_args().strip().lower()
    options = DeckRecommendOptions()

    additional, args = extract_addtional_options(args)

    options.algorithm = "dfs"
    options.timeout_ms = int(BONUS_RECOMMEND_TIMEOUT_CFG.get() * 1000)
    options.target = "bonus"
    options.live_type = "solo"

    # 卡牌设置
    options.rarity_1_config = NOCHANGE_CARD_CONFIG
    options.rarity_2_config = NOCHANGE_CARD_CONFIG
    options.rarity_3_config = NOCHANGE_CARD_CONFIG
    options.rarity_4_config = NOCHANGE_CARD_CONFIG
    options.rarity_birthday_config = NOCHANGE_CARD_CONFIG

    # 活动id
    event, wl_cid, args = await extract_target_event(
        ctx, args, 
        match_type="full",
        default_return_current=True,
        raise_if_not_found=True,
    )
    options.event_id = event['id']
    options.world_bloom_character_id = wl_cid
        
    # 歌曲id和难度
    await extract_music_and_diff(ctx, "", options, "event", options.live_type, additional)

    # 组卡限制
    options.limit = config.get('deck.return_deck_num.bonus')

    # 目标加成
    try:
        options.target_bonus_list = list(map(int, args.split()))
        assert options.target_bonus_list
    except:
        raise ReplyException("""
使用方式: /加成组卡 加成 其他参数...
例如: /加成组卡 120
""".strip())

    return {
        'options': options,
        'last_args': '',
        'additional': additional,
    }

# 从args中提取烤森组卡参数
async def extract_mysekai_options(ctx: SekaiHandlerContext, args: str) -> Dict:
    args = ctx.get_args().strip().lower()
    options = DeckRecommendOptions()

    additional, args = extract_addtional_options(args)

    options.algorithm = "ga"
    options.timeout_ms = int(RECOMMEND_TIMEOUT_CFG.get() * 1000)
    options.live_type = "mysekai"

    args = extract_fixed_cards_and_characters(args, options)
    args = extract_card_config(args, options, default_nochange=True)

    args = await extract_target_event_or_simulate_event(ctx, args, options)

    # 组卡限制
    options.limit = config.get('deck.return_deck_num.mysekai')

    # 歌曲id和难度
    await extract_music_and_diff(ctx, "", options, "event", "multi", additional)

    return {
        'options': options,
        'last_args': '',
        'additional': additional,
    }


# ======================= 处理逻辑 ======================= #

RECOMMEND_SERVERS_CFG = config.item("deck.servers")
_deckrec_request_id = 0


# 添加OMAKASE音乐
def add_omakase_music(music_metas: list[dict]) -> list[dict]:
    if find_by(music_metas, "id", OMAKASE_MUSIC_ID) is None:
        omakase = {
            "music_id": OMAKASE_MUSIC_ID,
            "difficulty": None,
            "music_time": 0.0,
            "event_rate": 0.0,
            "base_score": 0.0,
            "base_score_auto": 0.0,
            "skill_score_solo": [0.0 for _ in range(6)],
            "skill_score_auto": [0.0 for _ in range(6)],
            "skill_score_multi": [0.0 for _ in range(6)],
            "fever_score": 0,
            "fever_end_time": 0,
            "tap_count": 0,
        }

        music_count = 0
        for item in music_metas:
            if item['difficulty'] in OMAKASE_MUSIC_DIFFS:
                omakase['music_time'] += item['music_time']
                omakase['event_rate'] += item['event_rate']
                omakase['base_score'] += item['base_score']
                omakase['base_score_auto'] += item['base_score_auto']
                for i in range(6):
                    omakase['skill_score_solo'][i] += item['skill_score_solo'][i]
                    omakase['skill_score_auto'][i] += item['skill_score_auto'][i]
                    omakase['skill_score_multi'][i] += item['skill_score_multi'][i]
                omakase['fever_score'] += item['fever_score']
                omakase['fever_end_time'] += item['fever_end_time']
                omakase['tap_count'] += item['tap_count']
                music_count += 1

        omakase['music_time'] /= music_count
        omakase['event_rate'] = int(omakase['event_rate'] / music_count)
        omakase['base_score'] /= music_count
        omakase['base_score_auto'] /= music_count
        for i in range(6):
            omakase['skill_score_solo'][i] /= music_count
            omakase['skill_score_auto'][i] /= music_count
            omakase['skill_score_multi'][i] /= music_count
        omakase['fever_score'] /= music_count
        omakase['fever_end_time'] /= music_count
        omakase['tap_count'] = int(omakase['tap_count'] / music_count)

        for difficulty in ('easy', 'normal', 'hard', 'expert', 'master', 'append'):
            new_omakase = omakase.copy()
            new_omakase['difficulty'] = difficulty
            new_music_metas = music_metas + [new_omakase]
    return new_music_metas

# 获取deck的hash
def get_deck_hash(deck: RecommendDeck) -> str:
    deck_hash = str(deck.score) + str(deck.total_power) + str(deck.cards[0].card_id)
    return deck_hash

# 打印组卡配置
def log_options(ctx: SekaiHandlerContext, user_id: int, options: DeckRecommendOptions):
    def cardconfig2str(cfg: DeckRecommendCardConfig):
        return f"{(int)(cfg.disable)}{(int)(cfg.level_max)}{(int)(cfg.episode_read)}{(int)(cfg.master_max)}{(int)(cfg.skill_max)}"
    log = "组卡配置: "
    log += f"region={ctx.region}, "
    log += f"uid={user_id}, "
    log += f"type={options.live_type}, "
    log += f"mid={options.music_id}, "
    log += f"mdiff={options.music_diff}, "
    log += f"eid={options.event_id}, "
    log += f"wl_cid={options.world_bloom_character_id}, "
    log += f"challenge_cid={options.challenge_live_character_id}, "
    log += f"limit={options.limit}, "
    log += f"member={options.member}, "
    log += f"rarity1={cardconfig2str(options.rarity_1_config)}, "
    log += f"rarity2={cardconfig2str(options.rarity_2_config)}, "
    log += f"rarity3={cardconfig2str(options.rarity_3_config)}, "
    log += f"rarity4={cardconfig2str(options.rarity_4_config)}, "
    log += f"rarity_bd={cardconfig2str(options.rarity_birthday_config)}, "
    log += f"fixed_cards={options.fixed_cards}"
    logger.info(log)

# 自动组卡实现（批量提交），每批会发送到同一个后端，返回 [Tuple[结果，结果算法来源，Dict[算法: Tuple[耗时，等待时间]]], ...]
async def do_deck_recommend_batch(
    ctx: SekaiHandlerContext, 
    options_list: list[DeckRecommendOptions],
    user_data: bytes,
) -> list[Tuple[DeckRecommendResult, List[str], Dict[str, Tuple[timedelta, timedelta]]]]:
    # 获取组卡后端相关信息
    servers = RECOMMEND_SERVERS_CFG.get()
    if not servers:
        raise ReplyException("未配置可用的组卡服务")
    server_urls = [s['url'] for s in servers]
    server_weights = [s['weight'] for s in servers]
    server_url_indices = list(range(len(server_urls)))
    server_min_weight = min([w for w in server_weights if w > 0], default=0)
    if server_min_weight <= 0:
        raise ReplyException("未配置可用的组卡服务")
    
    # 负载均衡决定请求后端优先级
    global _deckrec_request_id
    _deckrec_request_id += 1
    server_order = [[] for _ in range(server_min_weight)]
    for i, w in enumerate(server_weights):
        for j in range(w):
            server_order[j % len(server_order)].append(i)
    server_order = [idx for sublist in server_order for idx in sublist]
    select_idx = server_order[_deckrec_request_id % len(server_order)]
    urls = server_urls[select_idx:] + server_urls[:select_idx]
    url_indices = server_url_indices[select_idx:] + server_url_indices[:select_idx]

    # 通用请求函数
    async def req(payload: bytes, url: str) -> dict:
        async with get_client_session().post(url, data=payload) as resp:
            if resp.status != 200:
                msg = f"{resp.status}: "
                try:
                    err_data = await resp.json()
                    msg += err_data.get('detail', '')
                except:
                    try: msg += await resp.text()
                    except: pass
                raise ReplyException(msg)
            return await resp.json()

    # 用户数据负载
    userdata_payload = []
    add_payload_segment(userdata_payload, user_data)
    userdata_payload = build_multiparts_payload(userdata_payload)

    # 组卡请求数据，以及原始options_list的索引映射（用于结果归类）
    recommend_data = { 'region': ctx.region, 'batch_options': [] }
    original_indices = []
    for i, options in enumerate(options_list):
        if options.algorithm == "all": 
            algs = RECOMMEND_ALGS_CFG.get()
        else:
            algs = [options.algorithm]
        for alg in algs:
            opt = options.to_dict()
            opt['algorithm'] = alg
            recommend_data['batch_options'].append(opt)
            original_indices.append(i)
    
    # 按后端优先级进行组卡请求
    errors = {}
    result_list = None
    for url, url_index in zip(urls, url_indices):
        # 向该后端缓存用户数据段
        try:
            res = await req(userdata_payload, url + "/cache_userdata")
            recommend_data['userdata_hash'] = res.get('userdata_hash')
            payload = []
            add_payload_segment(payload, dumps_json(recommend_data, indent=False).encode('utf-8'))
            with ProfileTimer("deckrec.request"):
                result_list = await req(build_multiparts_payload(payload), url + "/recommend")
            break
        except Exception as e:
            logger.warning(f"组卡请求 {url} 失败: {get_exc_desc(e)}")
            errors.setdefault(get_exc_desc(e), []).append(url_index+1)
    
    # 所有后端请求均失败
    if result_list is None:
        error_text = ""
        for err_msg, url_idxs in errors.items():
            error_text += "".join([f"[{url_index}]" for url_index in url_idxs]) + f" {err_msg}\n"
        raise ReplyException(f"请求所有可用的组卡服务失败:\n" + error_text.strip())

    # 结果归类整理
    result_dict = {}
    for original_index, result in zip(original_indices, result_list):
        result_dict.setdefault(original_index, []).append(result)
    
    ret = []
    for index in range(len(options_list)):
        results = result_dict[index]
        # 结果排序去重
        decks: List[RecommendDeck] = []
        cost_and_wait_times = {}
        deck_src_alg = {}
        for resp in results:
            alg = resp['alg']
            cost_time = resp['cost_time']
            wait_time = resp['wait_time']
            result = DeckRecommendResult.from_dict(resp['result'])
            cost_and_wait_times[alg] = (cost_time, wait_time)
            for deck in result.decks:
                deck_hash = get_deck_hash(deck)
                if deck_hash not in deck_src_alg:
                    deck_src_alg[deck_hash] = alg
                    decks.append(deck)
                else:
                    deck_src_alg[deck_hash] += "+" + alg
        def key_func(deck: RecommendDeck):
            if options.live_type == "mysekai":
                return (deck.mysekai_event_point, deck.total_power)
            elif options.target == "score":
                return (deck.score, deck.multi_live_score_up)
            elif options.target == "power":
                return deck.total_power
            elif options.target == "skill":
                return deck.multi_live_score_up
            elif options.target == "bonus":
                return (-deck.event_bonus_rate, deck.score)
        limit = options.limit if options.target != "bonus" else options.limit * len(options.target_bonus_list)
        decks = sorted(decks, key=key_func, reverse=True)[:limit]
        src_algs = [deck_src_alg[get_deck_hash(deck)] for deck in decks]
        res = DeckRecommendResult()
        # 加成组卡的队伍按照加成排序
        if options.target == "bonus":
            for deck in decks:
                deck.cards = sorted(deck.cards, key=lambda x: x.event_bonus_rate, reverse=True)
        res.decks = decks
        ret.append((res, src_algs, cost_and_wait_times))
    return ret

# 构造顶配profile
async def construct_max_profile(ctx: SekaiHandlerContext, max_area_item_level: int | None = None) -> dict:
    try: 
        await ctx.md.mysekai_gates.get()
        has_mysekai = True
    except:
        has_mysekai = False

    p = {
        'userGamedata': {},
        'userDecks': [],
        'userCards': [],
        'userHonors': [],
        "userMysekaiCanvases": [],
        "userCharacters": [],
        "userMysekaiGates": [],
        "userMysekaiFixtureGameCharacterPerformanceBonuses": [],
        "userAreas": [],
    }

    for card in await ctx.md.cards.get():
        release_time = datetime.fromtimestamp(card['releaseAt'] / 1000)
        if release_time > datetime.now():
            continue
        episodes = await ctx.md.card_episodes.find_by("cardId", card['id'], mode='all')
        
        match card['cardRarityType']:
            case "rarity_1": level = 20
            case "rarity_2": level = 30
            case "rarity_3": level = 50
            case "rarity_4": level = 60
            case "rarity_birthday": level = 60

        p["userCards"].append({
            "cardId": card['id'],
            "level": level,
            "skillLevel": 4,
            "masterRank": 5,
            "specialTrainingStatus": "done" if has_after_training(card) else "none",
            "defaultImage": "special_training" if has_after_training(card) else "original",
            "episodes": [
                {
                    "cardEpisodeId": ep['id'],
                    "scenarioStatus": "already_read",
                } for ep in episodes
            ]
        })
        if has_mysekai:
            p['userMysekaiCanvases'].append({
                'cardId': card['id'],
                "quantity": 1,
            })

    for honor in await ctx.md.honors.get():
        if honor.get('levels'):
            p['userHonors'].append({
                "honorId": honor['id'],
                "level": honor['levels'][-1]['level'],
            })

    for cid in range(1, 27):
        p['userCharacters'].append({
            "characterId": cid,
            "characterRank": 120,
        })

    if has_mysekai:
        for gid in range(1, 6):
            p['userMysekaiGates'].append({
                "mysekaiGateId": gid,
                "mysekaiGateLevel": 40,
            })
        
        fixture_chara_bonus = { cid: 0 for cid in range(1, 27) }
        for fixture in await ctx.md.mysekai_fixtures.get():
            bid = fixture.get('mysekaiFixtureGameCharacterGroupPerformanceBonusId')
            if bid:
                cid = (bid - 1) // 3 + 1
                t = (bid - 1) % 3
                if t == 0: fixture_chara_bonus[cid] += 1
                elif t == 1: fixture_chara_bonus[cid] += 3
                else: fixture_chara_bonus[cid] += 6
        for cid in range(1, 27):
            p['userMysekaiFixtureGameCharacterPerformanceBonuses'].append({
                "gameCharacterId": cid,
                "totalBonusRate": min(fixture_chara_bonus[cid], 100)
            })
    
    levels = {}
    for item in await ctx.md.area_item_levels.get():
        item_id = item['areaItemId']
        lv = item['level']
        if max_area_item_level is not None and lv > max_area_item_level:
            continue
        levels[item_id] = max(levels.get(item_id, 0), lv)
    p['userAreas'].append({
        "userAreaStatus": {},
        "areaItems": [
            {
                "areaItemId": item_id,
                "level": lv,
            } for item_id, lv in levels.items()
        ]
    })

    return p

# 根据用户数据推荐挑战组卡歌曲
async def recommend_challenge_music(
    ctx: SekaiHandlerContext,
    profile: dict | None,
) -> tuple[int, str] | None:
    if not config.get('deck.challenge_music_auto_recommend.enabled'):
        return None
    if not profile or not profile.get('userMusicResults'):
        return None

    # 统计各难度各等级fc数量
    fc_count: dict[str, dict[int, int]] = {}
    for music in await get_valid_musics(ctx, leak=False):
        for diff in DIFF_COLORS:
            mid = music['id']
            level = (await get_music_diff_info(ctx, mid)).level.get(diff)
            if not level: 
                continue
            results = find_by(profile['userMusicResults'], "musicId", mid, mode='all') 
            results = find_by(results, 'musicDifficultyType', diff, mode='all') + find_by(results, 'musicDifficulty', diff, mode='all')
            if results:
                full_combo, all_prefect = False, False
                for item in results:
                    full_combo = full_combo or item["fullComboFlg"]
                    all_prefect = all_prefect or item["fullPerfectFlg"]
                if full_combo or all_prefect:
                    fc_count.setdefault(diff, {}).setdefault(level, 0)
                    fc_count[diff][level] += 1
    # 各等级后缀和
    for diff in DIFF_COLORS:
        if count := fc_count.get(diff):
            for level in range(40, 1):
                if level + 1 not in count:
                    continue
                count.setdefault(level, 0)
                count[level] += count[level + 1]
    # 根据规则进行推荐
    for rule in config.get('deck.challenge_music_auto_recommend.rules'):
        mid, diff = rule['music']
        if not await is_valid_music(ctx, mid, leak=False, diff=diff):
            continue
        ok = True
        for req_key, req_count in rule['fc_requires'].items():
            req_diff, req_level = req_key.split('_')
            if fc_count.get(req_diff, {}).get(int(req_level), 0) < req_count:
                ok = False
                break
        if ok:
            return (mid, diff)
    return None


# 合成自动组卡图片
async def compose_deck_recommend_image(
    ctx: SekaiHandlerContext, 
    qid: int,
    options: DeckRecommendOptions,
    last_args: str,
    additional: dict,
) -> Image.Image:
    # ---------------------------- 判断组卡类型方便后续处理 ---------------------------- #

    NO_MUSIC_TYPES = ["bonus", "wl_bonus", "mysekai"]

    is_wl = options.world_bloom_character_id or options.event_id == 180

    if options.live_type == "mysekai":
        recommend_type = "mysekai"
    elif options.target == "bonus":
        if is_wl:
            recommend_type = "wl_bonus"
        else:
            recommend_type = "bonus"
    elif options.live_type in ["challenge", "challenge_auto"]:
        if options.challenge_live_character_id:
            recommend_type = "challenge"
        else:
            recommend_type = "challenge_all"
    elif options.world_bloom_event_turn:
        recommend_type = "wl_fake"
    elif options.event_id:
        if is_wl:
            recommend_type = "wl"
        else:
            recommend_type = "event"
    else:
        if options.event_unit:
            recommend_type = "unit_attr"
        else:
            recommend_type = "no_event"

    # ---------------------------- 处理额外参数 ---------------------------- #
            
    # 是否是顶配租卡
    use_max_profile = additional.get('max_profile', False)
    use_sub_max_profile = additional.get('sub_max_profile', False)
    if use_max_profile:
        profile = await construct_max_profile(ctx)
        uid = None
    elif use_sub_max_profile:
        profile = await construct_max_profile(ctx, max_area_item_level=15)
        uid = None
    else:
        # 用户信息
        with ProfileTimer("deckrec.get_detailed_profile"):
            profile, pmsg = await get_detailed_profile(
                ctx, 
                qid, 
                filter=get_detailed_profile_card_filter(
                    'userGamedata',
                    'userDecks',
                    'userCards',
                    'userHonors',
                    "userMysekaiCanvases",
                    "userCharacters",
                    "userMysekaiGates",
                    "userMysekaiFixtureGameCharacterPerformanceBonuses",
                    "userAreas", 
                    'userChallengeLiveSoloDecks',
                    'userChallengeLiveSoloHighScoreRewards',
                    'userChallengeLiveSoloStages',
                    'userChallengeLiveSoloResults',
                    'userMusicResults',
                ),
                strict=False,
                raise_exc=True, ignore_hide=True)
            uid = profile['userGamedata']['userId']

    original_usercards = profile['userCards']
    # 组合卡牌过滤
    unit_filter = additional.get('unit_filter', None)
    if unit_filter:
        profile['userCards'] = [
            uc for uc in profile['userCards']
            if await get_unit_by_card_id(ctx, uc['cardId'], return_support=(unit_filter != 'piapro')) == unit_filter
        ]
    # 属性卡牌过滤
    attr_filter = additional.get('attr_filter', None)
    if attr_filter:
        profile['userCards'] = [
            uc for uc in profile['userCards']
            if (await ctx.md.cards.find_by_id(uc['cardId']))['attr'] == attr_filter
        ]
    # 排除卡牌
    excluded_cards = additional.get('excluded_cards', [])
    if excluded_cards:
        profile['userCards'] = [
            uc for uc in profile['userCards']
            if uc['cardId'] not in excluded_cards
        ]

    # 使用当前队伍
    use_current_deck = additional.get('use_current_deck', False)
    if use_current_deck:
        assert_and_reply(recommend_type != 'challenge_all', "需要指定挑战组卡角色才能使用\"当前\"参数")
        if recommend_type == 'challenge':
            deck = find_by(profile.get('userChallengeLiveSoloDecks', []), "characterId", options.challenge_live_character_id)
            assert_and_reply(deck, "找不到你的该角色的当前挑战卡组（更新当前挑战卡组需要抓包）")
            cards = []
            if deck.get('leader'): cards.append(deck['leader'])
            if deck.get('support1'): cards.append(deck['support1'])
            if deck.get('support2'): cards.append(deck['support2'])
            if deck.get('support3'): cards.append(deck['support3'])
            if deck.get('support4'): cards.append(deck['support4'])
            if len(cards)!= 5:
                raise ReplyException("你的该角色的当前挑战卡组不足5张，无法使用\"当前\"参数（更新当前挑战卡组需要抓包）")
            options.fixed_cards = cards
            options.fixed_characters = None
            options.best_skill_as_leader = False
        else:
            basic_profile = await get_basic_profile(
                ctx, get_player_bind_id(ctx), 
                use_cache=False, use_remote_cache=False,
            )
            options.fixed_cards = [basic_profile['userDeck'][f'member{i}'] for i in range(1, 6)]
            options.fixed_characters = None
            options.best_skill_as_leader = False
            # 转移basic_profile中的卡到profile中
            for bp_card in basic_profile['userCards']:
                if p_card := find_by(profile['userCards'], 'cardId', bp_card['cardId']):
                    p_card.update(bp_card)
                else:
                    # suite中没有该卡，提示需要抓包更新
                    raise ReplyException(f"当前卡组中的卡牌 {bp_card['cardId']} 不在Suite数据中，请更新抓包数据")

    # 如果卡组完全固定则只需要跑一种算法，并删除profile中除固定以外的其他卡牌以减少开销
    is_deck_fixed = options.fixed_cards and len(options.fixed_cards) == 5 or use_current_deck
    if is_deck_fixed:
        options.algorithm = "dfs"
        profile['userCards'] = [
            uc for uc in profile['userCards']
            if uc['cardId'] in options.fixed_cards
        ]

    # 检查是否在未使用固定队伍情况下指定技能顺序
    if not is_deck_fixed:
        assert_and_reply(options.skill_order_choose_strategy != "specific", 
                         "仅在使用固定队伍（例如添加\"当前\"参数）时可指定特定技能顺序")

    # 歌曲比较相关
    music_compare = False
    if additional.get('music_compare'):
        options.best_skill_as_leader = False    # 固定位置
        music_compare = True
        music_diffs_to_compare: list[tuple[int, str, str]] = additional.get('music_diffs_to_compare', [])
        music_compare_show_num = len(music_diffs_to_compare) if music_diffs_to_compare else MUSIC_COMPARE_DEFAULT_MUSIC_NUM

        if not music_diffs_to_compare:
            # 必须至少固定歌曲或固定卡组，（非固定卡组的情况每首都组一遍开销太大）
            assert_and_reply(is_deck_fixed, f"""
如果不限定要比较的歌曲，则必须固定一个卡组！
1. 固定5个卡牌ID:
/指令 ... 歌曲比较 #1 2 3 4 5
2. 固定为你的主队配置(实时更新):
/指令 ... 歌曲比较 当前
3. 限定比较的歌曲（难度默认ma）:
/指令 ... 歌曲比较 龙 虾ex 群青apd
4. 限定比较的歌曲并固定卡组:
/指令 ... 歌曲比较 龙 虾ex #1 2 3 4 5
""".strip())
            # 没有指定比较的歌曲，则通过musicmeta计算出5张卡技能加分全100%的情况的前排候选歌曲，
            musicmetas_json = get_musicmetas_json(ctx.region)
            musicmetas = await musicmetas_json.get()
            music_values = []
            is_multi = options.live_type in ['multi', 'cheerful']
            is_auto = options.live_type in ['auto', 'challenge_auto']
            for item in musicmetas:
                music_id = item['music_id']
                diff = item['difficulty']
                if not await is_valid_music(ctx, music_id, False, diff):
                    continue
                value = item['base_score'] if not is_auto else item['base_score_auto']
                for i in range(6):
                    key = 'skill_score_solo'
                    if is_multi: key = 'skill_score_multi'
                    if is_auto: key = 'skill_score_auto'
                    value += item[key][i] * (1.8 if is_multi else 1.0)
                if is_multi:
                    value += item['fever_score'] * 0.5
                if recommend_type in ['event', 'wl', 'wl_fake', 'unit_attr'] and options.target == 'score':
                    value *= item['event_rate'] / 100.0
                music_values.append((value, music_id, diff))
            music_values = sorted(music_values, key=lambda x: x[0], reverse=True)[:MUSIC_COMPARE_CANDIDATE_MUSIC_NUM]
            for _, mid, diff in music_values:
                music_diffs_to_compare.append((mid, diff, ""))

    # 挑战组卡自动推荐歌曲
    use_recommended_challenge_music = False
    if options.live_type == "challenge" and additional.get('use_default_music'):
        if res := await recommend_challenge_music(ctx, profile):
            options.music_id, options.music_diff = res
            use_recommended_challenge_music = True

    # 体力加成
    boost = additional.get('boost', None)
    if options.live_type not in ('multi', 'auto', 'solo') or options.target != 'score':
        boost = None
    if boost is not None:
        boost_bonus = BOOST_BONUS_DICT.get(boost, 1)

    # 区域道具等级
    area_item_level = additional.get('area_item_level', None)
    if area_item_level is not None:
        levels = {}
        for item in await ctx.md.area_item_levels.get():
            item_id = item['areaItemId']
            lv = item['level']
            if lv > area_item_level:
                continue
            levels[item_id] = max(levels.get(item_id, 0), lv)
        # 检查区服还没有开放等级上限
        for item_id, lv in levels.items():
            if lv < area_item_level:
                raise ReplyException(f"{get_region_name(ctx.region)}区域道具等级最多为{lv}")
        # 已存在的区域道具等级覆盖
        for area in profile['userAreas']:
            for area_item in area['areaItems']:
                item_id = area_item['areaItemId']
                if item_id in levels:
                    area_item['level'] = max(area_item['level'], levels[item_id])
                    del levels[item_id]
        # 不存在的添加
        profile['userAreas'].append({
            "userAreaStatus": {},
            "areaItems": [
                {
                    "areaItemId": item_id,
                    "level": lv,
                } for item_id, lv in levels.items()
            ]
        })
        

    # ---------------------------- 调用组卡服务 ---------------------------- #

    options.region = ctx.region
    log_options(ctx, uid, options)

    # 准备用户数据
    user_data = dump_bytes_json(profile)  
    # 还原profile避免画头像问题
    profile['userCards'] = original_usercards

    # 准备批次组卡参数
    all_options = []
    if recommend_type == "challenge_all":
        # 挑战组卡没有指定角色情况下，每角色组1个最强
        assert_and_reply(not music_compare, f"挑战组卡必须指定一个角色才能进行歌曲比较")
        for cid in range(1, 26 + 1):
            options.challenge_live_character_id = cid
            options.limit = 1
            all_options.append(DeckRecommendOptions(options))
        options.challenge_live_character_id = None
    elif music_compare:
        # 歌曲比较
        options.limit = 1
        for mid, diff, _ in music_diffs_to_compare:
            options.music_id = mid
            options.music_diff = diff
            all_options.append(DeckRecommendOptions(options))
    else:
        # 正常组卡
        all_options = [options]

    # 调用组卡并合并批次结果
    cost_times, wait_times = {}, {}
    result_decks = []
    result_algs = []
    for res, algs, cost_and_wait_times in await do_deck_recommend_batch(ctx, all_options, user_data):
        result_decks.extend(res.decks)
        result_algs.extend(algs)
        for alg, (cost, wait) in cost_and_wait_times.items():
            cost_times.setdefault(alg, []).append(cost)
            wait_times.setdefault(alg, []).append(wait)
    for alg in cost_and_wait_times:
        cost_times[alg] = sum(cost_times[alg]) / len(cost_times[alg])
        wait_times[alg] = sum(wait_times[alg]) / len(wait_times[alg])

    # 歌曲比较模式还要额外进行排序
    if music_compare:
        result_music_decks = list(zip(music_diffs_to_compare, result_decks))
        result_music_decks.sort(key=lambda x: x[1].score, reverse=True)
        result_music_decks = result_music_decks[:music_compare_show_num]
        result_decks = [d for _, d in result_music_decks]
        music_diffs_to_compare = [md for md, _ in result_music_decks]

    # ---------------------------- 绘图数据获取 ---------------------------- #

    # jp_ctx = SekaiHandlerContext.from_region('jp')

    if not music_compare:
        # 获取一般情况音乐标题和封面
        if options.music_id == OMAKASE_MUSIC_ID:
            music_title = "おまかせ（所有歌曲平均）"
            music_cover = ctx.static_imgs.get('omakase.png')
        else:
            music = await ctx.md.musics.find_by_id(options.music_id)
            music_title = truncate(music['title'], 20)
            music_title += f" ({options.music_diff.upper()})"
            music_cover = await get_music_cover_thumb(ctx, options.music_id)

    # 获取活动banner和标题
    live_name = "协力"
    event_id = options.event_id
    if recommend_type in ["event", "wl", "bonus", "wl_bonus", "mysekai"] and event_id:
        event = await ctx.md.events.find_by_id(event_id)
        if event:
            event_banner = await get_event_banner_img(ctx, event)
            event_title = event['name']
            if event['eventType'] == 'cheerful_carnival':
                live_name = "5v5" 
        else:
            # 预填充终章的情况
            event_banner, event_title = None, ""

    # 团队属性组卡指定5v5
    if recommend_type == "unit_attr" and options.event_type == "cheerful_carnival":
        live_name = "5v5"
        
    # 获取挑战角色名字和头像
    chara_name = None
    if recommend_type == "challenge":
        chara = await ctx.md.game_characters.find_by_id(options.challenge_live_character_id)
        chara_name = chara.get('firstName', '') + chara.get('givenName', '')
        chara_icon = get_chara_icon_by_chara_id(chara['id'])

    # 获取WL角色名字和头像
    wl_chara_name = None
    if options.world_bloom_character_id:
        wl_chara = await ctx.md.game_characters.find_by_id(options.world_bloom_character_id)
        if wl_chara:
            wl_chara_name = wl_chara.get('firstName', '') + wl_chara.get('givenName', '')
            wl_chara_icon = get_chara_icon_by_chara_id(wl_chara['id'])
        else:
            wl_chara_name = ""
            wl_chara_icon = None

    # 获取指定团名和属性的icon和logo
    unit_logo, attr_icon = None, None
    if options.event_unit and options.event_attr:
        unit_logo = get_unit_logo(options.event_unit)
        attr_icon = get_attr_icon(options.event_attr)

    # 获取卡组卡牌缩略图
    draw_eventbonus = recommend_type in ["bonus", "wl_bonus"]
    async def _get_thumb(card, pcard):
        try: 
            custom_text = None
            if draw_eventbonus:
                bonus = pcard.get('eventBonus', 0)
                if abs(bonus - int(bonus)) < 0.01:
                    bonus = int(bonus)
                custom_text = f"+{bonus}%"
            return await get_card_full_thumbnail(ctx, card, pcard=pcard, custom_text=custom_text)
        except: 
            return UNKNOWN_IMG
    card_imgs, card_keys = [], []
    for deck in result_decks:
        for deckcard in deck.cards:
            card = await ctx.md.cards.find_by_id(deckcard.card_id)
            usercard = find_by(profile['userCards'], 'cardId', deckcard.card_id)
            pcard = {
                'cardId': deckcard.card_id,
                'defaultImage': deckcard.default_image,                                 # 默认图片跟随组卡结果
                'specialTrainingStatus': usercard.get('specialTrainingStatus', 'none') if usercard else 'none', # 稀有度图标绘制跟随原本卡组
                'level': deckcard.level,
                'masterRank': deckcard.master_rank,
                'eventBonus': deckcard.event_bonus_rate,
            }
            card_key = f"{deckcard.card_id}_{deckcard.default_image}"
            if card_key not in card_keys:
                card_keys.append(card_key)
                card_imgs.append(_get_thumb(card, pcard))
    card_imgs = await asyncio.gather(*card_imgs)
    card_imgs = { key : img for key, img in zip(card_keys, card_imgs) }

    # 获取挑战live额外分数信息
    challenge_score_dlt = []
    if recommend_type in ["challenge", "challenge_all"]:
        try: challenge_live_info = await get_user_challenge_live_info(ctx, profile)
        except: challenge_live_info = {}
        for deck in result_decks:
            card_id = deck.cards[0].card_id
            chara_id = (await ctx.md.cards.find_by_id(card_id))['characterId']
            _, high_score, _, _ = challenge_live_info.get(chara_id, (None, 0, None, None))
            challenge_score_dlt.append(deck.score - high_score)

    # ---------------------------- 绘图 ---------------------------- #
        
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16):
            if uid is not None:
                await get_detailed_profile_card(ctx, profile, pmsg)

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg()):
                # 标题
                with VSplit().set_content_align('lb').set_item_align('lb').set_sep(16).set_padding(16).set_bg(roundrect_bg()):
                    title = ""

                    if recommend_type == "mysekai":
                        if event_id:
                            title += f"烤森活动#{event_id}组卡"
                        else:
                            title += f"烤森模拟活动组卡"
                    elif recommend_type in ['challenge', 'challenge_all']: 
                        title += "每日挑战组卡"
                        if options.live_type == "challenge_auto":
                            title += "(AUTO)"
                    elif recommend_type in ['bonus', 'wl_bonus']:
                        if recommend_type == "bonus":
                            title += f"活动#{event_id}加成组卡"
                        elif recommend_type == "wl_bonus":
                            title += f"WL活动#{event_id}加成组卡"
                    else:
                        if recommend_type == "event":
                            title += f"活动#{event_id}组卡"
                        elif recommend_type == "wl":
                            if wl_chara_name:
                                title += f"WL活动#{event_id}组卡"
                            else:
                                title += f"WL终章活动组卡"
                        elif recommend_type == "wl_fake":
                            title += f"第{options.world_bloom_event_turn}轮WL模拟组卡"
                        elif recommend_type == "unit_attr":
                            title += f"团队+颜色模拟活动组卡"
                        elif recommend_type == "no_event":
                            title += f"无活动组卡"
                    
                        if options.live_type == "multi":
                            title += f"({live_name})"
                        elif options.live_type == "solo":
                            title += "(单人)"
                        elif options.live_type == "auto":
                            title += "(AUTO)"
                    
                    score_name = "PT"
                    if recommend_type in ["challenge", "challenge_all", "no_event"]:
                        score_name = "分数"  

                    with HSplit().set_content_align('l').set_item_align('l').set_sep(16):
                        if recommend_type in ["event", "wl", "bonus", "wl_bonus", "mysekai"] and options.event_id:
                            if event_banner:
                                ImageBox(event_banner, size=(None, 50))
                            else:
                                title = event_title + " " + title

                        TextBox(title, TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50)), use_real_line_count=True)

                        if recommend_type == "challenge":
                            ImageBox(chara_icon, size=(None, 50))
                            TextBox(f"{chara_name}", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(70, 70, 70)))
                        if wl_chara_name:
                            ImageBox(wl_chara_icon, size=(None, 50))
                            TextBox(f"{wl_chara_name} 章节", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(70, 70, 70)))
                        if unit_logo and attr_icon:
                            ImageBox(unit_logo, size=(None, 60))
                            ImageBox(attr_icon, size=(None, 50))
                        
                        if use_max_profile:
                            TextBox(f"({get_region_name(ctx.region)}顶配)", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50)))
                        if use_sub_max_profile:
                            TextBox(f"({get_region_name(ctx.region)}次顶配)", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50)))

                    if any([
                        unit_filter, attr_filter, 
                        excluded_cards, 
                        options.multi_live_score_up_lower_bound, 
                        options.keep_after_training_state,
                    ]):
                        with HSplit().set_content_align('l').set_item_align('l').set_sep(16):
                            setting_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
                            TextBox("卡组设置:", setting_style)
                            if unit_filter or attr_filter:
                                TextBox(f"仅", setting_style)
                                if unit_filter: ImageBox(get_unit_logo(unit_filter), size=(None, 40))
                                if attr_filter: ImageBox(get_attr_icon(attr_filter), size=(None, 35))
                                TextBox(f"上场", setting_style)
                            if excluded_cards:
                                TextBox(f"排除 {','.join(map(str, excluded_cards))}", setting_style)
                            if options.multi_live_score_up_lower_bound:
                                TextBox(f"实效≥{int(options.multi_live_score_up_lower_bound)}%", setting_style)
                            if options.keep_after_training_state:
                                TextBox(f"禁用双技能自动切换", setting_style)
                            
                    if recommend_type in ["bonus", "wl_bonus"]:
                        TextBox(f"该功能需要输入活动加成而不是要控的PT", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(255, 50, 50)))
                        TextBox(f"友情提醒：控分前请核对加成和体力设置", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(255, 50, 50)))
                        if recommend_type == "wl_bonus":
                            TextBox(f"WL仅支持自动组主队，支援队请自行配置", TextStyle(font=DEFAULT_FONT, size=24, color=(50, 50, 50)))
                    
                    if recommend_type not in NO_MUSIC_TYPES and not music_compare:
                        with HSplit().set_content_align('l').set_item_align('l').set_sep(16):
                            if last_args:
                                TextBox(f"{last_args} → ", TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(70, 70, 70)))
                            with Frame().set_size((50, 50)):
                                if options.music_id != OMAKASE_MUSIC_ID:
                                    Spacer(w=50, h=50).set_bg(FillBg(fill=DIFF_COLORS[options.music_diff])).set_offset((6, 6))
                                    ImageBox(music_cover, size=(50, 50))
                                else:
                                    ImageBox(music_cover, size=(50, 50), shadow=True)
                            TextBox(music_title, TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(70, 70, 70)))
                            if use_recommended_challenge_music:
                                TextBox(f"*根据游玩记录自动推荐", TextStyle(font=DEFAULT_FONT, size=20, color=(70, 70, 70)))

                    if recommend_type not in ["bonus", "wl_bonus", "mysekai"]:
                        if options.skill_order_choose_strategy == 'average':
                            skill_order_text = "技能顺序: ⚖️平均情况"
                        elif options.skill_order_choose_strategy == 'max':
                            skill_order_text = "技能顺序: 🌟最优顺序"
                        elif options.skill_order_choose_strategy == 'min':
                            skill_order_text = "技能顺序: 🥀最差顺序"
                        elif options.skill_order_choose_strategy == 'specific':
                            skill_order = options.specific_skill_order
                            skill_order_text = f"技能顺序: {''.join([str(s+1) for s in skill_order])}"

                        if options.skill_reference_choose_strategy == 'average':
                            skill_reference_text = "BloomFes花前技能吸取: ⚖️平均值"
                        elif options.skill_reference_choose_strategy == 'max':
                            skill_reference_text = "BloomFes花前技能吸取: 🌟最大值"
                        elif options.skill_reference_choose_strategy == 'min':
                            skill_reference_text = "BloomFes花前技能吸取: 🥀最小值"

                        TextBox(skill_order_text + "  " + skill_reference_text, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)))
                    
                    info_text = ""

                    if last_args:
                        arg_unit, args = extract_unit(last_args)
                        arg_attr, args = extract_card_attr(last_args)
                        arg_nickname, args = extract_nickname_from_args(last_args)
                        if arg_unit or arg_attr:
                            info_text += "检测到你的歌曲查询中包含团名或颜色，可能是参数格式不正确\n"
                            info_text += "如果你想指定仅包含某个团名或颜色的卡牌请用: 纯mmj 纯绿\n"
                            info_text += "如果你想组某个团名颜色加成的模拟活动请使用“/组卡”\n"
                        if arg_nickname and not args.strip().isdigit():
                            info_text += "检测到你的歌曲查询中包含角色昵称，可能是参数格式不正确\n"
                            info_text += "如果你想指定固定角色请用: #角色1 角色2...\n"

                    if use_max_profile:
                        info_text += "\"顶配\"为该服截止当前的全卡满养成配置(并非基于你的卡组计算)\n"
                    if use_sub_max_profile:
                        info_text += "\"次顶配\"为该服截止当前的全卡满养成道具15级配置(并非基于你的卡组计算)\n"
                    if use_current_deck:
                        info_text += "活动组卡的“当前”队伍无需抓包更新，挑战组卡则需要抓包更新\n"
                    if area_item_level:
                        info_text += f"所有区域道具等级已提升为至少{area_item_level}级\n"

                    if info_text:  
                        TextBox(info_text.strip(), TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(200, 75, 75)), use_real_line_count=True)

                # 表格
                gh, vsp, voffset = 120, 12, 18
                with VSplit().set_content_align('c').set_item_align('c').set_sep(16).set_padding(16).set_bg(roundrect_bg()):
                    if len(result_decks) > 0:
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(16).set_padding(0):
                            th_style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(0, 0, 0))
                            th_style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(75, 75, 75))
                            th_main_sign = '∇'
                            tb_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))

                            # 歌曲比较添加额外歌曲列
                            if music_compare:
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(vsp).set_padding(8):
                                    TextBox("歌曲", th_style2).set_h(gh // 2).set_content_align('c')
                                    Spacer(h=6)
                                    for (mid, diff, marg), deck in zip(music_diffs_to_compare, result_decks):
                                        music = await ctx.md.musics.find_by_id(mid)
                                        music_title = music['title'] + f" ({diff.upper()})"
                                        with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(0).set_h(gh):
                                            with Frame().set_content_align('c'):
                                                Spacer(w=64, h=64).set_bg(FillBg(fill=DIFF_COLORS[diff])).set_offset((3, 3))
                                                ImageBox(await get_music_cover_thumb(ctx, mid), size=(64, 64)).set_offset((-3, -3))
                                            text = f"{mid}"
                                            if marg: text = f"{truncate(marg, 8)} → " + text
                                            TextBox(text, TextStyle(font=DEFAULT_FONT, size=15, color=(75, 75, 75)))

                            # 分数
                            if recommend_type not in ["bonus", "wl_bonus"]:
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(vsp).set_padding(8):
                                    target_score = options.target == "score"
                                    text = score_name + th_main_sign if target_score else score_name
                                    style = th_style1 if target_score else th_style2
                                    with Frame().set_h(gh // 2).set_content_align('c'):
                                        TextBox(text, style)
                                        if boost is not None:
                                            TextBox(f"{boost}🔥(x{boost_bonus})", TextStyle(font=DEFAULT_FONT, size=18, color=(75, 75, 75))) \
                                                .set_content_align('c').set_offset((0, 28))
                                    Spacer(h=6)
                                    for i, (deck, alg) in enumerate(zip(result_decks, result_algs)):
                                        with Frame().set_content_align('rb'):
                                            alg_offset = 0
                                            # 挑战分数差距
                                            if recommend_type in ['challenge', 'challenge_all']: 
                                                alg_offset = 20
                                                dlt = challenge_score_dlt[i]
                                                color = (50, 150, 50) if dlt > 0 else (150, 50, 50)
                                                TextBox(f"{dlt:+d}", TextStyle(font=DEFAULT_FONT, size=15, color=color)).set_offset((0, -8-voffset*2))
                                            # 算法
                                            TextBox(alg.upper(), TextStyle(font=DEFAULT_FONT, size=12, color=(125, 125, 125))).set_offset((0, -8-voffset*2+alg_offset))
                                            # 分数
                                            score = deck.score
                                            if recommend_type == "no_event":
                                                score = deck.live_score 
                                            elif recommend_type == "mysekai":
                                                score = deck.mysekai_event_point
                                            if boost is not None:
                                                score = int(score * boost_bonus)
                                            with Frame().set_content_align('c'):
                                                TextBox(str(score), tb_style).set_h(gh).set_content_align('c').set_offset((0, -voffset))

                            # 卡片
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(vsp).set_padding(8):
                                TextBox("卡组", th_style2).set_h(gh // 2).set_content_align('c')
                                Spacer(h=6)
                                for deck in result_decks:
                                    with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(0):
                                        for card in deck.cards:
                                            card_id = card.card_id
                                            character_id = (await ctx.md.cards.find_by_id(card_id))['characterId']
                                            event_bonus = card.event_bonus_rate
                                            ep1_read, ep2_read = card.episode1_read, card.episode2_read
                                            slv, sup = card.skill_level, int(card.skill_score_up)

                                            with VSplit().set_content_align('c').set_item_align('c').set_sep(4).set_padding(0).set_h(gh):
                                                with Frame().set_content_align('rt'):
                                                    card_key = f"{card_id}_{card.default_image}"
                                                    ImageBox(card_imgs[card_key], size=(None, 80))
                                                    if options.fixed_cards and card_id in options.fixed_cards \
                                                    or options.fixed_characters and character_id in options.fixed_characters:
                                                        TextBox(str(card_id), TextStyle(font=DEFAULT_FONT, size=10, color=WHITE)) \
                                                            .set_bg(RoundRectBg((200, 50, 50, 200), 2)).set_offset((-2, 0)).set_text_offset((0, -2))
                                                    else:
                                                        TextBox(str(card_id), TextStyle(font=DEFAULT_FONT, size=10, color=(75, 75, 75))) \
                                                            .set_bg(RoundRectBg((255, 255, 255, 200), 2)).set_offset((-2, 0)).set_text_offset((0, -2))
                                                    if card.has_canvas_bonus:
                                                        ImageBox(ctx.static_imgs.get(f"mysekai/icon_canvas.png"), size=(11, 11)) \
                                                                .set_offset((-32, 65))

                                                info_bg = RoundRectBg((255, 255, 255, 150), 2)
                                                with HSplit().set_content_align('c').set_item_align('c').set_sep(3).set_padding(0):
                                                    TextBox(f"SLv.{slv}", TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50))).set_bg(info_bg)
                                                    TextBox(f"↑{sup}%", TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50))).set_bg(info_bg)
                                                
                                                with HSplit().set_content_align('c').set_item_align('c').set_sep(3).set_padding(0):
                                                    show_event_bonus = event_bonus > 0
                                                    if show_event_bonus:
                                                        event_bonus_str = f"+{event_bonus:.1f}%" if int(event_bonus) != event_bonus else f"+{int(event_bonus)}%"
                                                        TextBox(event_bonus_str, TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50))).set_bg(info_bg)
                                                    read_fg, read_bg = (50, 150, 50, 255), (255, 255, 255, 255)
                                                    noread_fg, noread_bg = (150, 50, 50, 255), (255, 255, 255, 255)
                                                    none_fg, none_bg = (255, 255, 255, 255), (255, 255, 255, 255)
                                                    ep1_fg = none_fg if ep1_read is None else (read_fg if ep1_read else noread_fg)
                                                    ep1_bg = none_bg if ep1_read is None else (read_bg if ep1_read else noread_bg)
                                                    ep2_fg = none_fg if ep2_read is None else (read_fg if ep2_read else noread_fg)
                                                    ep2_bg = none_bg if ep2_read is None else (read_bg if ep2_read else noread_bg)
                                                    TextBox("前" if show_event_bonus else "前篇", TextStyle(font=DEFAULT_FONT, size=12, color=ep1_fg)).set_bg(info_bg)
                                                    TextBox("后" if show_event_bonus else "后篇", TextStyle(font=DEFAULT_FONT, size=12, color=ep2_fg)).set_bg(info_bg)

                            # 加成
                            if recommend_type not in ["challenge", "challenge_all", "no_event"]:
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(vsp).set_padding(8):
                                    TextBox("加成", th_style2).set_h(gh // 2).set_content_align('c')
                                    Spacer(h=6)
                                    for deck in result_decks:
                                        if is_wl:
                                            bonus = f"{deck.event_bonus_rate:.1f}+{deck.support_deck_bonus_rate:.1f}%"
                                            total = f"{deck.event_bonus_rate+deck.support_deck_bonus_rate:.1f}%"
                                        else:
                                            bonus = None
                                            total = f"{deck.event_bonus_rate:.1f}%"
                                        with Frame().set_content_align('rb'):
                                            if bonus is not None:
                                                TextBox(bonus, TextStyle(font=DEFAULT_FONT, size=14, color=(150, 150, 150))).set_offset((0, -6-voffset*2))
                                            with Frame().set_content_align('c'):
                                                TextBox(total, tb_style).set_h(gh).set_content_align('c').set_offset((0, -voffset))

                            # 实效
                            if options.live_type in ['multi', 'cheerful']:
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(vsp).set_padding(8):
                                    target_skill = options.target == "skill"
                                    text = "实效" + th_main_sign if target_skill else "实效"
                                    style = th_style1 if target_skill else th_style2
                                    TextBox(text, style).set_h(gh // 2).set_content_align('c')
                                    Spacer(h=6)
                                    for deck in result_decks:
                                        with Frame().set_content_align('rb'):
                                            if options.multi_live_teammate_score_up is not None:
                                                teammate_text = f"队友 {int(options.multi_live_teammate_score_up)}"
                                                TextBox(teammate_text, TextStyle(font=DEFAULT_FONT, size=14, color=(125, 125, 125))).set_offset((0, -8-voffset*2))
                                            with Frame().set_content_align('c'):
                                                TextBox(f"{deck.multi_live_score_up:.1f}%", tb_style).set_h(gh).set_content_align('c').set_offset((0, -voffset))

                            # 综合力和算法
                            if recommend_type not in ["bonus", "wl_bonus"]:
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(vsp).set_padding(8):
                                    target_power = options.target == "power"
                                    text = "综合力" + th_main_sign if target_power else "综合力"
                                    style = th_style1 if target_power else th_style2
                                    TextBox(text, style).set_h(gh // 2).set_content_align('c')
                                    Spacer(h=6)
                                    for deck in result_decks:
                                        with Frame().set_content_align('rb'):
                                            if options.multi_live_teammate_power is not None:
                                                teammate_text = f"队友 {int(options.multi_live_teammate_power)}"
                                                TextBox(teammate_text, TextStyle(font=DEFAULT_FONT, size=14, color=(125, 125, 125))).set_offset((0, -8-voffset*2))
                                            with Frame().set_content_align('c'):
                                                TextBox(str(deck.total_power), tb_style).set_h(gh).set_content_align('c').set_offset((0, -voffset))
                    # 找不到结果
                    else:
                        TextBox("未找到符合条件的卡组", TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(255, 50, 50)))

                # 说明
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                    tip_style = TextStyle(font=DEFAULT_FONT, size=16, color=(20, 20, 20))
                    TextBox(f"功能移植并修改自33Kit https://3-3.dev/sekai/deck-recommend 算错概不负责", tip_style)
                    def tt(t: float) -> str:
                        return f"{t*1000:.0f}ms" if t < 1 else f"{t:.2f}s"
                    alg_and_cost_text = "本次组卡使用算法: "
                    for alg in cost_times:
                        alg_and_cost_text += f"{alg.upper()}-{RECOMMEND_ALG_NAMES[alg]} (等待{tt(wait_times[alg])}/耗时{tt(cost_times[alg])}) + "
                    alg_and_cost_text = alg_and_cost_text[:-3]
                    TextBox(alg_and_cost_text, tip_style)
                    TextBox(f"若发现组卡漏掉最优解可指定固定卡牌再尝试，发送\"{ctx.original_trigger_cmd}help\"获取详细帮助", tip_style)

    add_watermark(canvas)

    with ProfileTimer("deckrec.draw"):
        img = await canvas.get_img()

    return img


# ======================= 指令处理 ======================= #

# 活动组卡
pjsk_event_deck = SekaiCmdHandler([
    "/pjsk event card", "/pjsk event deck", "/pjsk deck", 
    "/活动组卡", "/活动组队", "/活动卡组", "/活动配队",
    "/组卡", "/组队", "/配队", 
    "/指定属性组卡", "/指定属性组队", "/指定属性卡组", "/指定属性配队",
    "/模拟组卡", "/模拟配队", "/模拟组队", "/模拟卡组",
])
pjsk_event_deck.check_cdrate(cd).check_wblist(gbl)
@pjsk_event_deck.handle()
async def _(ctx: SekaiHandlerContext):
    with ProfileTimer("deckrec.total"):
        await ctx.asend_reply_msg(await get_image_cq(
            await compose_deck_recommend_image(
                ctx, ctx.user_id, 
                **(await extract_event_options(ctx, ctx.get_args()))
            ),
            low_quality=True,
        ))


# 挑战组卡
pjsk_challenge_deck = SekaiCmdHandler([
    "/pjsk challenge card", "/pjsk challenge deck",
    "/挑战组卡", "/挑战组队", "/挑战卡组", "/挑战配队",
])
pjsk_challenge_deck.check_cdrate(cd).check_wblist(gbl)
@pjsk_challenge_deck.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_deck_recommend_image(
            ctx, ctx.user_id,
            **(await extract_challenge_options(ctx, ctx.get_args()))
        ),
        low_quality=True,
    ))


# 长草组卡
pjsk_no_event_deck = SekaiCmdHandler([
    "/pjsk no event deck", "/pjsk best deck",
    "/长草组卡", "/长草组队", "/长草卡组", "/长草配队", 
    "/最强卡组", "/最强组卡", "/最强组队", "/最强配队",
])
pjsk_no_event_deck.check_cdrate(cd).check_wblist(gbl)
@pjsk_no_event_deck.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_deck_recommend_image(
            ctx, ctx.user_id,
            **(await extract_no_event_options(ctx, ctx.get_args()))
        ),
        low_quality=True,
    ))


# 加成组卡
pjsk_bonus_deck = SekaiCmdHandler([
    "/pjsk bonus deck", "/pjsk bonus card",
    "/加成组卡", "/加成组队", "/加成卡组", "/加成配队",
    "/控分组卡", "/控分组队", "/控分卡组", "/控分配队",
])
pjsk_bonus_deck.check_cdrate(cd).check_wblist(gbl)
@pjsk_bonus_deck.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_deck_recommend_image(
            ctx, ctx.user_id,
            **(await extract_bonus_options(ctx, ctx.get_args()))
        ),
        low_quality=True,
    ))


# 烤森组卡
mysekai_deck = SekaiCmdHandler([
    "/mysekai deck", "/pjsk mysekai deck",
    "/烤森组卡", "/烤森组队", "/烤森卡组", "/烤森配队",
    "/ms组卡", "/ms组队", "/ms卡组", "/ms配队",
])
mysekai_deck.check_cdrate(cd).check_wblist(gbl)
@mysekai_deck.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_deck_recommend_image(
            ctx, ctx.user_id,
            **(await extract_mysekai_options(ctx, ctx.get_args()))
        ),
        low_quality=True,
    ))


# 实效计算
pjsk_score_up = CmdHandler([
    "/实效", "/倍率", "/时效", "/pjsk score up",
], logger)
pjsk_score_up.check_cdrate(cd).check_wblist(gbl)
@pjsk_score_up.handle()
async def _(ctx: SekaiHandlerContext):
    try:
        args = ctx.get_args().strip().split()
        values = list(map(float, args))
        assert len(values) == 5
    except:
        raise ReplyException(f"使用方式: {ctx.trigger_cmd} 100 100 100 100 100") 
    res = values[0] + (values[1] + values[2] + values[3] + values[4]) / 5.
    return await ctx.asend_reply_msg(f"实效: {res:.1f}%")



# ======================= 定时任务 ======================= #

DECKREC_DATA_UPDATE_INTERVAL_CFG = config.item('deck.data_update_interval_seconds')

@repeat_with_interval(DECKREC_DATA_UPDATE_INTERVAL_CFG, "组卡数据更新", logger)
async def deckrec_update_data():
    for region in ALL_SERVER_REGIONS:
        try:
            ctx = SekaiHandlerContext.from_region(region)
            current_masterdata_version = await ctx.md.get_version()
            musicmetas_json = get_musicmetas_json(ctx.region)
            current_musicmetas_update_ts = await musicmetas_json.get_update_time()
            logger.debug(f"组卡 {region} 当前 masterdata 版本: {current_masterdata_version} musicmetas 更新时间: {current_musicmetas_update_ts}")

            async def construct_payload(with_masterdata: bool, with_musicmetas: bool) -> bytes:
                payloads = []

                data = { 
                    'region': ctx.region,
                    'masterdata_version': str(current_masterdata_version),
                    'musicmetas_update_ts': int(current_musicmetas_update_ts.timestamp()),
                }
                add_payload_segment(payloads, dumps_json(data, indent=False).encode('utf-8'))

                if with_masterdata:
                    logger.info(f"为自动组卡加载 {ctx.region} masterdata")
                    mds = [
                        ctx.md.area_item_levels.get_path(),
                        ctx.md.area_items.get_path(),
                        ctx.md.areas.get_path(),
                        ctx.md.card_episodes.get_path(),
                        ctx.md.cards.get_path(),
                        ctx.md.card_rarities.get_path(),
                        ctx.md.character_ranks.get_path(),
                        ctx.md.event_cards.get_path(),
                        ctx.md.event_deck_bonuses.get_path(),
                        ctx.md.event_exchange_summaries.get_path(),
                        ctx.md.events.get_path(),
                        ctx.md.event_items.get_path(),
                        ctx.md.event_rarity_bonus_rates.get_path(),
                        ctx.md.game_characters.get_path(),
                        ctx.md.game_character_units.get_path(),
                        ctx.md.honors.get_path(),
                        ctx.md.master_lessons.get_path(),
                        ctx.md.music_diffs.get_path(),
                        ctx.md.musics.get_path(),
                        ctx.md.music_vocals.get_path(),
                        ctx.md.shop_items.get_path(),
                        ctx.md.skills.get_path(),
                        ctx.md.world_bloom_different_attribute_bonuses.get_path(),
                        ctx.md.world_blooms.get_path(),
                        ctx.md.world_bloom_support_deck_bonuses.get_path(),
                    ]
                    if ctx.region in MYSEKAI_REGIONS:
                        mds += [
                            ctx.md.card_mysekai_canvas_bonuses.get_path(),
                            ctx.md.mysekai_fixture_game_character_groups.get_path(),
                            ctx.md.mysekai_fixture_game_character_group_performance_bonuses.get_path(),
                            ctx.md.mysekai_gates.get_path(),
                            ctx.md.mysekai_gate_levels.get_path(),
                        ]
                    if await ctx.md.events.find_by_id(180):
                        mds.append(ctx.md.world_bloom_support_deck_unit_event_limited_bonuses.get_path())
                    for path in await asyncio.gather(*mds):
                        with open(path, 'rb') as f:
                            add_payload_segment(payloads, os.path.basename(path).encode('utf-8'))
                            add_payload_segment(payloads, f.read())

                if with_musicmetas:
                    logger.info(f"为自动组卡加载 {ctx.region} musicmetas")
                    musicmetas = await musicmetas_json.get()
                    musicmetas = add_omakase_music(musicmetas)
                    add_payload_segment(payloads, b'musicmetas')
                    add_payload_segment(payloads, dumps_json(musicmetas, indent=False).encode('utf-8'))
                
                return build_multiparts_payload(payloads)

            async def req(url :str, with_masterdata: bool, with_musicmetas: bool):
                async with get_client_session().post(url + "/update_data", data=await construct_payload(with_masterdata, with_musicmetas)) as resp:
                    if not (with_masterdata or with_musicmetas) and resp.status == 426:
                        data = await resp.json()
                        missing_data = data.get('detail', {}).get('missing_data', [])
                        if not missing_data:
                            logger.warning(f"{region} 组卡数据需要更新但未指明具体内容")
                            return
                        logger.info(f"{region} 组卡数据需要更新: {missing_data}")
                        await req(
                            url,
                            with_masterdata = 'masterdata' in missing_data,
                            with_musicmetas = 'musicmetas' in missing_data,
                        )
                        logger.info(f"{region} 组卡数据更新完成")
                        return
                    elif resp.status != 200:
                        msg = f"更新 {url} 组卡数据失败 ({resp.status}): "
                        try:
                            err_data = await resp.json()
                            msg += err_data.get('detail', '')
                        except:
                            try:
                                msg += await resp.text()
                            except:
                                pass
                        raise Exception(msg)

            for server in RECOMMEND_SERVERS_CFG.get():
                await req(server['url'], False, False)

        except Exception as e:
            logger.warning(f"更新组卡数据失败 ({region}): {get_exc_desc(e)}")