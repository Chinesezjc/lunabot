from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..gameapi import get_gameapi_config, request_gameapi
import hashlib


# ======================= 常量 ======================= #

HOUSING_HELP = """使用方式:
/bjsk
/bjsk 1
/bjsk 1,3,5
/bjsk 10-14
/bjsk id=25 1-5
/bjsk 25 1-5
/bjsk sample=2 interval=1000 1-5

不填参数时默认查询 1-5 名；可以写单个名次、逗号分隔或范围，一次最多查询 5 个名次。
可用区服前缀指定服务器，如 /jpbjsk 1-5、/cnbjsk 1-5。
结果基于采样统计，仅供参考。"""

DEFAULT_RANKS = [1, 2, 3, 4, 5]
DEFAULT_SAMPLE_COUNT = 1
MAX_SAMPLE_COUNT = 10
MAX_RANK_COUNT = 5
HOUSING_REFRESH_INTERVAL_SECONDS = 10  # 缓存新鲜度

HOUSING_NOTICE = "基于统计得出结果并不一定精确，仅供参考"

HOUSING_CACHE_DIR = f"{SEKAI_DATA_DIR}/mysekai_housing_cache"

BANNER_REL_PATH = "mysekai/effect/ui_anim/mysekai_housing_competition/lottery_result/{name}.png"

# ======================= 渲染常量 ======================= #

CARD_WIDTH = 680
CARD_INNER_WIDTH = CARD_WIDTH - 24
THUMBNAIL_SIZE = (CARD_INNER_WIDTH, 360)
HEADER_BANNER_SIZE = (210, 86)

TITLE_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(35, 35, 35, 255))
SUBTITLE_STYLE = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70, 255))
SMALL_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=(80, 80, 80, 255))
OWNER_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(45, 45, 45, 255))
NAME_STYLE = TextStyle(font=DEFAULT_FONT, size=17, color=(55, 55, 55, 255))
RANK_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=28, color=(35, 35, 35, 255))
META_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70, 255))


# ======================= 数据模型 ======================= #

@dataclass
class HousingCompetitionInfo:
    id: int = 0
    name: str = ""
    description: str = ""
    submit_start_at: int = 0
    review_start_at: int = 0
    submit_end_at: int = 0
    aggregate_at: int = 0
    background_assetbundle_name: str = ""
    banner_path: str = ""


@dataclass
class HousingCompetitionEntry:
    cache_key: str = ""
    rank: int = 0
    competition_id: int = 0
    owner_user_id: int = 0
    owner_user_name: str = ""
    entry_name: str = ""
    entry_word: str = ""
    thumbnail_path: str = ""
    submitted_at: int = 0
    review_count: int = 0
    tab_type: str = ""
    last_seen_at: int = 0


@dataclass
class HousingCompetitionLineResult:
    competition: HousingCompetitionInfo
    region: str
    entries: List[HousingCompetitionEntry]  # 已按名次排序选中的
    all_entries: List[HousingCompetitionEntry]
    unique_count: int
    sampled_at: int
    sample_count: int


# ======================= 参数解析 ======================= #

def _split_range_token(token: str) -> List[int]:
    """
    解析范围token，如 '10-14'、'1～5'、'3..8'
    """
    for sep in ("～", "~", "－", "—", "–", "..", "到", "至", "-"):
        if sep in token:
            parts = token.split(sep)
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                if start > end:
                    start, end = end, start
                return list(range(start, end + 1))
            return []
    return []


def parse_housing_ranks(args: str) -> Tuple[List[int], int, int, int]:
    """
    解析名次/期数/采样参数，返回 (ranks, housing_id, sample_count, sample_interval_ms)
    """
    ranks: List[int] = []
    housing_id = 0
    sample_count = DEFAULT_SAMPLE_COUNT
    sample_interval_ms = 0

    tokens = str(args or "").split()

    # 提取 id= / housing_id=
    remaining = []
    for token in tokens:
        lower = token.lower()
        if lower.startswith("id=") or lower.startswith("housing_id="):
            value = lower.split("=", 1)[1]
            if value.isdigit():
                housing_id = int(value)
                continue
        if lower.startswith("sample="):
            value = lower.split("=", 1)[1]
            if value.isdigit():
                sample_count = min(max(int(value), 1), MAX_SAMPLE_COUNT)
            continue
        if lower.startswith("interval="):
            value = lower.split("=", 1)[1]
            if value.isdigit():
                sample_interval_ms = int(value)
            continue
        remaining.append(token)

    # 简写形式：第一个token是纯数字，第二个是范围/名次列表 → 第一个是housing_id
    if not housing_id and len(remaining) >= 2:
        first = remaining[0]
        if first.isdigit() and int(first) > 0:
            second_ranks = _parse_rank_tokens(remaining[1:])
            if second_ranks:
                housing_id = int(first)
                remaining = remaining[1:]

    ranks = _parse_rank_tokens(remaining)
    return ranks, housing_id, sample_count, sample_interval_ms


def _parse_rank_tokens(tokens: List[str]) -> List[int]:
    ranks: List[int] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "-" in token or "～" in token or "~" in token or ".." in token or "到" in token or "至" in token or "－" in token or "—" in token or "–" in token:
            ranks.extend(_split_range_token(token))
            continue
        for part in re.split(r"[,，、]", token):
            part = part.strip()
            if part.isdigit():
                ranks.append(int(part))
    return ranks


def normalize_housing_ranks(ranks: List[int]) -> List[int]:
    if not ranks:
        return list(DEFAULT_RANKS)
    out = []
    seen = set()
    for rank in ranks:
        if rank <= 0:
            raise ReplyException("百景排名必须大于0")
        if rank in seen:
            continue
        seen.add(rank)
        out.append(rank)
    out.sort()
    if len(out) > MAX_RANK_COUNT:
        raise ReplyException(f"一次最多查询{MAX_RANK_COUNT}个百景排名")
    return out


# ======================= 数据获取 ======================= #

def _housing_competition_start_at(info: HousingCompetitionInfo) -> int:
    return info.review_start_at if info.review_start_at > 0 else info.submit_start_at


def housing_competition_info_from_masterdata(item: dict) -> HousingCompetitionInfo:
    info = HousingCompetitionInfo(
        id=int(item.get('id') or 0),
        name=(item.get('name') or "").strip(),
        description=(item.get('description') or "").strip(),
        submit_start_at=int(item.get('submitStartAt') or 0),
        review_start_at=int(item.get('reviewStartAt') or 0),
        submit_end_at=int(item.get('submitEndAt') or 0),
        aggregate_at=int(item.get('aggregateAt') or 0),
        background_assetbundle_name=(item.get('backgroundImageAssetbundleFileName') or "").strip(),
    )
    if not info.name:
        info.name = f"第{info.id}期"
    if info.background_assetbundle_name:
        info.banner_path = BANNER_REL_PATH.format(name=info.background_assetbundle_name)
    return info


async def resolve_housing_competition(ctx: SekaiHandlerContext, housing_id: int) -> HousingCompetitionInfo:
    items = await ctx.md.mysekai_housing_competitions.get()
    assert_and_reply(items, "mysekaiHousingCompetitions masterdata 不可用")

    if housing_id > 0:
        for item in items:
            info = housing_competition_info_from_masterdata(item)
            if info.id == housing_id:
                return info
        raise ReplyException(f"没有找到百景 housing_id={housing_id}")

    now_ms = int(time.time() * 1000)
    current: HousingCompetitionInfo | None = None
    for item in items:
        info = housing_competition_info_from_masterdata(item)
        if info.id == 0:
            continue
        start_at = _housing_competition_start_at(info)
        if start_at <= 0 or info.aggregate_at <= 0:
            continue
        if start_at <= now_ms < info.aggregate_at:
            if current is None or start_at > _housing_competition_start_at(current) or (
                start_at == _housing_competition_start_at(current) and info.id > current.id
            ):
                current = info
    if current is None:
        raise ReplyException("当前没有正在进行的烤森百景活动")
    return current


def housing_competition_entry_cache_key(entry: HousingCompetitionEntry) -> str:
    if entry.cache_key:
        return entry.cache_key
    raw = "\x00".join([
        str(entry.competition_id),
        str(entry.owner_user_id),
        str(entry.submitted_at),
        (entry.thumbnail_path or "").strip(),
        (entry.entry_name or "").strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def merge_housing_competition_entry(current: HousingCompetitionEntry, next_: HousingCompetitionEntry) -> HousingCompetitionEntry:
    out = HousingCompetitionEntry(
        cache_key=current.cache_key,
        competition_id=current.competition_id,
        owner_user_id=current.owner_user_id,
        owner_user_name=current.owner_user_name,
        entry_name=current.entry_name,
        entry_word=current.entry_word,
        thumbnail_path=current.thumbnail_path,
        submitted_at=current.submitted_at,
        review_count=current.review_count,
        tab_type=current.tab_type,
        last_seen_at=current.last_seen_at,
    )
    if next_.review_count >= current.review_count:
        out.review_count = next_.review_count
    if next_.competition_id:
        out.competition_id = next_.competition_id
    if (next_.owner_user_name or "").strip():
        out.owner_user_name = next_.owner_user_name
    if (next_.entry_name or "").strip():
        out.entry_name = next_.entry_name
    if (next_.entry_word or "").strip():
        out.entry_word = next_.entry_word
    if (next_.thumbnail_path or "").strip():
        out.thumbnail_path = next_.thumbnail_path
    if next_.submitted_at > 0:
        out.submitted_at = next_.submitted_at
    if (next_.tab_type or "").strip():
        out.tab_type = next_.tab_type
    if next_.last_seen_at > out.last_seen_at:
        out.last_seen_at = next_.last_seen_at
    if next_.owner_user_id:
        out.owner_user_id = next_.owner_user_id
    return out


def parse_housing_competition_entries(raw) -> Tuple[List[HousingCompetitionEntry], int]:
    if not isinstance(raw, dict):
        raise ReplyException("百景投稿列表格式错误")
    lottery_at = int(raw.get('lotteryAt') or 0)
    raw_items = raw.get('results')
    if not isinstance(raw_items, list):
        raw_items = []
    entries: List[HousingCompetitionEntry] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if 'isDisplayable' in item and not bool(item.get('isDisplayable')):
            continue
        entry = HousingCompetitionEntry(
            competition_id=int(item.get('mysekaiHousingCompetitionId') or item.get('mysekai_housing_competition_id') or 0),
            owner_user_id=int(item.get('mysekaiOwnerUserId') or item.get('mysekai_owner_user_id') or 0),
            owner_user_name=str(item.get('mysekaiOwnerUserName') or item.get('mysekai_owner_user_name') or ""),
            entry_name=str(item.get('userMysekaiHousingCompetitionName') or item.get('user_mysekai_housing_competition_name') or ""),
            entry_word=str(item.get('userMysekaiHousingCompetitionWord') or item.get('user_mysekai_housing_competition_word') or ""),
            thumbnail_path=str(item.get('thumbnailPath') or item.get('thumbnail_path') or ""),
            submitted_at=int(item.get('submittedAt') or 0),
            review_count=int(item.get('reviewCount') or item.get('review_count') or 0),
            tab_type=str(item.get('mysekaiHousingCompetitionTabType') or item.get('mysekai_housing_competition_tab_type') or ""),
            last_seen_at=lottery_at if lottery_at > 0 else int(time.time() * 1000),
        )
        entry.cache_key = housing_competition_entry_cache_key(entry)
        entries.append(entry)
    return entries, lottery_at


async def fetch_housing_entries_once(ctx: SekaiHandlerContext, housing_id: int) -> Tuple[List[HousingCompetitionEntry], int]:
    url = get_gameapi_config(ctx).mysekai_housing_api_url
    assert_and_reply(url, f"暂不支持{get_region_name(ctx.region)}的烤森百景查询")
    try:
        url = url.format(housing_id=housing_id)
        sep = '&' if '?' in url else '?'
        raw = await request_gameapi(url + f"{sep}is_lottery=True")
    except HttpError as e:
        logger.info(f"获取百景 {ctx.region} {housing_id} 投稿列表失败: {get_exc_desc(e)}")
        raise ReplyException(f"获取百景投稿列表失败: {get_exc_desc(e)}")
    if 'error' in raw and raw.get('error'):
        raise ReplyException(f"获取百景投稿列表失败: {raw['error']}")
    return parse_housing_competition_entries(raw)


def sort_housing_competition_entries(entries: List[HousingCompetitionEntry]) -> None:
    entries.sort(key=lambda e: (
        -e.review_count,
        e.submitted_at,
        e.cache_key,
        e.entry_name,
    ))


def _housing_cache_path(region: str, housing_id: int) -> str:
    return f"{HOUSING_CACHE_DIR}/{region}/{housing_id}.json"


def _load_housing_cache(region: str, housing_id: int) -> Tuple[Dict[str, HousingCompetitionEntry], int]:
    path = _housing_cache_path(region, housing_id)
    if not os.path.exists(path):
        return {}, 0
    try:
        data = load_json(path)
        entries_map: Dict[str, HousingCompetitionEntry] = {}
        refreshed_at = int(data.get('refreshed_at') or 0)
        for item in data.get('entries', []):
            entry = HousingCompetitionEntry(**item)
            if entry.cache_key:
                entries_map[entry.cache_key] = entry
        return entries_map, refreshed_at
    except Exception as e:
        logger.warning(f"加载百景缓存失败 {path}: {get_exc_desc(e)}")
        return {}, 0


def _save_housing_cache(region: str, housing_id: int, entries_map: Dict[str, HousingCompetitionEntry], refreshed_at: int) -> None:
    path = _housing_cache_path(region, housing_id)
    try:
        create_parent_folder(path)
        payload = {
            'version': 1,
            'refreshed_at': refreshed_at,
            'entries': [
                {
                    'cache_key': e.cache_key,
                    'competition_id': e.competition_id,
                    'owner_user_name': e.owner_user_name,
                    'entry_name': e.entry_name,
                    'entry_word': e.entry_word,
                    'thumbnail_path': e.thumbnail_path,
                    'submitted_at': e.submitted_at,
                    'review_count': e.review_count,
                    'tab_type': e.tab_type,
                    'last_seen_at': e.last_seen_at,
                }
                for e in entries_map.values()
            ],
        }
        dump_json(payload, path)
    except Exception as e:
        logger.warning(f"保存百景缓存失败 {path}: {get_exc_desc(e)}")


async def load_housing_competition_stats(
    ctx: SekaiHandlerContext,
    housing_id: int,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_interval_ms: int = 0,
) -> Tuple[List[HousingCompetitionEntry], int, int]:
    """
    获取百景统计：采样(sample_count次)合并进持久化缓存后返回全部投稿
    返回 (entries, sampled_at_ms, refreshed_count)
    """
    sample_count = min(max(sample_count, 1), MAX_SAMPLE_COUNT)
    now_ms = int(time.time() * 1000)

    entries_map, refreshed_at = _load_housing_cache(ctx.region, housing_id)
    # 缓存新鲜则直接返回快照
    if entries_map and (now_ms - refreshed_at) < HOUSING_REFRESH_INTERVAL_SECONDS * 1000:
        all_entries = list(entries_map.values())
        sort_housing_competition_entries(all_entries)
        return all_entries, refreshed_at, 0

    sampled_at = 0
    for i in range(sample_count):
        entries, lottery_at = await fetch_housing_entries_once(ctx, housing_id)
        if lottery_at > 0:
            sampled_at = max(sampled_at, lottery_at)
        for entry in entries:
            key = entry.cache_key
            if key in entries_map:
                entries_map[key] = merge_housing_competition_entry(entries_map[key], entry)
            else:
                entries_map[key] = entry
        if i + 1 < sample_count and sample_interval_ms > 0:
            await asyncio.sleep(sample_interval_ms / 1000.0)

    if sampled_at <= 0:
        sampled_at = now_ms
    _save_housing_cache(ctx.region, housing_id, entries_map, now_ms)

    all_entries = list(entries_map.values())
    sort_housing_competition_entries(all_entries)
    return all_entries, sampled_at, sample_count


async def build_housing_competition_line(
    ctx: SekaiHandlerContext,
    ranks: List[int],
    housing_id: int = 0,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_interval_ms: int = 0,
) -> HousingCompetitionLineResult:
    ranks = normalize_housing_ranks(ranks)
    competition = await resolve_housing_competition(ctx, housing_id)
    all_entries, sampled_at, refreshed_count = await load_housing_competition_stats(
        ctx, competition.id, sample_count, sample_interval_ms
    )
    assert_and_reply(all_entries, "没有采样到可用的百景投稿")

    for i, entry in enumerate(all_entries):
        entry.rank = i + 1

    selected = []
    for rank in ranks:
        if rank > len(all_entries):
            raise ReplyException(f"只采样到{len(all_entries)}个唯一百景投稿，无法查询第{rank}名")
        selected.append(all_entries[rank - 1])

    return HousingCompetitionLineResult(
        competition=competition,
        region=ctx.region,
        entries=selected,
        all_entries=all_entries,
        unique_count=len(all_entries),
        sampled_at=sampled_at,
        sample_count=refreshed_count,
    )


# ======================= 渲染 ======================= #

async def _load_housing_thumbnail(ctx: SekaiHandlerContext, thumbnail_path: str) -> Image.Image | None:
    path = (thumbnail_path or "").strip()
    if not path:
        return None
    url = get_gameapi_config(ctx).mysekai_housing_thumbnail_api_url
    if not url:
        return None
    try:
        data = await request_gameapi(url.format(thumbnail_path=path), data_type='bytes')
        return open_image(io.BytesIO(data))
    except Exception as e:
        logger.warning(f"获取百景缩略图失败 {truncate(path, 40)}: {get_exc_desc(e)}")
        return None


async def _load_housing_banner(ctx: SekaiHandlerContext, competition: HousingCompetitionInfo) -> Image.Image:
    if competition.banner_path:
        try:
            return await ctx.rip.img(competition.banner_path, use_img_cache=True)
        except Exception as e:
            logger.warning(f"获取百景banner失败: {get_exc_desc(e)}")
    return UNKNOWN_IMG


def _entry_neighbor_text(label: str, score: int | None, delta: int | None, verb: str) -> str:
    if score is None:
        return f"{label} 无"
    return f"{label} {score}，{verb} {max(0, int(delta or 0))}"


def _draw_housing_entry_block(entry: HousingCompetitionEntry, thumbnail: Image.Image | None, all_entries: List[HousingCompetitionEntry]) -> None:
    owner = (entry.owner_user_name or "").strip() or "匿名玩家"
    work = (entry.entry_name or "").strip() or "未命名投稿"

    # 前后差距按该名次在完整榜单中的实际位置计算（而非selected中的下标）
    pos = max(0, entry.rank - 1)
    previous_score = all_entries[pos - 1].review_count if pos > 0 else None
    previous_delta = (previous_score - entry.review_count) if previous_score is not None else None
    next_score = all_entries[pos + 1].review_count if pos + 1 < len(all_entries) else None
    next_delta = (entry.review_count - next_score) if next_score is not None else None

    with VSplit().set_w(CARD_WIDTH).set_padding(12).set_sep(7).set_bg(roundrect_bg(alpha=80)).set_item_align('lt'):
        TextBox(owner, OWNER_STYLE, overflow='shrink').set_w(CARD_INNER_WIDTH)
        TextBox(f"作品: {work}", NAME_STYLE, line_count=2, overflow='shrink').set_w(CARD_INNER_WIDTH)
        if thumbnail is not None:
            ImageBox(thumbnail, size=THUMBNAIL_SIZE, image_size_mode='fill', shadow=True)
        else:
            Spacer(w=CARD_INNER_WIDTH, h=360).set_bg(RoundRectBg((235, 242, 248, 255), 6))
        TextBox(f"点赞数 {entry.review_count}，排名 {entry.rank}", RANK_STYLE).set_w(CARD_INNER_WIDTH)
        with HSplit().set_sep(12).set_content_align('lt').set_item_align('lt'):
            TextBox(_entry_neighbor_text("上一名", previous_score, previous_delta, "还差"), META_STYLE).set_w(322)
            TextBox(_entry_neighbor_text("下一名", next_score, next_delta, "领先"), META_STYLE).set_w(322)
        if (entry.entry_word or "").strip():
            TextBox(entry.entry_word, META_STYLE, line_count=2, overflow='shrink').set_w(CARD_INNER_WIDTH)


async def compose_housing_competition_image(ctx: SekaiHandlerContext, result: HousingCompetitionLineResult) -> Image.Image:
    banner_task = _load_housing_banner(ctx, result.competition)
    thumbnail_tasks = [_load_housing_thumbnail(ctx, entry.thumbnail_path) for entry in result.entries]
    banner, *thumbnails = await asyncio.gather(banner_task, *thumbnail_tasks)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(10).set_content_align('lt').set_item_align('lt'):
            with VSplit().set_w(CARD_WIDTH).set_padding(12).set_sep(8).set_bg(roundrect_bg(alpha=80)):
                with HSplit().set_sep(12).set_content_align('lt').set_item_align('lt'):
                    ImageBox(banner, size=HEADER_BANNER_SIZE, image_size_mode='fill', shadow=True)
                    with VSplit().set_sep(4).set_content_align('lt').set_item_align('lt'):
                        TextBox(f"烤森百景 {result.competition.name}", TITLE_STYLE, line_count=2, overflow='shrink').set_w(420)
                        TextBox(HOUSING_NOTICE, SUBTITLE_STYLE, line_count=2, overflow='shrink').set_w(420)
                        meta = f"{result.region.upper()}-{result.competition.id}"
                        meta += f"  统计 {result.unique_count} 个投稿"
                        TextBox(meta, SMALL_STYLE, overflow='shrink').set_w(420)

            entries = list(result.entries[:5])
            if not entries:
                TextBox("没有采样到可显示的百景投稿", TITLE_STYLE).set_w(CARD_WIDTH).set_padding(18).set_bg(
                    roundrect_bg(alpha=80)
                )
            for entry, thumbnail in zip(entries, thumbnails):
                _draw_housing_entry_block(entry, thumbnail, result.all_entries)

    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令 ======================= #

pjsk_housing_sk = SekaiCmdHandler([
    "/百景sk", "/百景SK", "/烤森百景sk", "/烤森百景SK",
    "/mysekai-housing-sk", "/mshsk", "/bjsk",
])
pjsk_housing_sk.check_cdrate(cd).check_wblist(gbl)


@pjsk_housing_sk.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    if args in ("help", "-help", "--help", "帮助"):
        return await ctx.asend_reply_msg(HOUSING_HELP)

    ranks, housing_id, sample_count, sample_interval_ms = parse_housing_ranks(args)

    result = await build_housing_competition_line(
        ctx, ranks, housing_id=housing_id,
        sample_count=sample_count, sample_interval_ms=sample_interval_ms,
    )
    image = await compose_housing_competition_image(ctx, result)
    return await ctx.asend_reply_msg(await get_image_cq(image, low_quality=True))
