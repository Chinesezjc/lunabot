from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..suite import Suite
from sekai_deck_recommend_cpp import (
    DeckRecommendOptions,
    DeckRecommendSaOptions,
)
from .deck import (
    BOOST_BONUS_DICT,
    OMAKASE_MUSIC_ID,
    do_deck_recommend_batch,
    extract_addtional_options,
    extract_card_config,
    extract_fixed_cards_and_characters,
    extract_multilive_options,
    construct_max_profile,
)
from .event import get_current_event, get_event_banner_img
from .sk import get_latest_ranking
from .music import search_music, MusicSearchOptions, get_music_cover_thumb
from .card import get_unit_by_card_id
from .profile import (
    get_player_bind_id,
    get_basic_profile,
    get_detailed_profile,
    get_detailed_profile_card,
    get_detailed_profile_card_filter,
    get_user_data_mode,
)


# ======================= 常量 ======================= #

PLANNER_HELP = """活动规划用法:
/活动规划 pt1000w
/活动规划 pt1000w 当前pt120w 歌 虾ex 龙hd
/jp活动规划 t100 event202 歌 野车 10火
/cn活动规划 pt1200w #123 456 789 101 112 队友综合25w 队友实效200

不写区服时使用默认绑定区服，也可以加 jp/cn 前缀指定。
参数: pt/目标, t排名, 当前pt, 总榜, 1-10火, 歌曲/难度, 野车, event活动ID,
      #固定卡/角色, 当前/顶配/画布/已读/队友综合/队友实效等活动组卡参数。
WL活动当前按活动总榜规划（暂不支持章节单榜角色选择）。
不写歌曲时默认算虾 EXPERT、龙 HARD 和 野车；不写火数时默认算 5火 和 10火；不写卡组时默认使用最优卡组。"""

LOST_AND_FOUND_MUSIC_ID = 226  # 「龙」

BOOST_MULTIPLIERS = BOOST_BONUS_DICT  # {0:1, 1:5, ..., 10:35}

RE_TARGET_RANK = re.compile(r"(?:^|\s)t\s*([0-9]+)|([0-9]+)\s*名", re.I)
RE_TARGET_POINT = re.compile(r"(?:目标pt|目标|pt|打到)\s*([0-9][0-9,._]*(?:万|億|亿|w|k)?)", re.I)
RE_CURRENT_POINT = re.compile(r"(?:当前pt|已有pt|已打|现在pt)\s*([0-9][0-9,._]*(?:万|億|亿|w|k)?)", re.I)
RE_BOOST = re.compile(r"([0-9]{1,2})\s*火")
RE_TOTAL_RANKING = re.compile(r"(?:总榜|總榜|total|overall)", re.I)
RE_EVENT_ID = re.compile(r"(?:活动|event)\s*(\d+)")

HUMAN_NUMBER_SUFFIXES = [
    ("亿", 100000000), ("億", 100000000), ("万", 10000), ("w", 10000), ("k", 1000),
]

DIFFICULTY_SUFFIXES = [
    ("append", "append"), ("apd", "append"), ("app", "append"),
    ("master", "master"), ("mas", "master"), ("ma", "master"),
    ("expert", "expert"), ("exp", "expert"), ("ex", "expert"),
    ("hard", "hard"), ("hd", "hard"),
    ("normal", "normal"), ("nm", "normal"),
    ("easy", "easy"), ("ez", "easy"),
]

SONG_TOKEN_EXCLUDE_KEYWORDS = (
    "当前", "最优", "最佳", "目标", "已有", "现在", "卡组", "主队",
    "队友", "实效", "综合", "画布", "已读", "满", "次顶配", "顶配",
)

DEFAULT_SONGS = [
    {"query": "虾", "difficulty": "expert", "music_id": 0},
    {"query": "龙", "difficulty": "hard", "music_id": LOST_AND_FOUND_MUSIC_ID},
    {"query": "野车", "difficulty": "master", "music_id": OMAKASE_MUSIC_ID},
]

# ======================= 参数解析 ======================= #

@dataclass
class PlannerSongSelection:
    query: str
    difficulty: str = ""
    music_id: int = 0


@dataclass
class PlannerParams:
    target_point: int = 0
    target_rank: int = 0
    current_point: int = 0
    current_point_set: bool = False
    total_ranking: bool = False
    boosts: List[int] = field(default_factory=lambda: [5, 10])
    songs: List[PlannerSongSelection] = field(default_factory=list)
    event_id: int = 0
    deck_args: str = ""
    deck_additional: dict = field(default_factory=dict)


def parse_human_number(raw: str) -> int | None:
    s = str(raw or "").strip().lower()
    s = s.replace(",", "").replace("_", "").replace("，", "").replace(" ", "")
    if not s:
        return None
    multiplier = 1.0
    for suffix, mul in HUMAN_NUMBER_SUFFIXES:
        if s.endswith(suffix):
            multiplier = mul
            s = s[: -len(suffix)]
            break
    try:
        value = float(s)
    except ValueError:
        return None
    if value < 0:
        return None
    return int(value * multiplier + 0.5)


def _parse_point_with_re(regex: "re.Pattern", text: str) -> Tuple[int, bool]:
    match = regex.search(text)
    if not match or not match.group(1):
        return 0, False
    value = parse_human_number(match.group(1))
    return value, value is not None


def parse_bare_target_point(args: str) -> Tuple[int, str]:
    """
    扫描裸数字目标pt（>=10000才生效），遇到#开头的token后全部保留
    """
    fields = str(args).split()
    remaining = []
    target = 0
    in_fixed_targets = False
    for token in fields:
        clean = token.strip(" ,，。")
        if not clean:
            remaining.append(token)
            continue
        if clean.startswith("#"):
            in_fixed_targets = True
        if in_fixed_targets:
            remaining.append(token)
            continue
        lower = clean.lower()
        if lower.startswith("t") or "火" in lower or lower.startswith("event") or lower.startswith("活动"):
            remaining.append(token)
            continue
        value = parse_human_number(clean)
        if value is not None and value >= 10000 and target == 0:
            target = value
            continue
        remaining.append(token)
    return target, " ".join(remaining).strip()


def parse_boosts(args: str, fallback: List[int]) -> Tuple[List[int], str]:
    matches = RE_BOOST.findall(args)
    if not matches:
        return list(fallback), RE_BOOST.sub(" ", args)
    boosts = []
    seen = set()
    for raw in matches:
        boost = int(raw)
        if boost < 1 or boost > 10:
            raise ReplyException("火数只能指定 1-10 火")
        if boost not in seen:
            seen.add(boost)
            boosts.append(boost)
    return boosts, RE_BOOST.sub(" ", args)


def split_difficulty(token: str) -> Tuple[str, str]:
    lower = token.lower()
    for suffix, difficulty in DIFFICULTY_SUFFIXES:
        if len(lower) > len(suffix) and lower.endswith(suffix):
            return token[: -len(suffix)], difficulty
    return token, ""


def _looks_like_song_token(token: str) -> bool:
    clean = token.strip()
    if not clean or clean.startswith("#"):
        return False
    lower = clean.lower()
    if "火" in lower or lower.startswith("event") or lower.startswith("活动") or lower.startswith("pt"):
        return False
    if lower in ("solo", "单人", "auto", "自动", "multi", "多人", "协力"):
        return False
    if re.match(r"^t\d+$", lower):
        return False
    if any(kw in lower for kw in SONG_TOKEN_EXCLUDE_KEYWORDS):
        return False
    if parse_human_number(clean) is not None and parse_human_number(clean) > 0:
        return False
    return True


def parse_songs(args: str) -> Tuple[List[PlannerSongSelection], str]:
    """
    解析 歌/歌曲/曲 标记之后的歌曲token，返回 (歌曲列表, 剩余args)
    """
    marker_idx = -1
    marker_len = 0
    for marker in ("歌曲", "歌", "曲"):
        idx = args.find(marker)
        if idx != -1 and (marker_idx == -1 or idx < marker_idx):
            marker_idx = idx
            marker_len = len(marker)

    if marker_idx == -1:
        return [], args

    prefix = args[:marker_idx].strip()
    after = args[marker_idx + marker_len:].strip()

    # 歌曲token分隔：/ | ， , 、 → 空格
    after = re.sub(r"[/|，,、]", " ", after)
    tokens = after.split()
    songs: List[PlannerSongSelection] = []
    remaining_tokens = []
    for token in tokens:
        if not _looks_like_song_token(token):
            remaining_tokens.append(token)
            continue
        music_query, difficulty = split_difficulty(token)
        if not music_query:
            music_query = token
        songs.append(PlannerSongSelection(query=music_query, difficulty=difficulty))

    remaining = prefix
    if remaining_tokens:
        remaining += " " + " ".join(remaining_tokens)
    return songs, remaining.strip()


def _music_id_override(query: str) -> int:
    q = query.strip()
    if q in ("龙", "lost and found", "lostandfound"):
        return LOST_AND_FOUND_MUSIC_ID
    if q in ("野车", "omakase", "随机", "おまかせ"):
        return OMAKASE_MUSIC_ID
    return 0


def default_song_difficulty(query: str) -> str:
    if query == "虾":
        return "expert"
    if query == "龙":
        return "hard"
    return "master"


def apply_default_song_difficulties(songs: List[PlannerSongSelection]) -> None:
    for song in songs:
        if not song.difficulty:
            song.difficulty = default_song_difficulty(song.query)
        if song.music_id <= 0:
            song.music_id = _music_id_override(song.query)


def parse_event_planner_params(args: str, trigger: str) -> PlannerParams:
    args = str(args or "").strip()
    if not args:
        raise ReplyException(f"需要提供目标 pt 或目标排名，例如：{trigger} pt1000w\n查看完整用法：{trigger} -help")

    params = PlannerParams()

    if RE_TOTAL_RANKING.search(args):
        params.total_ranking = True
        args = RE_TOTAL_RANKING.sub(" ", args)

    rank_match = RE_TARGET_RANK.search(args)
    if rank_match:
        for group in rank_match.groups():
            if group:
                params.target_rank = int(group)
                break

    params.current_point, params.current_point_set = _parse_point_with_re(RE_CURRENT_POINT, args)

    target_args = RE_CURRENT_POINT.sub(" ", args)
    params.target_point, _ = _parse_point_with_re(RE_TARGET_POINT, target_args)

    deck_args = RE_CURRENT_POINT.sub(" ", args)
    deck_args = RE_TARGET_POINT.sub(" ", deck_args)
    deck_args = RE_TARGET_RANK.sub(" ", deck_args)
    if params.target_point == 0:
        params.target_point, deck_args = parse_bare_target_point(deck_args)

    params.boosts, deck_args = parse_boosts(deck_args, params.boosts)
    params.songs, deck_args = parse_songs(deck_args)
    if params.songs:
        apply_default_song_difficulties(params.songs)

    # 组卡参数（复用活动组卡解析）
    deck_args = deck_args.strip().lower()
    params.deck_additional, deck_args = extract_addtional_options(deck_args)
    params.deck_args = deck_args.strip()

    # 活动ID
    event_match = RE_EVENT_ID.search(deck_args)
    if event_match:
        params.event_id = int(event_match.group(1))
        params.deck_args = RE_EVENT_ID.sub(" ", params.deck_args).strip()

    if params.target_point == 0 and params.target_rank == 0:
        raise ReplyException(f"需要提供目标 pt 或目标排名，例如：{trigger} pt1000w")

    return params


async def resolve_planner_event(ctx: SekaiHandlerContext, params: PlannerParams) -> Tuple[dict, str]:
    if params.event_id > 0:
        event = await ctx.md.events.find_by_id(params.event_id)
        assert_and_reply(event, f"当前{get_region_name(ctx.region)}服未找到该活动数据")
        return event, ""
    event = await get_current_event(ctx, fallback=None)
    if not event:
        raise ReplyException(f"当前{get_region_name(ctx.region)}服没有进行中的活动，请在指令里加 eventID")
    return event, f"未指定活动，已使用当前活动 {event['name']}"


async def resolve_planner_target_point(
    ctx: SekaiHandlerContext,
    params: PlannerParams,
    event: dict,
) -> Tuple[int, str]:
    if params.target_point > 0:
        return params.target_point, "直接输入"
    if params.target_rank <= 0:
        raise ReplyException("缺少目标 pt")

    rankings = await get_latest_ranking(ctx, event['id'], [params.target_rank])
    if not rankings or not rankings[0].score:
        raise ReplyException(f"tracker 未返回 t{params.target_rank} 的有效榜线")
    return int(rankings[0].score), f"Tracker实时榜线:t{params.target_rank}"


async def resolve_planner_current_point(
    ctx: SekaiHandlerContext,
    params: PlannerParams,
    event: dict,
    profile: Suite | None,
) -> Tuple[int, bool, str]:
    if params.current_point_set:
        return params.current_point, True, ""
    if profile is not None:
        for ue in profile.get('userEvents', []):
            if isinstance(ue, dict) and int(ue.get('eventId') or 0) == int(event['id']):
                pt = int(ue.get('eventPoint') or 0)
                if pt > 0:
                    return pt, True, ""
    return 0, True, "未指定当前pt且未读取到当前活动PT，当前按 0 计算"


def planner_daily_point(
    target_point: int,
    current_point: int,
    start_at: int,
    aggregate_at: int,
    now_ms: int,
    current_point_known: bool,
) -> int:
    if target_point <= 0 or aggregate_at <= start_at:
        return 0
    point = target_point
    period_start = start_at
    if current_point_known and start_at < now_ms < aggregate_at:
        point = max(0, target_point - current_point)
        period_start = now_ms
    duration_days = (aggregate_at - period_start) / 86400000.0
    if duration_days <= 0:
        return 0
    return int(math.ceil(point / duration_days))


async def search_planner_music(ctx: SekaiHandlerContext, query: str, difficulty: str) -> Tuple[int, dict | None]:
    music_id = _music_id_override(query)
    music = None
    if music_id <= 0:
        search_options = MusicSearchOptions(use_emb=False, use_id=True, use_nidx=True, raise_when_err=False)
        search_options.diff = difficulty
        music = (await search_music(ctx, query, search_options)).music
        assert_and_reply(music, f"在组卡支持的所有歌曲中找不到\"{query}\"")
        music_id = music['id']
    return music_id, music


async def prepare_planner_deck_profile(
    ctx: SekaiHandlerContext,
    profile: Suite,
    params: PlannerParams,
) -> Suite:
    """
    按组卡参数加工suite（团/属性过滤、排除卡、当前主队、区域道具等级）
    与 compose_deck_recommend_image 的行为保持一致
    """
    additional = params.deck_additional

    # 组合卡牌过滤
    unit_filter = additional.get('unit_filter', None)
    if unit_filter:
        profile.userCards = [
            uc for uc in profile.userCards
            if await get_unit_by_card_id(ctx, uc['cardId'], return_support=(unit_filter != 'piapro')) == unit_filter
        ]
    # 属性卡牌过滤
    attr_filter = additional.get('attr_filter', None)
    if attr_filter:
        profile.userCards = [
            uc for uc in profile.userCards
            if (await ctx.md.cards.find_by_id(uc['cardId']))['attr'] == attr_filter
        ]
    # 排除卡牌
    excluded_cards = additional.get('excluded_cards', [])
    if excluded_cards:
        profile.userCards = [
            uc for uc in profile.userCards
            if uc['cardId'] not in excluded_cards
        ]

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
        for area in profile.userAreas:
            for area_item in area['areaItems']:
                item_id = area_item['areaItemId']
                if item_id in levels:
                    area_item['level'] = max(area_item['level'], levels[item_id])
                    del levels[item_id]
        profile.userAreas.append({
            "userAreaStatus": {},
            "areaItems": [
                {"areaItemId": item_id, "level": lv} for item_id, lv in levels.items()
            ],
        })

    return profile


async def build_planner_deck_options(
    ctx: SekaiHandlerContext,
    params: PlannerParams,
    event: dict,
    profile: Suite,
) -> DeckRecommendOptions:
    """
    构造基础组卡options（不含歌曲），后续每首歌复制一份并设置music_id
    """
    options = DeckRecommendOptions()
    options.region = ctx.region
    options.live_type = "multi"
    options.target = "score"
    options.algorithm = "all"
    options.limit = 1
    options.timeout_ms = int(config.get('deck.timeout.default') * 1000)

    additional = params.deck_additional

    # 队友参数（multi默认 25w/200）
    deck_args_for_multi = extract_multilive_options(params.deck_args, options)

    # 当前主队
    if additional.get('use_current_deck'):
        basic_profile = await get_basic_profile(
            ctx, get_player_bind_id(ctx),
            use_cache=False, use_remote_cache=False,
        )
        options.fixed_cards = [basic_profile['userDeck'][f'member{i}'] for i in range(1, 6)]
        options.fixed_characters = None
        options.best_skill_as_leader = False
        # 同步当前主队卡到suite（缺失则报错）
        for bp_card in basic_profile['userCards']:
            if p_card := find_by(profile.userCards, 'cardId', bp_card['cardId']):
                p_card.update(bp_card)
            else:
                raise ReplyException(f"当前卡组中的卡牌 {bp_card['cardId']} 不在Suite数据中，请更新抓包数据")

    # 固定卡/角色 + 卡牌配置（复用组卡解析）
    args = deck_args_for_multi
    args = extract_fixed_cards_and_characters(args, options)
    args = extract_card_config(args, options)

    # 事件
    options.event_id = int(event['id'])

    # 模拟退火设置
    options.sa_options = DeckRecommendSaOptions()
    options.sa_options.max_no_improve_iter = 10000

    return options


# ======================= 主流程 ======================= #

@dataclass
class PlannerRow:
    boost: int
    point_per_play: int
    plays: int
    energy: int


@dataclass
class PlannerSongResult:
    query: str
    title: str
    difficulty: str
    music_id: int
    cover: Image.Image | None = None
    rows: List[PlannerRow] = field(default_factory=list)


@dataclass
class PlannerResult:
    event: dict
    event_warning: str
    target_point: int
    target_source: str
    current_point: int
    current_warning: str
    remaining_point: int
    daily_point: int
    songs: List[PlannerSongResult]
    deck_summary: str
    total_power: int = 0
    event_bonus: float = 0
    skill_up: float = 0
    profile: Suite | None = None


def _format_planner_int(value: int | None) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _format_planner_optional_int(value: int | None) -> str:
    if value is None:
        return "-"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    if number <= 0:
        return "-"
    return f"{number:,}"


def _format_planner_rate(value: float | None) -> str:
    if value is None:
        return "0"
    rounded = round(value * 10) / 10
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def _build_deck_summary(params: PlannerParams, total_power: int, event_bonus: float, skill_up: float) -> str:
    label = "最优组卡"
    additional = params.deck_additional
    if params.deck_args and ('#' in params.deck_args):
        label = "指定卡组"
    elif additional.get('use_current_deck'):
        label = "当前主队"
    elif additional.get('max_profile'):
        label = "顶配组卡"
    elif additional.get('sub_max_profile'):
        label = "次顶配组卡"
    parts = [label]
    if total_power > 0:
        parts.append(f"综合力 {_format_planner_int(total_power)}")
    if event_bonus > 0:
        parts.append(f"活动加成 {_format_planner_rate(event_bonus)}%")
    if skill_up > 0:
        parts.append(f"协力实效 {_format_planner_rate(skill_up)}%")
    return " / ".join(parts)


async def compose_planner_result(ctx: SekaiHandlerContext, params: PlannerParams) -> PlannerResult:
    # 获取suite
    profile, pmsg = await get_detailed_profile(
        ctx, ctx.user_id,
        filter=get_detailed_profile_card_filter(
            'userEvents',
            'userCards',
            'userDecks',
            'userAreas',
            'userCharacters',
            'userMysekaiCanvases',
            'userMysekaiGates',
            'userMysekaiFixtureGameCharacterPerformanceBonuses',
            'userHonors',
            'userChallengeLiveSoloDecks',
            'userChallengeLiveSoloHighScoreRewards',
            'userChallengeLiveSoloStages',
            'userChallengeLiveSoloResults',
            'userMusicResults',
        ),
        strict=False,
        raise_exc=True,
    )
    assert_and_reply(profile, "未获取到Suite抓包数据")

    # 事件
    event, event_warning = await resolve_planner_event(ctx, params)

    # 目标/当前PT
    target_point, target_source = await resolve_planner_target_point(ctx, params, event)
    current_point, current_point_known, current_warning = await resolve_planner_current_point(ctx, params, event, profile)

    remaining = max(0, target_point - current_point)
    daily = planner_daily_point(
        target_point, current_point,
        int(event.get('startAt') or 0), int(event.get('aggregateAt') or 0),
        int(time.time() * 1000), current_point_known,
    )

    # 歌曲
    songs = list(params.songs) if params.songs else [PlannerSongSelection(**s) for s in DEFAULT_SONGS]
    apply_default_song_difficulties(songs)

    # 顶配/次顶配用合成profile，否则按参数加工suite
    use_max_profile = params.deck_additional.get('max_profile', False)
    use_sub_max_profile = params.deck_additional.get('sub_max_profile', False)
    if use_max_profile:
        deck_profile = await construct_max_profile(ctx)
    elif use_sub_max_profile:
        deck_profile = await construct_max_profile(ctx, max_area_item_level=15)
    else:
        deck_profile = await prepare_planner_deck_profile(ctx, profile, params)

    base_options = await build_planner_deck_options(ctx, params, event, deck_profile)

    # 每首歌一个组卡options
    options_list = []
    music_metas = []
    for song in songs:
        opt = DeckRecommendOptions(base_options)
        music_id, music = await search_planner_music(ctx, song.query, song.difficulty)
        song.music_id = music_id
        opt.music_id = music_id
        opt.music_diff = song.difficulty
        opt.limit = 1
        options_list.append(opt)
        music_metas.append(music)

    user_data = dump_bytes_json(deck_profile.to_dict())
    results = await do_deck_recommend_batch(ctx, options_list, user_data)

    planner_songs: List[PlannerSongResult] = []
    deck_summary = ""
    total_power = 0
    event_bonus = 0.0
    skill_up = 0.0
    for song, music, (res, algs, _) in zip(songs, music_metas, results):
        assert_and_reply(res.decks, f"组卡服务没有返回 {song.query} 的卡组数据")
        deck = res.decks[0]
        base_point = int(deck.score or 0)
        assert_and_reply(base_point > 0, f"组卡服务返回的 {song.query} PT 为 0")

        title = song.query
        cover = None
        if song.music_id == OMAKASE_MUSIC_ID:
            title = "おまかせ（所有歌曲平均）"
            cover = ctx.static_imgs.get('omakase.png')
        else:
            if music:
                title = truncate(music.get('title') or song.query, 20)
            try:
                cover = await get_music_cover_thumb(ctx, song.music_id)
            except Exception as e:
                logger.warning(f"获取歌曲封面失败 {song.music_id}: {get_exc_desc(e)}")

        rows = []
        for boost in params.boosts:
            point = base_point * BOOST_MULTIPLIERS.get(boost, 1)
            plays = int(math.ceil(remaining / point)) if remaining > 0 and point > 0 else 0
            rows.append(PlannerRow(boost=boost, point_per_play=point, plays=plays, energy=plays * boost))

        planner_songs.append(PlannerSongResult(
            query=song.query, title=title, difficulty=song.difficulty,
            music_id=song.music_id, cover=cover, rows=rows,
        ))

        if not deck_summary:
            total_power = int(deck.total_power or 0)
            event_bonus = float(deck.event_bonus_rate or 0)
            skill_up = float(deck.multi_live_score_up or 0)
            deck_summary = _build_deck_summary(params, total_power, event_bonus, skill_up)

    return PlannerResult(
        event=event,
        event_warning=event_warning,
        target_point=target_point,
        target_source=target_source,
        current_point=current_point,
        current_warning=current_warning,
        remaining_point=remaining,
        daily_point=daily,
        songs=planner_songs,
        deck_summary=deck_summary,
        total_power=total_power,
        event_bonus=event_bonus,
        skill_up=skill_up,
        profile=profile,
    )


# ======================= 渲染 ======================= #

async def compose_planner_image(ctx: SekaiHandlerContext, result: PlannerResult) -> Image.Image:
    event = result.event
    event_banner = None
    try:
        event_banner = await get_event_banner_img(ctx, event)
    except Exception as e:
        logger.warning(f"获取活动banner失败: {get_exc_desc(e)}")

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(14):
            # 头部：banner + 活动名 + 规划概览
            with HSplit().set_content_align('l').set_item_align('c').set_sep(14).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                if event_banner is not None:
                    ImageBox(event_banner, size=(210, 86), image_size_mode='fill', shadow=True)
                with VSplit().set_sep(4).set_content_align('lt').set_item_align('lt'):
                    TextBox("活动规划", TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(50, 50, 50)))
                    TextBox(truncate(str(event.get('name') or ''), 30), TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)))
                    with HSplit().set_sep(14).set_content_align('l').set_item_align('c'):
                        TextBox(f"目标 {_format_planner_int(result.target_point)}pt", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)))
                        TextBox(f"当前 {_format_planner_int(result.current_point)}pt", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)))
                        TextBox(f"还需 {_format_planner_int(result.remaining_point)}pt", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(0, 180, 220)))
                    if result.target_source:
                        TextBox(f"来源 {result.target_source}", TextStyle(font=DEFAULT_FONT, size=18, color=(90, 90, 90)), overflow='shrink').set_w(560)

            # 玩家资料
            if result.profile:
                await get_detailed_profile_card(ctx, result.profile, "", mode=get_user_data_mode(ctx, ctx.user_id))

            # 卡组摘要
            if result.deck_summary:
                TextBox(result.deck_summary, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(70, 70, 70))).set_padding(
                    (16, 10)).set_bg(roundrect_bg(alpha=60))

            # 歌曲规划表
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(10).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                th_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(75, 75, 75))
                with HSplit().set_content_align('l').set_item_align('c').set_sep(16):
                    TextBox("歌曲 / 火数", th_style).set_w(330).set_h(40).set_content_align('c')
                    TextBox("每把PT", th_style).set_w(140).set_h(40).set_content_align('c')
                    TextBox("需要把数", th_style).set_w(140).set_h(40).set_content_align('c')
                    TextBox("体力", th_style).set_w(120).set_h(40).set_content_align('c')
                    TextBox("日速", th_style).set_w(140).set_h(40).set_content_align('c')

                for song in result.songs:
                    for row in song.rows:
                        with HSplit().set_content_align('l').set_item_align('c').set_sep(16):
                            with HSplit().set_w(330).set_h(76).set_content_align('c').set_item_align('c').set_sep(10):
                                with Frame().set_size((56, 56)).set_content_align('c'):
                                    diff = (song.difficulty or "").lower()
                                    if diff in DIFF_COLORS:
                                        Spacer(w=52, h=52).set_bg(FillBg(fill=DIFF_COLORS[diff])).set_offset((3, 3))
                                    if song.cover is not None:
                                        ImageBox(song.cover, size=(52, 52)).set_offset((-2, -2))
                                    else:
                                        Spacer(w=52, h=52).set_bg(RoundRectBg((235, 242, 248, 255), 6)).set_offset((-2, -2))

                                with VSplit().set_w(230).set_content_align('l').set_item_align('l').set_sep(2):
                                    TextBox(song.title, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(70, 70, 70)), overflow='shrink').set_w(230)
                                    with HSplit().set_content_align('l').set_item_align('l').set_sep(6):
                                        TextBox(
                                            (song.difficulty or "DIFF").upper(),
                                            TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=DIFF_COLORS.get(diff, (70, 70, 70))),
                                        ).set_bg(RoundRectBg((255, 255, 255, 180), 4))
                                        TextBox(
                                            f"{row.boost}火",
                                            TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=(130, 80, 180)),
                                        ).set_bg(RoundRectBg((246, 237, 255, 220), 4))

                            def planner_number_cell(text: str, sub_text: str, width: int, color=(70, 70, 70)) -> None:
                                with VSplit().set_w(width).set_h(76).set_content_align('c').set_item_align('c').set_sep(2):
                                    TextBox(text, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=color), overflow='shrink').set_w(width).set_content_align('c')
                                    TextBox(sub_text, TextStyle(font=DEFAULT_FONT, size=13, color=(75, 75, 75))).set_w(width).set_content_align('c')

                            planner_number_cell(_format_planner_int(row.point_per_play), "pt/把", 140)
                            planner_number_cell(_format_planner_int(row.plays), "把", 140, (0, 180, 220))
                            planner_number_cell(_format_planner_int(row.energy), "火", 120, (142, 94, 190))
                            planner_number_cell(_format_planner_optional_int(result.daily_point), "pt/日", 140)

                if not result.songs:
                    TextBox("没有可展示的规划歌曲", TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(255, 50, 50)))

            # 提示
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                tip_style = TextStyle(font=DEFAULT_FONT, size=16, color=(20, 20, 20))
                TextBox("活动规划按当前数据估算，实际结算以游戏内和榜线更新为准。", tip_style, use_real_line_count=True).set_w(920)
                TextBox("未指定当前 pt 时按 0 计算；不写歌曲时默认虾 EXPERT / 龙 HARD。", tip_style, use_real_line_count=True).set_w(920)
                for warning in (result.event_warning, result.current_warning):
                    if warning:
                        TextBox(f"提示: {warning}", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(200, 75, 75)), use_real_line_count=True).set_w(920)

    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令 ======================= #

pjsk_event_planner = SekaiCmdHandler([
    "/活动规划", "/pjsk event planner", "/event-planner",
])
pjsk_event_planner.check_cdrate(cd).check_wblist(gbl)


@pjsk_event_planner.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    if args in ("help", "-help", "--help", "帮助"):
        return await ctx.asend_reply_msg(PLANNER_HELP)

    params = parse_event_planner_params(args, ctx.original_trigger_cmd)
    result = await compose_planner_result(ctx, params)
    image = await compose_planner_image(ctx, result)
    return await ctx.asend_reply_msg(await get_image_cq(image, low_quality=True))
