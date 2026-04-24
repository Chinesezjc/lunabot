from ...utils import *
from ...llm import translate_text
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..gameapi import get_gameapi_config, request_gameapi
from .profile import get_player_bind_id
from .event import (
    get_current_event, 
    get_event_banner_img, 
    parse_search_single_event_args,
    get_wl_chapter_cid,
    get_wl_events,
)
from .sk_sql import (
    Ranking, 
    query_ranking, 
    query_latest_ranking, 
    query_first_ranking_after,
    query_update_time,
    archive_database,
)
from .sk_forecast import (
    get_forecast_data,
    save_rankings_to_csv,
    get_local_forecast_history_csv_path,
)
from pathlib import Path
import zipfile
from matplotlib import pyplot as plt
from matplotlib import font_manager as mpl_font_manager
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
import matplotlib
import matplotlib.cm as cm
import numpy as np
import subprocess

DEFAULT_MPL_FONT_NAME = "Source Han Sans CN"
BUILTIN_MPL_FONT_PATHS = [
    Path(__file__).resolve().parents[4] / "data" / "utils" / "fonts" / name
    for name in [
        "SourceHanSansCN-Regular.otf",
        "SourceHanSansCN-Bold.otf",
        "SourceHanSansCN-Heavy.otf",
    ]
]


def register_matplotlib_font() -> str:
    configured_font_name = global_config.get("font.name", default=None, raise_exc=False)
    configured_font_path = global_config.get("font.path", default=None, raise_exc=False)
    font_paths = []
    if configured_font_path:
        font_paths.append(Path(configured_font_path).expanduser())
    font_paths.extend(BUILTIN_MPL_FONT_PATHS)

    font_name = None
    for path in font_paths:
        if not path.exists():
            continue
        try:
            mpl_font_manager.fontManager.addfont(str(path))
            if font_name is None:
                font_name = mpl_font_manager.FontProperties(fname=str(path)).get_name()
        except Exception:
            continue
    return font_name or configured_font_name or DEFAULT_MPL_FONT_NAME


FONT_NAME = register_matplotlib_font()
plt.switch_backend('agg')
matplotlib.rcParams['font.family'] = [FONT_NAME]
matplotlib.rcParams['axes.unicode_minus'] = False  

SK_RECORD_TOLERANCE_CFG = config.item("sk.record_interval_tolerance")

SKL_QUERY_RANKS = [
    *range(10, 51, 10),
    *range(100, 501, 100),
    *range(1000, 5001, 1000),
    *range(10000, 50001, 10000),
    *range(100000, 500001, 100000),
]
ALL_RANKS = [
    *range(1, 100),
    *range(100, 501, 100),
    *range(1000, 5001, 1000),
    1500, 2500,
    *range(10000, 50001, 10000),
    *range(100000, 500001, 100000),
]

latest_rankings_cache: Dict[str, Dict[int, List[Ranking]]] = {}
latest_rankings_mtime: Dict[str, Dict[int, datetime]] = {}

@dataclass
class PredictWinrate:
    event_id: int
    recruiting: Dict[int, bool]
    predict_rates: Dict[int, float]
    predict_time: datetime

SK_TEXT_QUERY_BG_COLOR = [255, 255, 255, 150]
SK_PLAYCOUNT_MYSEKAI_THRESHOLD = 37


# ======================= 处理逻辑 ======================= #

# 检查某个榜线记录是否属于高精度记录
def check_ranking_is_high_res(region: str, ranking: Ranking):
    for rank_min, rank_max in config.get('sk.high_res_record.ranks', {}).get(region, []):
        if rank_min <= ranking.rank <= rank_max:
            return True
    for uid in config.get('sk.high_res_record.uids', {}).get(region, []):
        if str(ranking.uid) == str(uid):
            return True
    return False

# 采样榜线，保证首个和最后一个记录被保留
def sample_ranking_list(rankings: list[Ranking], limit: int) -> list[Ranking]:
    if len(rankings) <= limit:
        return rankings
    step = (len(rankings) - 1) / (limit - 1)
    sampled = []
    for i in range(limit):
        idx = round(i * step)
        sampled.append(rankings[idx])
    if sampled[-1] != rankings[-1]:
        sampled[-1] = rankings[-1]
    return sampled

# 获取用于显示的活动ID-活动名称文本
def get_event_id_and_name_text(region: str, event_id: int, event_name: str) -> str:
    if event_id < 1000:
        return f"【{region.upper()}-{event_id}】{event_name}"
    else:
        chapter_id = event_id // 1000
        event_id = event_id % 1000
        return f"【{region.upper()}-{event_id}-第{chapter_id}章单榜】{event_name}"

# 从参数获取带有wl_id的wl_event，返回 (wl_event, args)，未指定章节则默认查询当前章节
async def extract_wl_event(ctx: SekaiHandlerContext, args: str) -> Tuple[dict, str]:
    if 'wl' not in args:
        return None, args
    else:
        event = await get_current_event(ctx, fallback="prev")
        chapters = await ctx.md.world_blooms.find_by('eventId', event['id'], mode='all')
        assert_and_reply(chapters, f"当期活动{ctx.region.upper()}-{event['id']}并不是WorldLink活动")

        # 通过"wl序号"查询章节
        def query_by_seq() -> Tuple[Optional[int], Optional[str]]:
            for i in range(len(chapters)):
                carg = f"wl{i+1}"
                if carg in args:
                    chapter_id = i + 1
                    return chapter_id, carg
            return None, None
        # 通过"wl角色昵称"查询章节
        def query_by_nickname() -> Tuple[Optional[int], Optional[str]]:
            for nickname, cid in get_character_nickname_data().nickname_ids:
                for carg in (f"wl{nickname}", f"-c {nickname}", f"{nickname}"):
                    if carg in args:
                        chapter = find_by(chapters, "gameCharacterId", cid)
                        assert_and_reply(chapter, f"当期活动{ctx.region.upper()}-{event['id']}并没有角色{nickname}的章节")
                        chapter_id = chapter['chapterNo']
                        return chapter_id, carg
            return None, None
        # 查询当前章节
        def query_current() -> Tuple[Optional[int], Optional[str]]:
            now = datetime.now()
            chapters.sort(key=lambda x: x['chapterNo'], reverse=True)
            for chapter in chapters:
                start = datetime.fromtimestamp(chapter['chapterStartAt'] / 1000)
                if start <= now:
                    chapter_id = chapter['chapterNo']
                    return chapter_id, "wl"
            return None, None
        
        chapter_id, carg = query_by_seq()
        if not chapter_id:
            chapter_id, carg = query_by_nickname()
        if not chapter_id:
            chapter_id, carg = query_current()
        assert_and_reply(chapter_id, f"""
查询WL活动榜线需要指定章节，可用参数格式:
1. wl: 查询当前章节
2. wl2: 查询第二章
3. wlmiku: 查询miku章节
""".strip())

        chapter = find_by(chapters, "chapterNo", chapter_id)
        event = event.copy()
        event['id'] = chapter_id * 1000 + event['id']
        event['startAt'] = chapter['chapterStartAt']
        event['aggregateAt'] = chapter['aggregateAt']
        event['wl_cid'] = chapter.get('gameCharacterId', None)
        args = args.replace(carg, "").replace("wl", "")

        logger.info(f"查询WL活动章节: chapter_arg={carg} wl_id={event['id']}")
        return event, args

# 绘制昼夜变化背景
def draw_daynight_bg(ax, start_time: datetime, end_time: datetime):
    t_start = mdates.date2num(start_time)
    t_end = mdates.date2num(end_time)
    step = 1.0 / 24.0 
    times = np.arange(t_start, t_end, step)
    if len(times) == 0:
        return
    ratio = np.sin(2 * np.pi * times - np.pi / 2)
    mix_factor = (ratio + 1) / 2
    night_color = np.array([200, 200, 230]) / 255.0
    day_color = np.array([245, 245, 250]) / 255.0
    colors = (1 - mix_factor[:, None]) * night_color + mix_factor[:, None] * day_color
    img_data = colors.reshape(1, -1, 3)
    ylim = ax.get_ylim()
    ax.imshow(
        img_data, 
        extent=[t_start, t_end, ylim[0], ylim[1]], 
        aspect='auto', 
        origin='lower',
        zorder=0
    )
    ax.set_ylim(ylim)

# 从榜线列表中找到最近的前一个榜线
def find_prev_ranking(ranks: List[Ranking], rank: int) -> Optional[Ranking]:
    most_prev = None
    for r in ranks:
        if r.rank >= rank:
            continue
        if not most_prev or r.rank > most_prev.rank:
            most_prev = r
    return most_prev

# 从榜线列表中找到最近的后一个榜线
def find_next_ranking(ranks: List[Ranking], rank: int) -> Optional[Ranking]:
    most_next = None
    for r in ranks:
        if r.rank <= rank:
            continue
        if not most_next or r.rank < most_next.rank:
            most_next = r
    return most_next

# 从榜线数据解析Rankings
async def parse_rankings(ctx: SekaiHandlerContext, event_id: int, data: dict) -> List[Ranking]:
    data_top100 = data.get('top100', {})
    data_border = data.get('border', {})
    assert data_top100, "获取榜线Top100数据失败"
    assert data_border, "获取榜线Border数据失败"

    # 普通活动
    if event_id < 1000:
        top100 = [Ranking.from_sk(item) for item in data_top100['rankings']]
        border = [Ranking.from_sk(item) for item in data_border['borderRankings'] if item['rank'] != 100]
    
    # WL活动
    else:
        cid = await get_wl_chapter_cid(ctx, event_id)
        top100_rankings = find_by(data_top100.get('userWorldBloomChapterRankings', []), 'gameCharacterId', cid)
        top100 = [Ranking.from_sk(item) for item in top100_rankings['rankings']]
        border_rankings = find_by(data_border.get('userWorldBloomChapterRankingBorders', []), 'gameCharacterId', cid)
        border = [Ranking.from_sk(item) for item in border_rankings['borderRankings'] if item['rank'] != 100]

    for item in top100:
        item.uid = str(item.uid)
    for item in border:
        item.uid = str(item.uid)
    
    return top100 + border
  
# 获取最新榜线记录
async def get_latest_ranking(ctx: SekaiHandlerContext, event_id: int, query_ranks: List[int] = ALL_RANKS) -> List[Ranking]:
    # 从缓存中获取
    db_mtime = query_update_time(ctx.region, event_id)
    rankings = latest_rankings_cache.get(ctx.region, {}).get(event_id, None)
    if rankings and latest_rankings_mtime.get(ctx.region, {}).get(event_id, 0) == db_mtime:
        logger.info(f"从缓存中获取 {ctx.region}_{event_id} 最新榜线数据")
        return [r for r in rankings if r.rank in query_ranks]
    # 从数据库中获取，并更新缓存
    rankings = await query_latest_ranking(ctx.region, event_id)
    if rankings:
        logger.info(f"从数据库获取 {ctx.region}_{event_id} 最新榜线数据")
        latest_rankings_cache.setdefault(ctx.region, {})[event_id] = rankings
        latest_rankings_mtime.setdefault(ctx.region, {})[event_id] = db_mtime
        return [r for r in rankings if r.rank in query_ranks]
    # 从API获取
    url = get_gameapi_config(ctx).ranking_api_url
    assert_and_reply(url, f"暂不支持获取{ctx.region}榜线数据")
    data = await request_gameapi(url.format(event_id=event_id % 1000))
    assert_and_reply(data, "获取榜线数据失败")
    logger.info(f"从API获取 {ctx.region}_{event_id} 最新榜线数据")
    return [r for r in await parse_rankings(ctx, event_id, data) if r.rank in query_ranks]

# 获取榜线分数字符串
def get_board_score_str(score: int, width: int = None, precise: bool = True) -> str:
    if score is None:
        ret = "?"
    else:
        score = int(score)
        M = 10000
        if precise:
            ret = f"{score // M}.{score % M:04d}w"
        else:
            ret = f"{score // M}.{score % M:04d}"
            ret = ret.rstrip('0').rstrip('.') + 'w'
    if width:
        ret = ret.rjust(width)
    return ret

# 获取榜线排名字符串
def get_board_rank_str(rank: int) -> str:
    # 每3位加一个逗号
    return "{:,}".format(rank)

# 判断字符串是否为排名文本
def is_rank_text(s: str) -> bool:
    s = s.strip().rstrip('w').rstrip('k')
    return s.isdigit()

# 从排名文本获取排名整数
def get_rank_from_text(s: str) -> int:
    s = s.strip().lower()
    try:
        if s.endswith('w'):
            s = s[:-1]
            return int(s) * 10000
        if s.endswith('k'):
            s = s[:-1]
            return int(s) * 1000
        return int(s)
    except:
        raise ReplyException(f"无法解析的排名\"{s}\"")

# 合成榜线预测图片
async def compose_skp_image(ctx: SekaiHandlerContext) -> Image.Image:
    event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")
    event_id, event_name = event['id'], event['name']
    event_start = datetime.fromtimestamp(event['startAt'] / 1000)
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    banner_img = await get_event_banner_img(ctx, event)
    chapter_id = event_id // 1000

    start_hours = config.get('sk.start_forecast_hours_after_event_start')
    end_hours = config.get('sk.stop_forecast_hours_before_event_end')

    forecasts = await get_forecast_data(ctx.region, event['id'])
    sources = {}
    for key, cfg in config.get('sk.forecast').items():
        if not cfg.get('enabled'):
            continue
        if ctx.region not in cfg.get('regions'):
            continue
        if chapter_id and not cfg.get('support_wl'):
            continue
        sources[key] = cfg
    
    ranks = set()
    for forecast in forecasts:
        if forecast.rank_data:
            ranks.update(forecast.rank_data.keys())
    ranks = sorted(ranks)

    latest_rankings = await get_latest_ranking(ctx, event_id, ranks)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(16).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(f"【{ctx.region.upper()}-{event_id}】{truncate(event_name, 20)}", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}", 
                            TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    time_from_start = datetime.now() - event_start
                    if time_to_end.total_seconds() <= 0:
                        time_to_end_text = "活动已结束"
                    else:
                        time_to_end_text = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end_text, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    at_start_phase, at_end_phase = False, False
                    if time_from_start < timedelta(hours=start_hours):
                        at_start_phase = True
                        TextBox(f"活动开始{start_hours}小时后开始更新数据", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=RED))
                    elif time_to_end < timedelta(hours=end_hours):
                        at_end_phase = True
                        TextBox(f"活动结束前{end_hours}小时停止更新数据", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=RED))

                if banner_img:
                    ImageBox(banner_img, size=(140, None))

            gh, gw = 30, 180
            with Grid(col_count=len(sources)+2).set_content_align('c').set_sep(hsep=8, vsep=5).set_padding(16):
                bg1 = FillBg((255, 255, 255, 200))
                bg2 = FillBg((255, 255, 255, 100))
                title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
                item_style  = TextStyle(font=DEFAULT_FONT,      size=20, color=BLACK)

                TextBox("排名", title_style).set_bg(bg1).set_size((gw, gh)).set_content_align('c')
                TextBox("当前榜线", title_style).set_bg(bg1).set_size((gw, gh)).set_content_align('c')
                for source in sources.keys():
                    TextBox(sources[source]['name'], title_style).set_bg(bg1).set_size((gw, gh)).set_content_align('c')

                bg = bg1
                for i, rank in enumerate(ranks):
                    bg = bg2 if bg == bg1 else bg1

                    TextBox(get_board_rank_str(rank), item_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c')

                    cur_text = "-"
                    if cur_rank := find_by_predicate(latest_rankings, lambda x: x.rank == rank):
                       cur_text = get_board_score_str(cur_rank.score)
                    TextBox(cur_text, item_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('r').set_padding((16, 0))

                    for source in sources.keys():
                        forecast_final = "-"
                        if forecast := find_by_predicate(forecasts, lambda x: x.source == source):
                            if rank_data := forecast.rank_data.get(rank, None):
                                if rank_data.final_score is not None:
                                    forecast_final = get_board_score_str(rank_data.final_score)
                        TextBox(forecast_final, item_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('r').set_padding((16, 0))

                OUTDATE_COLOR = (200, 0, 0)
                FROZEN_COLOR = (0, 100, 200)

                bg = bg2 if bg == bg1 else bg1
                TextBox("预测时间", title_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c')
                TextBox('-', item_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c').set_padding((0, 0))
                for source in sources.keys():
                    forcast_time_text = "-"
                    style = item_style
                    if forecast := find_by_predicate(forecasts, lambda x: x.source == source):
                        if forecast.forecast_ts:
                            forecast_time = datetime.fromtimestamp(forecast.forecast_ts)
                            forcast_time_text = get_readable_datetime(forecast_time, show_original_time=False)
                            if datetime.now() - forecast_time > timedelta(hours=3):
                                style = style.replace(color=OUTDATE_COLOR)
                                forcast_time_text += "⚠️"
                    if at_end_phase:
                        if forcast_time_text != '-': 
                            forcast_time_text = forcast_time_text.removesuffix("⚠️") + "❄️"
                        style = style.replace(color=FROZEN_COLOR)
                    TextBox(forcast_time_text, style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c').set_padding((0, 0))

                bg = bg2 if bg == bg1 else bg1
                TextBox("获取时间", title_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c')
                update_time = get_readable_datetime(latest_rankings[0].time, show_original_time=False) if latest_rankings else "-"
                TextBox(update_time, item_style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c').set_padding((0, 0))
                for source in sources.keys():
                    style = item_style
                    update_time_text = "-"
                    if forecast := find_by_predicate(forecasts, lambda x: x.source == source):
                        if forecast.mtime:
                            update_time_text = get_readable_datetime(datetime.fromtimestamp(forecast.mtime), show_original_time=False)
                    if at_end_phase:
                        if update_time_text != '-': update_time_text += "❄️"
                        style = style.replace(color=FROZEN_COLOR)
                    TextBox(update_time_text, style, overflow='clip').set_bg(bg).set_size((gw, gh)).set_content_align('c').set_padding((0, 0))

    add_watermark(canvas)
    return await canvas.get_img()

# 合成整体榜线图片
async def compose_skl_image(ctx: SekaiHandlerContext, event: dict = None, full: bool = False) -> Image.Image:
    if not event:
        event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")
    eid = event['id']
    event_start = datetime.fromtimestamp(event['startAt'] / 1000)
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    title = event['name']
    banner_img = await get_event_banner_img(ctx, event)
    wl_cid = await get_wl_chapter_cid(ctx, eid)

    query_ranks = ALL_RANKS if full else SKL_QUERY_RANKS
    ranks = await get_latest_ranking(ctx, eid, query_ranks)
    ranks = sorted(ranks, key=lambda x: x.rank)
    
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg()):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(8).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(get_event_id_and_name_text(ctx.region, eid, truncate(title, 16)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}", 
                            TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                with Frame().set_content_align('r'):
                    if banner_img:
                        ImageBox(banner_img, size=(140, None))
                    if wl_cid:
                        ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))

            if ranks:
                gh = 30
                bg1 = FillBg((255, 255, 255, 200))
                bg2 = FillBg((255, 255, 255, 100))
                title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
                item_style  = TextStyle(font=DEFAULT_FONT,      size=20, color=BLACK)
                with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(8):
                    with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_padding(0):
                        TextBox("排名", title_style).set_bg(bg1).set_size((140, gh)).set_content_align('c')
                        # TextBox("名称", title_style).set_bg(bg1).set_size((160, gh)).set_content_align('c')
                        TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align('c')
                        TextBox("RT",  title_style).set_bg(bg1).set_size((180, gh)).set_content_align('c')
                    for i, rank in enumerate(ranks):
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_padding(0):
                            bg = bg2 if i % 2 == 0 else bg1
                            r = get_board_rank_str(rank.rank)
                            score = get_board_score_str(rank.score)
                            rt = get_readable_datetime(rank.time, show_original_time=False, use_en_unit=False)
                            TextBox(r,          item_style, overflow='clip').set_bg(bg).set_size((140, gh)).set_content_align('r').set_padding((16, 0))
                            # TextBox(rank.name,  item_style,                ).set_bg(bg).set_size((160, gh)).set_content_align('l').set_padding((8,  0))
                            TextBox(score,      item_style, overflow='clip').set_bg(bg).set_size((180, gh)).set_content_align('r').set_padding((16, 0))
                            TextBox(rt,         item_style, overflow='clip').set_bg(bg).set_size((180, gh)).set_content_align('r').set_padding((16, 0))
            else:
                TextBox("暂无榜线数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)
    
    add_watermark(canvas)
    return await canvas.get_img()

# 合成时速图片
async def compose_sks_image(ctx: SekaiHandlerContext, unit: str, event: dict = None, period: timedelta = None) -> Image.Image:
    unit = unit[0].lower()
    assert unit in ['d', 'h', 'm']

    if period is None:
        period = timedelta(days=1) if unit == 'd' else timedelta(hours=1)
    match unit:
        case 'd': unit_period, unit_text = timedelta(days=1), "日"
        case 'h': unit_period, unit_text = timedelta(hours=1), "时"
        case 'm': unit_period, unit_text = timedelta(minutes=1), "分"

    if not event:
        event = await get_current_event(ctx, fallback="prev")
        assert_and_reply(event, "未找到当前活动")

    eid = event['id']
    title = event['name']
    event_start = datetime.fromtimestamp(event['startAt'] / 1000)
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    banner_img = await get_event_banner_img(ctx, event)
    wl_cid = await get_wl_chapter_cid(ctx, eid)

    query_ranks = SKL_QUERY_RANKS
    s_ranks = await query_first_ranking_after(ctx.region, eid, min(datetime.now(), event_end) - period, query_ranks)
    t_ranks = await get_latest_ranking(ctx, eid, query_ranks)

    speeds: List[Tuple[int, int, timedelta, datetime]] = []
    for s_rank in s_ranks:
        for t_rank in t_ranks:
            if s_rank.rank == t_rank.rank:
                speeds.append((s_rank.rank, t_rank.score, t_rank.score - s_rank.score, t_rank.time - s_rank.time, t_rank.time))
                break
    speeds.sort(key=lambda x: x[0])

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg()):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(8).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(get_event_id_and_name_text(ctx.region, eid, truncate(title, 16)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}", 
                            TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                with Frame().set_content_align('r'):
                    if banner_img:
                        ImageBox(banner_img, size=(140, None))
                    if wl_cid:
                        ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))

            if speeds:
                gh = 30
                bg1 = FillBg((255, 255, 255, 200))
                bg2 = FillBg((255, 255, 255, 100))
                title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
                item_style  = TextStyle(font=DEFAULT_FONT,      size=20, color=BLACK)
                with VSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(8):
                    
                    TextBox(f"近{get_readable_timedelta(period)}换算{unit_text}速", title_style).set_size((420, None)).set_padding((8, 8))

                    with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_padding(0):
                        TextBox("排名", title_style).set_bg(bg1).set_size((120, gh)).set_content_align('c')
                        TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align('c')
                        TextBox(f"{unit_text}速", title_style).set_bg(bg1).set_size((140, gh)).set_content_align('c')
                        TextBox("RT",  title_style).set_bg(bg1).set_size((160, gh)).set_content_align('c')
                    for i, (rank, score, dscore, dtime, rt) in enumerate(speeds):
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_padding(0):
                            bg = bg2 if i % 2 == 0 else bg1
                            r = get_board_rank_str(rank)
                            dtime = dtime.total_seconds()
                            speed = get_board_score_str(int(dscore * unit_period.total_seconds() / dtime)) if dtime > 0 else "-"
                            score = get_board_score_str(score)
                            rt = get_readable_datetime(rt, show_original_time=False, use_en_unit=False)
                            TextBox(r,          item_style, overflow='clip').set_bg(bg).set_size((120, gh)).set_content_align('r').set_padding((16, 0))
                            TextBox(score,      item_style, overflow='clip').set_bg(bg).set_size((180, gh)).set_content_align('r').set_padding((16, 0))
                            TextBox(speed,      item_style,                ).set_bg(bg).set_size((140, gh)).set_content_align('r').set_padding((8,  0))
                            TextBox(rt,         item_style, overflow='clip').set_bg(bg).set_size((160, gh)).set_content_align('r').set_padding((16, 0))
            else:
                TextBox("暂无时速数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)
    
    add_watermark(canvas)
    return await canvas.get_img()
    
# 从文本获取sk查询参数 (类型，值) 类型: 'name' 'uid' 'rank' 'ranks'
async def parse_sk_query_params(ctx: SekaiHandlerContext, args: str) -> Tuple[str, Union[str, int, List[int]]]:
    MAX_QUERY_RANKS = 20

    # 提取at
    ats = ctx.get_at_qids()
    if ats:
        uid = get_player_bind_id(ctx, ats[0], check_bind=False)
        assert_and_reply(uid, "@的用户未绑定游戏ID")
        return 'uid', uid
    
    args = args.strip()
    if not args:
        # 提取uid（管理员限定）
        if uid := get_player_bind_id(ctx, check_bind=False):
            return 'self', uid
    else:
        # 提取单个或多个多个rank
        ranks = []
        for seg in args.split():
            if not seg: continue
            if '-' in seg:
                start, end = seg.split('-', 1)
                start, end = get_rank_from_text(start), get_rank_from_text(end)
                assert_and_reply(start <= end, "查询排名范围错误: 起始排名大于结束排名")
                assert_and_reply(end - start + 1 <= MAX_QUERY_RANKS, f"最多同时查询{MAX_QUERY_RANKS}个排名")
                for rank in range(start, end + 1):
                    assert_and_reply(rank in ALL_RANKS, f"不支持的排名: {rank}")
                    ranks.append(rank)
            elif is_rank_text(seg):
                rank = get_rank_from_text(seg)
                assert_and_reply(rank in ALL_RANKS, f"不支持的排名: {rank}")
                ranks.append(rank)
        
        ranks = sorted(set(ranks))
        assert_and_reply(len(ranks) <= MAX_QUERY_RANKS, f"最多同时查询{MAX_QUERY_RANKS}个排名")
        if len(ranks) > 1:
            return 'ranks', ranks
        elif len(ranks) == 1:
            return 'rank', ranks[0]

    raise ReplyException(f"""
查询指定榜线方式：
查询自己: {ctx.original_trigger_cmd} (需要绑定游戏ID)
查询排名: {ctx.original_trigger_cmd} 100
查询多个排名: {ctx.original_trigger_cmd} 1 2 3
""".strip())
            
# 格式化sk查询参数
def format_sk_query_params(qtype: str, qval: Union[str, int, List[int]]) -> str:
    if qtype == 'self':
        return "你绑定的游戏ID"
    if qtype == 'uid':
        return "你查询的游戏ID"
    QTYPE_MAP = {
        'name': '游戏昵称',
        'rank': '排名',
        'ranks': '排名',
    }
    return f"玩家{QTYPE_MAP[qtype]}为{qval}"

# 合成榜线查询图片
async def compose_sk_image(ctx: SekaiHandlerContext, qtype: str, qval: Union[str, int, List[int]], event: dict = None) -> Image.Image:
    if not event:
        event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")

    eid = event['id']
    title = event['name']
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    wl_cid = await get_wl_chapter_cid(ctx, eid)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style1_hr = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=LinearGradient((0, 0, 150, 255), (150, 0, 100, 255), (0, 0), (1, 1)))
    style2 = TextStyle(font=DEFAULT_FONT, size=24, color=BLACK)
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=BLACK)
    style4 = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50))
    texts: List[str, TextStyle] = []

    latest_ranks = await get_latest_ranking(ctx, eid, ALL_RANKS)
    latest_ranks.sort(key=lambda x: x.rank)
    ret_ranks: List[Ranking] = []

    match qtype:
        case 'uid':
            ret_ranks = [r for r in latest_ranks if r.uid == qval]
        case 'self':
            ret_ranks = [r for r in latest_ranks if r.uid == qval]
        case 'name':
            ret_ranks = [r for r in latest_ranks if r.name == qval]
        case 'rank':
            ret_ranks = [r for r in latest_ranks if r.rank == qval]
        case 'ranks':
            ret_ranks = [r for r in latest_ranks if r.rank in qval]
        case _:
            raise ReplyException(f"不支持的查询类型: {qtype}")
    
    assert_and_reply(ret_ranks, f"找不到{format_sk_query_params(qtype, qval)}的榜线数据")

    # 查询单个
    if len(ret_ranks) == 1:
        rank = ret_ranks[0]
        texts.append((f"{truncate(rank.name, 40)}", style1_hr if check_ranking_is_high_res(ctx.region, rank) else style1))
        texts.append((f"排名 {get_board_rank_str(rank.rank)}  -  {get_board_score_str(rank.score)}", style3))
        skl_ranks = [r for r in latest_ranks if r.rank in list(range(1, 10)) + SKL_QUERY_RANKS]
        if prev_rank := find_prev_ranking(skl_ranks, rank.rank):
            dlt_score = prev_rank.score - rank.score
            texts.append((f"{prev_rank.rank}名分数: {get_board_score_str(prev_rank.score)}  ↑{get_board_score_str(dlt_score)}", style2))
        if next_rank := find_next_ranking(skl_ranks, rank.rank):
            dlt_score = rank.score - next_rank.score
            texts.append((f"{next_rank.rank}名分数: {get_board_score_str(next_rank.score)}  ↓{get_board_score_str(dlt_score)}", style2))
        texts.append((f"RT: {get_readable_datetime(rank.time, show_original_time=False)}", style4))
    # 查询多个
    else:
        for rank in ret_ranks:
            texts.append((truncate(rank.name, 40), style1_hr if check_ranking_is_high_res(ctx.region, rank) else style1))
            texts.append((f"排名 {get_board_rank_str(rank.rank)}  -  {get_board_score_str(rank.score)}", style2))
            texts.append((f"RT: {get_readable_datetime(rank.time, show_original_time=False)}", style4))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(fill=SK_TEXT_QUERY_BG_COLOR)):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(8).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(get_event_id_and_name_text(ctx.region, eid, truncate(title, 20)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                if wl_cid:
                    ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))
        
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(6).set_padding(16):
                for text, style in texts:
                    TextBox(text, style)
    
    add_watermark(canvas)
    return await canvas.get_img(1.5)

# 合成查房图片
async def compose_cf_image(ctx: SekaiHandlerContext, qtype: str, qval: Union[str, int], event: dict = None) -> Image.Image:
    if not event:
        event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")

    eid = event['id']
    title = event['name']
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    wl_cid = await get_wl_chapter_cid(ctx, eid)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style1_hr = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=LinearGradient((0, 0, 150, 255), (150, 0, 100, 255), (0, 0), (1, 1)))
    style2 = TextStyle(font=DEFAULT_FONT, size=24, color=BLACK)
    style3 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    style4 = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50))
    texts: List[str, TextStyle] = []

    ranks, ranks_list = [], None
    latest_ranks = await get_latest_ranking(ctx, eid, ALL_RANKS)
    cf_start_time = latest_ranks[0].time - timedelta(hours=1)
    skl_ranks = [r for r in latest_ranks if r.rank in list(range(1, 10)) + SKL_QUERY_RANKS]

    match qtype:
        case 'self':
            ranks = await query_ranking(ctx.region, eid, uid=qval, start_time=cf_start_time)
        case 'uid':
            ranks = await query_ranking(ctx.region, eid, uid=qval, start_time=cf_start_time)
        case 'name':
            ranks = await query_ranking(ctx.region, eid, name=qval, start_time=cf_start_time)
        case 'rank':
            r = find_by_predicate(latest_ranks, lambda x: x.rank == qval)
            assert_and_reply(r, f"找不到排名 {qval} 的榜线数据")
            ranks = await query_ranking(ctx.region, eid, uid=r.uid, start_time=cf_start_time)
        case 'ranks':
            uid_list = []
            for rank in qval:
                r = find_by_predicate(latest_ranks, lambda x: x.rank == rank)
                assert_and_reply(r, f"找不到排名 {rank} 的榜线数据")
                uid_list.append(r.uid)
            ranks_list = await batch_gather(*[query_ranking(ctx.region, eid, uid=uid, start_time=cf_start_time) for uid in uid_list])
        case _:
            raise ReplyException(f"不支持的查询类型: {qtype}")

    def calc(ranks: List[Ranking]) -> Dict[str, float]:
        if not ranks:
            return { 'status': 'no_found' }

        pts = []
        abnormal = False
        tolerance = timedelta(seconds=SK_RECORD_TOLERANCE_CFG.get())
        if ranks[0].time - cf_start_time > tolerance:
            abnormal = True
        for i in range(len(ranks) - 1):
            if ranks[i + 1].score != ranks[i].score:
                pts.append(ranks[i + 1].score - ranks[i].score)
            if ranks[i + 1].time - ranks[i].time > tolerance:
                abnormal = True
        
        ret = {
            'status': 'ok',
            'abnormal': abnormal,
            'name': truncate(ranks[-1].name, 40),
            'uid': ranks[-1].uid,
            'last_rank_item': ranks[-1],
            'cur_rank': ranks[-1].rank,
            'cur_score': ranks[-1].score,
            'start_time': ranks[0].time,
            'end_time': ranks[-1].time,
            'hour_speed': int((ranks[-1].score - ranks[0].score) / (ranks[-1].time - ranks[0].time).total_seconds() * 3600),
            'last_pt': pts[-1] if pts else 0,
            'avg_pt_n': min(10, len(pts)),
            'avg_pt': sum(pts[-min(10, len(pts)):]) / min(10, len(pts)) if pts else 0,
            'pts': pts,
        }
        if last_20min_rank := find_by_predicate(ranks, lambda x: x.time <= ranks[-1].time - timedelta(minutes=20), mode='last'):
            ret['last_20min_speed'] = int((ranks[-1].score - last_20min_rank.score) / (ranks[-1].time - last_20min_rank.time).total_seconds() * 3600)
        if prev_rank := find_prev_ranking(skl_ranks, ret['cur_rank']):
            ret['prev_score'] = prev_rank.score
            ret['prev_rank'] = prev_rank.rank
            ret['prev_dlt'] = prev_rank.score - ret['cur_score']
        if next_rank := find_next_ranking(skl_ranks, ret['cur_rank']):
            ret['next_score'] = next_rank.score
            ret['next_rank'] = next_rank.rank
            ret['next_dlt'] = ret['cur_score'] - next_rank.score
        return ret
    
    def get_ms_emoji(d):
        return '🏡' if len(d['pts']) > SK_PLAYCOUNT_MYSEKAI_THRESHOLD else ''

    if ranks_list is None:
        # 单个
        d = calc(ranks)
        assert_and_reply(d['status'] != 'no_found', f"找不到{format_sk_query_params(qtype, qval)}的榜线数据")
        assert_and_reply(d['status'] != 'no_enough', f"{format_sk_query_params(qtype, qval)}的最近游玩次数少于1，无法查询")
        texts.append((f"{d['name']}", style1_hr if check_ranking_is_high_res(ctx.region, ranks[-1]) else style1))
        texts.append((f"排名 {get_board_rank_str(d['cur_rank'])}  -  {get_board_score_str(d['cur_score'])}", style2))
        if 'prev_rank' in d:
            texts.append((f"{d['prev_rank']}名分数: {get_board_score_str(d['prev_score'])}  ↑{get_board_score_str(d['prev_dlt'])}", style3))
        if 'next_rank' in d:
            texts.append((f"{d['next_rank']}名分数: {get_board_score_str(d['next_score'])}  ↓{get_board_score_str(d['next_dlt'])}", style3))
        if d['avg_pt_n'] > 0:
            texts.append((f"近{d['avg_pt_n']}次平均Pt: {d['avg_pt']:.0f}", style2))
            texts.append((f"最近一次Pt: {d['last_pt']}", style2))
            texts.append((f"时速: {get_board_score_str(d['hour_speed'])}", style2))
            if 'last_20min_speed' in d:
                texts.append((f"20min×3时速: {get_board_score_str(d['last_20min_speed'])}", style2))
            texts.append((f"最近一小时内Pt变化次数: {len(d['pts'])} {get_ms_emoji(d)}", style2))
        else:
            texts.append((f"停车中💤", style2))
        if d['abnormal']:
            texts.append((f"记录时间内有数据空缺，周回数仅供参考", style4.replace(color=(200, 0, 0))))
        texts.append((f"RT: {get_readable_datetime(d['start_time'], show_original_time=False)} ~ {get_readable_datetime(d['end_time'], show_original_time=False)}", style4))
    else:
        # 多个
        ds = [calc(ranks) for ranks in ranks_list]
        for i, d in enumerate(ds):
            if d['status'] == 'no_found':
                texts.append((f"找不到{format_sk_query_params('rank', qval[i])}的榜线数据", style1))
                continue
            if d['status'] == 'no_enough':
                texts.append((f"{format_sk_query_params('rank', qval[i])}的最近游玩次数少于1，无法查询", style1))
                continue
            texts.append((f"{d['name']}", style1_hr if check_ranking_is_high_res(ctx.region, d['last_rank_item']) else style1))
            texts.append((f"排名 {get_board_rank_str(d['cur_rank'])}  -  {get_board_score_str(d['cur_score'])}", style2))
            if d['avg_pt_n'] > 0:
                texts.append((f"时速: {get_board_score_str(d['hour_speed'])} 近{d['avg_pt_n']}次平均Pt: {d['avg_pt']:.0f}", style2))
                texts.append((f"最近一小时内Pt变化次数: {len(d['pts'])} {get_ms_emoji(d)}", style2))
            else:
                texts.append((f"停车中💤", style2))
            if d['abnormal']:
                texts.append((f"记录时间内有数据空缺，周回数仅供参考", style4.replace(color=(200, 0, 0))))
            texts.append((f"RT: {get_readable_datetime(d['start_time'], show_original_time=False)} ~ {get_readable_datetime(d['end_time'], show_original_time=False)}", style4))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(fill=SK_TEXT_QUERY_BG_COLOR)):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(8).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(get_event_id_and_name_text(ctx.region, eid, truncate(title, 20)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                if wl_cid:
                    ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))
        
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(6).set_padding(16):
                for text, style in texts:
                    TextBox(text, style)
    
    add_watermark(canvas)
    return await canvas.get_img(1.5)

# 合成查水表图片
async def compose_csb_image(ctx: SekaiHandlerContext, qtype: str, qval: Union[str, int], event: dict = None) -> Image.Image:
    if not event:
        event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")

    eid = event['id']
    title = event['name']
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    wl_cid = await get_wl_chapter_cid(ctx, eid)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style1_hr = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=LinearGradient((0, 0, 150, 255), (150, 0, 100, 255), (0, 0), (1, 1)))
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    style3 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    texts: List[str, TextStyle] = []

    ranks = []
    
    match qtype:
        case 'self':
            ranks = await query_ranking(ctx.region, eid, uid=qval)
        case 'uid':
            ranks = await query_ranking(ctx.region, eid, uid=qval)
        case 'name':
            ranks = await query_ranking(ctx.region, eid, name=qval)
        case 'rank':
            latest_ranks = await get_latest_ranking(ctx, eid, ALL_RANKS)
            r = find_by_predicate(latest_ranks, lambda x: x.rank == qval)
            assert_and_reply(r, f"找不到排名 {qval} 的榜线数据")
            ranks = await query_ranking(ctx.region, eid, uid=r.uid)
        case 'ranks':
            raise ReplyException("查水表不支持同时查询多个玩家")
        case _:
            raise ReplyException(f"不支持的查询类型: {qtype}")

    if not ranks:
        raise ReplyException(f"找不到{format_sk_query_params(qtype, qval)}的榜线数据")

    # ================== 各小时周回数据 ================== #

    rankcounts: list[list[int]] = []
    playcounts: list[list[int]] = []
    abnormals: list[list[bool]] = []
    start_date = ranks[0].time.date()
    for i in range(len(ranks) - 1):
        cur, nxt = ranks[i], ranks[i + 1]
        lst = ranks[i - 1] if i - 1 >= 0 else None
        day = (cur.time.date() - start_date).days
        while len(rankcounts) <= day:
            rankcounts.append([0 for _ in range(24)])
            playcounts.append([0 for _ in range(24)])
            abnormals.append([False for _ in range(24)])
        hour = cur.time.hour
        rankcounts[day][hour] += 1
        if nxt.score > cur.score:
            playcounts[day][hour] += 1
        # 判断数据异常
        tolerance = timedelta(seconds=SK_RECORD_TOLERANCE_CFG.get())
        def check_abnormal(left: datetime, right: datetime):
            if right - left > tolerance:
                abnormals[day][hour] = True
        if lst and cur.time.hour != lst.time.hour:  
            check_abnormal(lst.time, cur.time)
        check_abnormal(cur.time, nxt.time)

    # ================== 停车区间 ================== #

    segs: list[tuple[Ranking, Ranking]] = []
    l, r = None, None
    for rank in ranks:
        if not l: l = rank
        if not r: r = rank
        # 如果掉出100（排名大于100或数据缺失过长），提前结算当前区间
        if rank.rank > 100 or rank.time - r.time > timedelta(seconds=SK_RECORD_TOLERANCE_CFG.get()):
            if l != r:
                segs.append((l, r))
            l, r = rank, None
        # 如果分数出现变化，提前结算当前区间
        elif rank.score != r.score:
            if l != r:
                segs.append((l, r))
            l, r = rank, None
        # 否则认为正在停车，更新右边界
        else:
            r = rank
    if l and r:
        segs.append((l, r))
    
    texts.append((f"T{ranks[-1].rank} \"{ranks[-1].name}\" 的停车区间", style1_hr if check_ranking_is_high_res(ctx.region, ranks[-1]) else style1))
    for l, r in segs:
        if l == r:
            continue
        if r.time - l.time < timedelta(minutes=config.get('sk.csb_judge_stop_threshold_minutes')):
            continue
        start = l.time.strftime('%m-%d %H:%M')
        end = r.time.strftime('%m-%d %H:%M')
        duration = get_readable_timedelta(r.time - l.time)
        texts.append((f"{start} ~ {end}（{duration}）", style2))
    if len(texts) == 1:
        texts.append((f"未找到停车区间", style2))
    row_num = len(texts) // 2 + 1
    first_text = texts[0]
    left_texts = texts[1:row_num]
    right_texts = texts[row_num:]

    HEAT_COLOR_MIN = color_code_to_rgb('#B8D8FF')
    HEAT_COLOR_MAX = color_code_to_rgb('#FFB5B5')
    HEAT_COLOR_MYSEKAI = color_code_to_rgb('#CCFFCC')

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(fill=SK_TEXT_QUERY_BG_COLOR)):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(8).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(get_event_id_and_name_text(ctx.region, eid, truncate(title, 20)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                if wl_cid:
                    ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(6).set_padding(16):
                TextBox(f"T{ranks[-1].rank} \"{ranks[-1].name}\" 各小时Pt变化次数", style1_hr if check_ranking_is_high_res(ctx.region, ranks[-1]) else style1)
                TextBox(f"标注*号的小时有数据缺失，周回数可能不准确", style2)
                with Grid(col_count=24, hsep=1, vsep=1):
                    for i in range(0, 24):
                        TextBox(f"{i}", TextStyle(font=DEFAULT_FONT, size=12, color=BLACK)) \
                            .set_content_align('c').set_size((30, 30))
                    for day in range(len(rankcounts)):
                        for hour in range(0, 24):
                            playcount, rankcount, abnormal = playcounts[day][hour], rankcounts[day][hour], abnormals[day][hour]
                            if rankcount < 10:
                                Spacer(w=24, h=24)
                            else:
                                playcount_text = str(playcount)
                                if abnormal:
                                    playcount_text += "*"
                                if playcount > SK_PLAYCOUNT_MYSEKAI_THRESHOLD:
                                    color = HEAT_COLOR_MYSEKAI
                                else:
                                    color = lerp_color(HEAT_COLOR_MIN, HEAT_COLOR_MAX, max(min((playcount - 15) / 15, 1.0), 0.0))
                                TextBox(playcount_text, TextStyle(font=DEFAULT_FONT, size=14, color=BLACK), overflow='clip') \
                                    .set_bg(RoundRectBg(color, radius=4)).set_content_align('c').set_size((30, 30)).set_offset((0, -2))
        
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(6).set_padding(16):
                TextBox(*first_text)
                with HSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                        for text in left_texts:
                            TextBox(*text)
                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                        for text in right_texts:
                            TextBox(*text)
    
    add_watermark(canvas)
    return await canvas.get_img(1.5 if len(texts) < 10 else 1.0)

# 合成玩家追踪图片
async def compose_player_trace_image(ctx: SekaiHandlerContext, qtype: str, qval: Union[str, int], event: dict = None) -> Image.Image:
    if not event:
        event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")
    eid = event['id']
    wl_cid = await get_wl_chapter_cid(ctx, eid)
    ranks, ranks2 = [], None
    latest_ranks = await get_latest_ranking(ctx, eid, ALL_RANKS)

    match qtype:
        case 'self':
            ranks = await query_ranking(ctx.region, eid, uid=qval)
        case 'uid':
            ranks = await query_ranking(ctx.region, eid, uid=qval)
        case 'name':
            ranks = await query_ranking(ctx.region, eid, name=qval)
        case 'rank':
            r = find_by_predicate(latest_ranks, lambda x: x.rank == qval)
            assert_and_reply(r, f"找不到排名 {qval} 的榜线数据")
            ranks = await query_ranking(ctx.region, eid, uid=r.uid)
        case 'ranks':
            assert_and_reply(len(qval) == 2, "最多同时对比两个玩家的追踪数据")
            v1, v2 = qval
            r = find_by_predicate(latest_ranks, lambda x: x.rank == v1)
            assert_and_reply(r, f"找不到排名 {v1} 的榜线数据")
            ranks = await query_ranking(ctx.region, eid, uid=r.uid)
            r = find_by_predicate(latest_ranks, lambda x: x.rank == v2)
            assert_and_reply(r, f"找不到排名 {v2} 的榜线数据")
            ranks2 = await query_ranking(ctx.region, eid, uid=r.uid)
        case _:
            raise ReplyException(f"不支持的查询类型: {qtype}")
        
    ranks = [r for r in ranks if r.rank <= 100]
    if ranks2 is not None:
        ranks2 = [r for r in ranks2 if r.rank <= 100]
        
    if len(ranks) < 1:
        raise ReplyException(f"{format_sk_query_params(qtype, qval)}的榜线记录过少，无法查询")
    if ranks2 is not None and len(ranks2) < 1:
        raise ReplyException(f"{format_sk_query_params(qtype, qval)}的榜线记录过少，无法查询")
    
    point_num_limit = config.get('sk.plot_point_num_limit')
    ranks.sort(key=lambda x: x.time)
    ranks = sample_ranking_list(ranks, point_num_limit)
    if ranks2 is not None:
        ranks2.sort(key=lambda x: x.time)
        ranks2 = sample_ranking_list(ranks2, point_num_limit)

    name = truncate(ranks[-1].name, 40)
    times = [rank.time for rank in ranks]
    scores = [rank.score for rank in ranks]
    rs = [rank.rank for rank in ranks]
    if ranks2 is not None:
        ranks2.sort(key=lambda x: x.time)
        name2 = truncate(ranks2[-1].name, 40)
        times2 = [rank.time for rank in ranks2]
        scores2 = [rank.score for rank in ranks2]
        rs2 = [rank.rank for rank in ranks2]

    def draw_graph() -> Image.Image:
        fig, ax1 = plt.subplots()
        ax2 = ax1.twinx()
        fig.set_size_inches(12, 8)
        fig.subplots_adjust(wspace=0, hspace=0)

        min_score = min(scores)
        max_score = max(scores) 
        if ranks2 is not None:
            min_score = min(min_score, min(scores2))
            max_score = max(max_score, max(scores2))

        color_p1 = ('royalblue', 'cornflowerblue')
        color_p2 = ('orangered', 'coral')

        # 绘制分数
        ax2.plot(times, scores, 'o', color=color_p1[0], markersize=1, linewidth=0.5)
        ax2.plot([], [], '-', label=f'[{name}] 分数', color=color_p1[0], linewidth=2)
        plt.annotate(f"{get_board_score_str(scores[-1])}", xy=(times[-1], scores[-1]), xytext=(times[-1], scores[-1]), 
                     color=color_p1[0], fontsize=12, ha='right')
        if ranks2 is not None:
            ax2.plot(times2, scores2, 'o', color=color_p2[0], markersize=1, linewidth=0.5)
            ax2.plot([], [], '-', label=f'[{name2}] 分数', color=color_p2[0], linewidth=2)
            plt.annotate(f"{get_board_score_str(scores2[-1])}", xy=(times2[-1], scores2[-1]), xytext=(times2[-1], scores2[-1]),
                            color=color_p2[0], fontsize=12, ha='right')

        ax2.set_ylim(min_score * 0.95, max_score * 1.05)
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: get_board_score_str(x, precise=False)))
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()

        # 绘制排名
        ax1.plot(times, rs, 'o', color=color_p1[1], markersize=0.5, linewidth=0.5)
        ax2.plot([], [], '-', label=f'[{name}] 排名', color=color_p1[1], linewidth=1)
        if ranks2 is not None:
            ax1.plot(times2, rs2, 'o', color=color_p2[1], markersize=0.5, linewidth=0.5)
            ax2.plot([], [], '-', label=f'[{name2}] 排名', color=color_p2[1], linewidth=1)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: str(int(x)) if 1 <= int(x) <= 100 else ''))
        ax1.set_ylim(110, -10)

        # 标签
        ax2.legend(loc='upper left')

        # 网格
        ax1.xaxis.grid(True, linestyle='-', alpha=0.3, color='gray')
        ax2.yaxis.grid(True, linestyle='-', alpha=0.3, color='gray')

        # 背景
        draw_daynight_bg(ax1, times[0], times[-1])
        
        if ranks2 is None:
            plt.title(f"{get_event_id_and_name_text(ctx.region, eid, '')} 玩家: [{name}] (T{ranks[-1].rank})")
        else:
            plt.title(f"{get_event_id_and_name_text(ctx.region, eid, '')} 玩家: [{name}] (T{ranks[-1].rank})  vs [{name2}] (T{ranks2[-1].rank})")

        return plt_fig_to_image(fig, tight=True)
    
    img = await run_in_pool(draw_graph)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        ImageBox(img).set_bg(roundrect_bg(fill=(255, 255, 255, 200))).set_padding(16)
        if wl_cid:
            with VSplit().set_content_align('c').set_item_align('c').set_sep(4).set_bg(roundrect_bg()).set_padding(8):
                ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))
                TextBox("单榜", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK))
    add_watermark(canvas)
    return await canvas.get_img()

# 合成排名追踪图片
async def compose_rank_trace_image(ctx: SekaiHandlerContext, rank: int, event: dict = None) -> Image.Image:
    if not event:
        event = await get_current_event(ctx, fallback="prev")
    assert_and_reply(event, "未找到当前活动")
    eid = event['id']
    wl_cid = await get_wl_chapter_cid(ctx, eid)
    ranks = []

    ranks = await query_ranking(ctx.region, eid, rank=rank)
    if len(ranks) < 1:
        raise ReplyException(f"指定排名为{rank}榜线记录过少，无法查询")
   
    ranks.sort(key=lambda x: x.time)
    point_num_limit = config.get('sk.plot_point_num_limit')
    ranks = sample_ranking_list(ranks, point_num_limit)

    times = [rank.time for rank in ranks]
    scores = [rank.score for rank in ranks]
    uids = [rank.uid for rank in ranks]

    # 时速计算
    speeds = []
    min_period = timedelta(minutes=50)
    max_period = timedelta(minutes=60)
    left = 0
    for right in range(0, len(ranks)):
        while ranks[right].time - ranks[left].time > max_period:
            left += 1
        if min_period <= ranks[right].time - ranks[left].time <= max_period:
            speed = (ranks[right].score - ranks[left].score) / (ranks[right].time - ranks[left].time).total_seconds() * 3600
            speeds.append(speed)
        else:
            speeds.append(-1)
    
    # 附加排名预测
    forecasts = await get_forecast_data(ctx.region, eid % 1000, eid // 1000)
    forecasts = {
        f.source: f.rank_data[rank] 
        for f in forecasts 
        if f and f.rank_data and rank in f.rank_data
    }

    def get_unique_colors(n: int) -> list:
        num_part1 = n // 2
        num_part2 = n - num_part1
        colors1 = cm.nipy_spectral(np.linspace(0.0, 0.3, num_part1))
        colors2 = cm.nipy_spectral(np.linspace(0.75, 0.95, num_part2))
        if n > 0:
            combined_colors = np.vstack((colors1, colors2))
            np.random.shuffle(combined_colors)
        else:
            combined_colors = []
        return combined_colors

    def pixel_size_to_data_size(ax, pixel_size: int) -> float:
        # 将像素大小转换为数据坐标大小
        fig = ax.get_figure()
        dpi = fig.dpi
        phys_size = pixel_size * dpi / 72
        inv = ax.transData.inverted()
        data_size = abs(inv.transform((0, phys_size))[1] - inv.transform((0, 0))[1])
        return data_size

    def draw_nocollide_texts(ax, texts: list[str], colors: list, x: float, y_positions: list[float], fontsize: int, ha: str, va: str):
        objs = list(zip(texts, colors, y_positions))
        objs.sort(key=lambda item: item[2], reverse=True)
        last_y = float('inf')
        for i in range(len(objs)):
            text, color, y = objs[i]
            height = pixel_size_to_data_size(ax, fontsize)
            if last_y - y < height:
                new_y = last_y - height * 1.1
                # 避免和线本身冲突
                if va == 'bottom' and y - new_y < height:
                    new_y = y - height * 1.1
                y = new_y
            ax.text(x, y, text, color=color, fontsize=fontsize, ha=ha, va=va, transform=ax.get_yaxis_transform())  
            last_y = y

    def draw_graph() -> Image.Image:
        max_score = max(scores)
        for f in forecasts.values():
            if f.final_score:
                max_score = max(max_score, f.final_score)
            if f.history_final_score:
                hist_scores = [x.score for x in f.history_final_score]
                if hist_scores:
                    max_score = max(max_score, min(f.final_score * 1.1, max(hist_scores)))

        fig, ax1 = plt.subplots()
        ax2 = ax1.twinx()
        fig.set_size_inches(12, 8)
        fig.subplots_adjust(wspace=0, hspace=0)

        unique_uids = sorted(list(set(uids)))
        num_unique_uids = len(unique_uids)
        if num_unique_uids > 20:
            # 数量太多，直接使用同一个颜色
            point_colors = ['blue' for _ in uids]
        else:
            # 为每个uid分配一个独特的、非绿色的深色
            unique_colors = get_unique_colors(num_unique_uids)
            uid_to_color = {uid: color for uid, color in zip(unique_uids, unique_colors)}
            point_colors = [uid_to_color.get(uid) for uid in uids]

        # 绘制分数，为不同uid的数据点使用不同颜色
        ax2.scatter(times, scores, c=point_colors, s=2)
        ax2.plot([], [], label='分数', color='blue', linestyle='-', linewidth=2)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: get_board_score_str(int(x), precise=False)))
        ax2.set_ylim(0, max_score * 1.1)
        fig.autofmt_xdate()
        if scores: # 当前分数
            plt.annotate(f"{get_board_score_str(scores[-1])}", 
                        xy=(times[-1], scores[-1]), xytext=(times[-1], scores[-1]),
                        color=point_colors[-1], fontsize=12, ha='right', va='bottom')
        
        # 绘制时速
        ax1.plot(times, speeds, 'o', color='green', markersize=0.5, linewidth=0.5)
        ax2.plot([], [], label='时速', color='green', linestyle=':', linewidth=2)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: get_board_score_str(int(x), precise=False) + "/h"))
        ax1.set_ylim(0, max(speeds) * 1.2)

        # 绘制预测
        colors = list(mcolors.TABLEAU_COLORS.values())
        final_score_texts, final_score_ys, final_score_colors = [], [], []
        for i, (source, f) in enumerate(forecasts.items()):
            name = config.get(f'sk.forecast.{source}.name')
            color = colors[i % len(colors)]
            # 最终预测线
            if f.final_score:
                ax2.axhline(y=f.final_score, color=color, linestyle='--', linewidth=0.8, alpha=0.7)
                score = round(f.final_score / 10000) * 10000
                final_score_texts.append(f"{name}: {get_board_score_str(score, precise=False)}")
                # final_score_ys.append(f.final_score)
                final_score_ys.append(max_score + f.final_score / 10000)
                final_score_colors.append(color)
            # 预测历史
            if config.get(f'sk.forecast.{source}.show_history') and f.history_final_score:
                history = [(datetime.fromtimestamp(x.ts), x.score) for x in f.history_final_score]
                history_times = [x[0] for x in history]
                history_preds = [x[1] for x in history]
                ax2.plot(history_times, history_preds, color=color, linestyle='-', linewidth=1.0, alpha=1.0)
                ax2.plot(history_times[-1], history_preds[-1], marker='x', color=color, markersize=6, alpha=1.0)
                ax2.plot([], [], label=f'{name}历史', color=color, linestyle='-', linewidth=2)
        # 统一绘制最终预测线对应的文本，避免重叠
        draw_nocollide_texts(
            ax2, final_score_texts, final_score_colors,
            1.0, final_score_ys,
            10, 'right', 'bottom'
        )
        
        # 标签
        ax2.legend(loc='upper left')

        # 网格
        ax1.xaxis.grid(True, linestyle='-', alpha=0.3, color='gray')
        ax2.yaxis.grid(True, linestyle='-', alpha=0.3, color='gray')

        # 背景
        draw_daynight_bg(ax1, times[0], times[-1])

        plt.title(f"{get_event_id_and_name_text(ctx.region, eid, '')} T{rank} 分数线")
        return plt_fig_to_image(fig, tight=True)
    
    img = await run_in_pool(draw_graph)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        ImageBox(img).set_bg(roundrect_bg(fill=(255, 255, 255, 200))).set_padding(16)
        if wl_cid:
            with VSplit().set_content_align('c').set_item_align('c').set_sep(4).set_bg(roundrect_bg()).set_padding(8):
                ImageBox(get_chara_icon_by_chara_id(wl_cid), size=(None, 50))
                TextBox("单榜", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK))
    add_watermark(canvas)
    return await canvas.get_img()

# 获取胜率预测数据
async def get_winrate_predict_data(ctx: SekaiHandlerContext):
    assert ctx.region == 'jp', "5v5胜率预测仅支持日服"
    data = await download_json("https://sekai-data.3-3.dev/cheerful_predict.json")
    try:
        event_id = data['eventId']
        predict_time = datetime.fromtimestamp(data['timestamp'] / 1000)
        recruiting = {}
        for team_id, status in data['status'].items():
            recruiting[int(team_id)] = (status == "recruite")
        predict_rates = {}
        for team_id, rate in data['predictRates'].items():
            predict_rates[int(team_id)] = rate
        return PredictWinrate(
            event_id=event_id,
            predict_time=predict_time,
            recruiting=recruiting,
            predict_rates=predict_rates,
        )
    except Exception as e:
        raise Exception(f"解析5v5胜率数据失败: {get_exc_desc(e)}")

# 合成5v5胜率预测图片
async def compose_winrate_predict_image(ctx: SekaiHandlerContext) -> Image.Image:
    predict = await get_winrate_predict_data(ctx)

    eid = predict.event_id
    event = await ctx.md.events.find_by_id(eid)
    banner_img = await get_event_banner_img(ctx, event)

    event_name = event['name']
    event_start = datetime.fromtimestamp(event['startAt'] / 1000)
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)

    teams = await ctx.md.cheerful_carnival_teams.find_by('eventId', eid, mode='all')
    assert_and_reply(len(teams) == 2, "未找到5v5活动数据")
    teams.sort(key=lambda x: x['id'])
    tids = [team['id'] for team in teams]
    tnames = [team['teamName'] for team in teams]
    for i in range(2):
        if tname_cn := await translate_text(tnames[i]):
            tnames[i] = f"{tnames[i]} ({tname_cn})"
    ticons = [
        await ctx.rip.img(f"event/{event['assetbundleName']}/team_image/{team['assetbundleName']}.png")
        for team in teams
    ]

    win_tid = tids[0] if predict.predict_rates[tids[0]] >= predict.predict_rates[tids[1]] else tids[1]

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
            with HSplit().set_content_align('rt').set_item_align('rt').set_padding(16).set_sep(7):
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5):
                    TextBox(f"【{ctx.region.upper()}-{eid}】{truncate(event_name, 20)}", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}", 
                            TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))
                    time_to_end = event_end - datetime.now()
                    if time_to_end.total_seconds() <= 0:
                        TextBox(f"预测的活动已结束！", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=RED))
                    else:
                        TextBox(f"距离活动结束还有{get_readable_timedelta(time_to_end)}", 
                                TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(f"预测更新时间: {predict.predict_time.strftime('%m-%d %H:%M:%S')} ({get_readable_datetime(predict.predict_time, show_original_time=False)})",
                            TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox("数据来源: 3-3.dev", TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50, 255)))
                if banner_img:
                    ImageBox(banner_img, size=(140, None))

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_item_bg(roundrect_bg()):
                for i in range(2):
                    with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(16):
                        ImageBox(ticons[i], size=(None, 100))
                        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                            TextBox(tnames[i], TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=BLACK), use_real_line_count=True).set_w(400)
                            with HSplit().set_content_align('lb').set_item_align('lb').set_sep(8).set_padding(0):
                                TextBox(f"预测胜率: ", TextStyle(font=DEFAULT_FONT, size=28, color=(75, 75, 75, 255)))
                                TextBox(f"{predict.predict_rates.get(tids[i]) * 100.0:.1f}%",
                                        TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=(25, 100, 25, 255) if win_tid == tids[i] else (100, 25, 25, 255)))
                                TextBox("（急募中）" if predict.recruiting.get(tids[i]) else "", 
                                        TextStyle(font=DEFAULT_FONT, size=28, color=(100, 25, 75, 255)))
                            
    add_watermark(canvas)
    return await canvas.get_img(2.)


# ======================= 指令处理 ======================= #

# 查询榜线预测
pjsk_skp = SekaiCmdHandler([
    "/pjsk sk predict", "/pjsk board predict",
    "/sk预测", "/榜线预测", "/skp",
], prefix_args=['', 'wl'])
pjsk_skp.check_cdrate(cd).check_wblist(gbl)
@pjsk_skp.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)
    assert_and_reply(not wl_event, "榜线预测不支持WL单榜")

    return await ctx.asend_msg(await get_image_cq(
        await compose_skp_image(ctx),
        low_quality=True,
    ))


# 查询整体榜线
pjsk_skl = SekaiCmdHandler([
    "/pjsk sk line", "/pjsk board line",
    "/sk线", "/skl", "/榜线",
], prefix_args=['', 'wl'])
pjsk_skl.check_cdrate(cd).check_wblist(gbl)
@pjsk_skl.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    full = False
    if any(x in args for x in ["full", "all", "全部"]):
        full = True
        args = args.replace("full", "").replace("all", "").replace("全部", "").strip()

    if args:
        raise ReplyException(f"已不支持查询往期榜线")
        try: event = await parse_search_single_event_args(ctx, args)
        except:
            return await ctx.asend_reply_msg(f"""
参数错误，查询指定活动榜线：
1. 指定活动ID: {ctx.original_trigger_cmd} 123
2. 指定活动倒数序号: {ctx.original_trigger_cmd} -1
3. 指定箱活: {ctx.original_trigger_cmd} mnr1
""".strip())
    else:
        event = None

    return await ctx.asend_msg(await get_image_cq(
        await compose_skl_image(ctx, wl_event or event, full),
        low_quality=True,
    ))


# 查询时速
pjsk_sks = SekaiCmdHandler([
    "/pjsk sk speed", "/pjsk board speed",
    "/时速", "/sks", "/skv", "/sk时速",
], prefix_args=['', 'wl'])
pjsk_sks.check_cdrate(cd).check_wblist(gbl)
@pjsk_sks.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    period = timedelta(minutes=60)
    try: period = timedelta(minutes=int(args))
    except: pass

    return await ctx.asend_msg(await get_image_cq(
        await compose_sks_image(ctx, unit='h', event=wl_event, period=period),
        low_quality=True,
    ))


# 查询日速
pjsk_skds = SekaiCmdHandler([
    "/pjsk sk daily speed", "/pjsk board daily speed",
    "/日速", "/skds", "/skdv", "/sk日速",
], prefix_args=['', 'wl'])
pjsk_skds.check_cdrate(cd).check_wblist(gbl)
@pjsk_skds.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    period = timedelta(days=1)
    try: period = timedelta(days=int(args))
    except: pass

    return await ctx.asend_msg(await get_image_cq(
        await compose_sks_image(ctx, unit='d', event=wl_event, period=period),
        low_quality=True,
    ))


# 查询指定榜线
pjsk_sk = SekaiCmdHandler([
    "/pjsk sk board", "/pjsk board",
    "/sk", 
], prefix_args=['', 'wl'])
pjsk_sk.check_cdrate(cd).check_wblist(gbl)
@pjsk_sk.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    qtype, qval = await parse_sk_query_params(ctx, args)
    return await ctx.asend_msg(await get_image_cq(
        await compose_sk_image(ctx, qtype, qval, event=wl_event),
        low_quality=True,
    ))
    

# 查房
pjsk_cf = SekaiCmdHandler([
    "/cf", "/查房", "/pjsk查房",
], prefix_args=['', 'wl'])
pjsk_cf.check_cdrate(cd).check_wblist(gbl)
@pjsk_cf.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    qtype, qval = await parse_sk_query_params(ctx, args)
    return await ctx.asend_msg(await get_image_cq(
        await compose_cf_image(ctx, qtype, qval, event=wl_event),
        low_quality=True,
    ))


# 查水表
pjsk_csb = SekaiCmdHandler([
    "/csb", "/查水表", "/pjsk查水表", "/停车时间",
], prefix_args=['', 'wl'])
pjsk_csb.check_cdrate(cd).check_wblist(gbl)
@pjsk_csb.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    qtype, qval = await parse_sk_query_params(ctx, args)
    return await ctx.asend_msg(await get_image_cq(
        await compose_csb_image(ctx, qtype, qval, event=wl_event),
        low_quality=True,
    ))


# 玩家追踪
pjsk_ptr = SekaiCmdHandler([
    "/ptr", "/玩家追踪", "/pjsk玩家追踪",
], prefix_args=['', 'wl'])
pjsk_ptr.check_cdrate(cd).check_wblist(gbl)
@pjsk_ptr.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    qtype, qval = await parse_sk_query_params(ctx, args)
    return await ctx.asend_msg(await get_image_cq(
        await compose_player_trace_image(ctx, qtype, qval, event=wl_event),
        low_quality=True,
    ))


# 分数线追踪
pjsk_rtr = SekaiCmdHandler([
    "/rtr", "/skt", "/追踪", "/pjsk追踪", 
    "/sklt", "/sktl", "/分数线追踪", "/pjsk分数线追踪",
], prefix_args=['', 'wl'])
pjsk_rtr.check_cdrate(cd).check_wblist(gbl)
@pjsk_rtr.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip() + ctx.prefix_arg
    wl_event, args = await extract_wl_event(ctx, args)

    rank = get_rank_from_text(args)
    
    assert_and_reply(rank in ALL_RANKS, f"不支持的排名: {rank}")

    return await ctx.asend_msg(await get_image_cq(
        await compose_rank_trace_image(ctx, rank, event=wl_event),
        low_quality=True,
    ))


# 5v5胜率预测
pjsk_winrate = SekaiCmdHandler([
    "/pjsk winrate predict",
    "/胜率预测", "/5v5预测", "/胜率", "/5v5胜率", "/预测胜率", "/预测5v5",
], regions=['jp'])
pjsk_winrate.check_cdrate(cd).check_wblist(gbl)
@pjsk_winrate.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_msg(await get_image_cq(
        await compose_winrate_predict_image(ctx),
        low_quality=True,
    ))


# ======================= 定时任务 ======================= #

SK_COMPRESS_INTERVAL_CFG = config.item('sk.backup.interval_seconds')
SK_COMPRESS_THRESHOLD_CFG = config.item('sk.backup.threshold_days')
SK_PYBD_UPLOAD_ENABLED_CFG = config.item('sk.backup.pybd_upload')
SK_PYBD_UPLOAD_REMOTE_DIR_CFG = config.item('sk.backup.pybd_remote_dir')
SK_PYBD_VERBOSE_CFG = config.item('sk.backup.pybd_verbose')

@repeat_with_interval(SK_COMPRESS_INTERVAL_CFG, '备份榜线数据', logger)
async def compress_ranking_data():
    # 压缩过期榜线数据库
    for region in ALL_SERVER_REGIONS:
        ctx = SekaiHandlerContext.from_region(region)
        db_path = SEKAI_DATA_DIR + f"/db/sk_{region}/*_ranking.db"
        db_files = glob.glob(db_path)
        for db_file in db_files:
            zip_path = db_file + '.zip'
            if os.path.exists(zip_path):
                continue

            try:
                event_id = int(Path(db_file).stem.split('_')[0])
                wl_cid = event_id // 1000
                event_id = event_id % 1000
                event = await ctx.md.events.find_by_id(event_id)
                assert event, f"未找到活动 {event_id}"
                end_time = datetime.fromtimestamp(event['aggregateAt'] / 1000)

                # 保存已完成的榜线数据供本地预测
                if datetime.now() > end_time and not wl_cid:
                    csv_path = get_local_forecast_history_csv_path(ctx.region, event_id)
                    if not os.path.exists(csv_path):
                        await save_rankings_to_csv(ctx.region, event_id, csv_path)

                # 压缩
                if datetime.now() - end_time > timedelta(days=SK_COMPRESS_THRESHOLD_CFG.get()):
                    # 归档数据库
                    try:
                        await archive_database(ctx.region, event_id)
                    except Exception as e:
                        logger.warning(f"尝试归档榜线数据库 {db_file} 失败: {get_exc_desc(e)}")

                    def do_zip():
                        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                            zf.write(db_file, arcname=Path(db_file).name)
                    await run_in_pool(do_zip)
                    os.remove(db_file)
                    logger.info(f"已压缩榜线数据库 {db_file}")
                
            except Exception as e:
                logger.warning(f"尝试检查压缩 {db_file} 失败: {get_exc_desc(e)}")

    # 上传往期数据到百度云
    if SK_PYBD_UPLOAD_ENABLED_CFG.get():
        for region in ALL_SERVER_REGIONS:
            src_dir = SEKAI_DATA_DIR + f"/db/sk_{region}/"
            local_dir = SEKAI_DATA_DIR + f"/tmp/sk_backup_{region}"
            remote_dir = SK_PYBD_UPLOAD_REMOTE_DIR_CFG.get() + f"/{region}"
            verbose = SK_PYBD_VERBOSE_CFG.get()

            def sync():
                try:
                    src_paths = sorted(glob.glob(os.path.join(src_dir, '*.zip')))
                    if not src_paths:
                        return

                    logger.info(f'开始同步{region}的往期榜线数据到百度网盘({remote_dir})')

                    for path in src_paths:
                        dst_path = os.path.join(local_dir, os.path.basename(path))
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                        shutil.copy2(path, dst_path)
                    
                    command = [
                        'bypy',
                        'syncup',
                        local_dir,
                        remote_dir,
                        'False', '-v'
                    ]
                    process = subprocess.Popen(
                        command, 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.STDOUT, 
                        text=True,
                        encoding='utf-8'
                    )
                    while True:
                        output = process.stdout.readline()
                        if output == '' and process.poll() is not None:
                            break
                        if output and verbose:
                            logger.info(f"[bypy] {output.strip()}")
                    if process.returncode != 0:
                        raise Exception(f'bypy执行失败: code={process.returncode}')
                    
                    # 同步成功后删除往期数据
                    for path in src_paths:
                        os.remove(path)
                    
                    logger.info(f'同步{region}的往期榜线数据到百度网盘完成，成功上传 {len(src_paths)} 个文件')

                except Exception as e:
                    logger.error(f'同步{region}的往期榜线数据到百度网盘失败: {get_exc_desc(e)}')

                finally:
                    if os.path.exists(local_dir):
                        shutil.rmtree(local_dir, ignore_errors=True)
            
            await run_in_pool(sync)
        
