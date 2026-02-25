from ...utils import *
from ...llm import get_text_retriever
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..sub import SekaiUserSubHelper, SekaiGroupSubHelper
from .profile import (
    get_detailed_profile, 
    get_detailed_profile_card, 
    get_detailed_profile_card_filter,
    get_player_avatar_info_by_detailed_profile,
    get_player_avatar_info_by_basic_profile,
    get_basic_profile,
    get_basic_profile_card,
    get_player_bind_id,
)
from .event import extract_ban_event
from .resbox import get_res_icon
import rapidfuzz
import pandas as pd


music_group_sub = SekaiGroupSubHelper("music", "新曲通知", ALL_SERVER_REGIONS)
music_user_sub = SekaiUserSubHelper("music", "新曲@提醒", ALL_SERVER_REGIONS, related_group_sub=music_group_sub)
apd_group_sub = SekaiGroupSubHelper("apd", "新APD通知", ALL_SERVER_REGIONS)
apd_user_sub = SekaiUserSubHelper("apd", "新APD@提醒", ALL_SERVER_REGIONS, related_group_sub=apd_group_sub)

music_name_retriever = get_text_retriever(f"music_name") 

music_cn_titles = WebJsonRes("曲名中文翻译", "https://i18n-json.sekai.best/zh-CN/music_titles.json", update_interval=timedelta(days=1))
music_en_titles = WebJsonRes("曲名英文翻译", "https://i18n-json.sekai.best/en/music_titles.json", update_interval=timedelta(days=1))

default_musicmetas_json = WebJsonRes(
    name="MusicMeta", 
    url = config.get("deck.music_meta_url"),
    update_interval=timedelta(hours=1),
)
# 获取各个区服的music_meta_urls
music_meta_urls = config.get("deck.music_meta_urls", {})
region_musicmetas_json: dict[str, WebJsonRes] = {
    region: WebJsonRes(
        name=f"{region.upper()}-MusicMeta", 
        url = url,
        update_interval=timedelta(hours=1),
    ) for region, url in music_meta_urls.items()
}
def get_musicmetas_json(region:str)->WebJsonRes:
    return region_musicmetas_json.get(region, default_musicmetas_json)


DIFF_NAMES = [
    ("easy", "Easy", "EASY", "ez", "EZ"),
    ("normal", "Normal", "NORMAL", "nm", "NM", ), 
    ("hard", "hd", "Hard", "HARD", "HD"), 
    ("expert", "ex", "Expert", "EXPERT", "EX", "Exp", "EXP", "exp"), 
    ("master", "ma", "Ma", "MA", "Master", "MASTER", "Mas", "mas", "MAS"),
    ("append", "apd", "Append", "APPEND", "APD", "Apd"), 
]

@dataclass
class MusicDiffInfo:
    level: Dict[str, int] = field(default_factory=dict)
    note_count: Dict[str, int] = field(default_factory=dict)
    has_append: bool = False

VOCAL_CAPTION_MAP_DICT = {
    "エイプリルフールver.": "April Fool",
    "コネクトライブver.": "Connect Live",
    "バーチャル・シンガーver.": "Virtual Singer",
    "アナザーボーカルver.": "Another Vocal",
    "あんさんぶるスターズ！！コラボver.": "Ensemble Stars!! Collab",
    "セカイver.": "Sekai",
    "Inst.ver.": "Inst.",
    "「劇場版プロジェクトセカイ」ver.": "Movie",
}

@dataclass
class PlayProgressCount:
    total: int = 0
    not_clear: int = 0
    clear: int = 0
    fc: int = 0
    ap: int = 0

@dataclass
class MusicAchievementReward:
    coin: int = 0
    jewel: int = 0
    shard: int = 0

MUSIC_RANK_REWARDS: Dict[int, MusicAchievementReward] = {
    1: MusicAchievementReward(0, 10, 0), # C
    2: MusicAchievementReward(0, 20, 0), # B
    3: MusicAchievementReward(0, 30, 0), # A
    4: MusicAchievementReward(0, 50, 0), # S
}
MUSIC_COMBO_REWARDS: Dict[str, Dict[int, MusicAchievementReward]] = {
    'easy': {
        5: MusicAchievementReward(500, 0, 0), 
        6: MusicAchievementReward(1000, 0, 0),
        7: MusicAchievementReward(2000, 0, 0),
        8: MusicAchievementReward(5000, 0, 0),
    },
    'normal': {
        9: MusicAchievementReward(1000, 0, 0), 
        10: MusicAchievementReward(2000, 0, 0),
        11: MusicAchievementReward(4000, 0, 0), 
        12: MusicAchievementReward(10000, 0, 0), 
    },
    'hard': {
        13: MusicAchievementReward(1500, 0, 0),
        14: MusicAchievementReward(3000, 0, 0),
        15: MusicAchievementReward(6000, 0, 0),
        16: MusicAchievementReward(0, 50, 0),
    },
    'expert': {
        17: MusicAchievementReward(2000, 0, 0),
        18: MusicAchievementReward(4000, 0, 0),
        19: MusicAchievementReward(0, 20, 0),
        20: MusicAchievementReward(0, 50, 0),
    },
    'master': {
        21: MusicAchievementReward(3000, 0, 0),
        22: MusicAchievementReward(6000, 0, 0),
        23: MusicAchievementReward(0, 20, 0),
        24: MusicAchievementReward(0, 50, 0),
    },
    'append': {
        25: MusicAchievementReward(3000, 0, 0),
        26: MusicAchievementReward(6000, 0, 0),
        27: MusicAchievementReward(0, 0, 5),
        28: MusicAchievementReward(0, 0, 10),
    },
}

@dataclass
class ChartBpmData:
    bpm_main: float
    bpm_events: List[Dict[str, float]]
    bar_count: int
    duration: float


# ======================= 别名处理 ======================= #

MUSIC_ALIAS_DB_PATH = f"{SEKAI_DATA_DIR}/music_alias/local.json"
MUSIC_ALIAS_DB_BACKUP_PATH = f"{SEKAI_DATA_DIR}/music_alias/local.backup.json"
USER_MUSIC_ALIAS_LOG_PATH = f"{SEKAI_DATA_DIR}/music_alias/history.log"

class MusicAliasDB:
    _instance: Optional['MusicAliasDB'] = None
    _on_add_funcs: List[Callable] = []
    _on_remove_funcs: List[Callable] = []

    @classmethod
    def get_instance(cls) -> 'MusicAliasDB':
        if cls._instance is None:
            cls._instance = MusicAliasDB()
        return cls._instance
    
    def __init__(self):
        self.db = get_file_db(MUSIC_ALIAS_DB_PATH, logger)
        self.mid_alias: Dict[int, List[str]] = {}
        self.alias_mid: Dict[str, int] = {}
        self._load()
    
    def _load(self):
        self.mid_alias.clear()
        self.alias_mid.clear()
        all_aliases = self.db.get('alias', {})
        total_count = 0
        for mid, aliases in all_aliases.items():
            for alias in aliases:
                if self._add(int(mid), alias) is None:
                    total_count += 1
        logger.info(f"从本地别名库成功加载 {total_count} 条歌曲别名")

    def _save(self):
        all_aliases = {}
        for mid, aliases in self.mid_alias.items():
            all_aliases[str(mid)] = aliases
        self.db.set('alias', all_aliases)

    def _add(self, mid: int, alias: str) -> Optional[int]:
        mid = int(mid)
        if alias in self.alias_mid:
            return self.alias_mid[alias]
        self.alias_mid[alias] = mid
        self.mid_alias.setdefault(mid, []).append(alias)
        for func in self._on_add_funcs:
            try: func(mid, alias)
            except: logger.print_exc(f"添加歌曲别名 {alias} -> {mid} 时回调失败")
        return None

    def _remove(self, alias: str) -> Optional[int]:
        if alias not in self.alias_mid:
            return None
        mid = self.alias_mid[alias]
        del self.alias_mid[alias]
        self.mid_alias[mid].remove(alias)
        if not self.mid_alias[mid]:
            del self.mid_alias[mid]
        for func in self._on_remove_funcs:
            try: func(mid, alias)
            except: logger.print_exc(f"删除歌曲别名 {alias} of {mid} 时回调失败")
        return mid

    def add(self, mid: int, alias: str, verbose=True) -> Optional[int]:
        """
        为mid添加别名，添加失败返回冲突的mid，否则返回None
        """
        ret = self._add(mid, alias)
        if ret is None:
            if verbose:
                logger.info(f"添加歌曲别名: \"{alias}\" -> {mid}")
            self._save()
        else:
            if verbose:
                logger.info(f"添加歌曲别名: \"{alias}\" -> {mid} 失败: 已经是 {ret} 的别名")
        return ret

    def remove(self, alias: str) -> Optional[int]:
        """
        删除别名，删除失败返回None，否则返回原本的mid
        """
        ret = self._remove(alias)
        if ret is not None:
            logger.info(f"删除歌曲别名: \"{alias}\" of {ret}")
            self._save()
        else:
            logger.info(f"删除歌曲别名: \"{alias}\" 失败: 别名不存在")
        return ret
    
    def update(self, mid: int, aliases: List[str], verbose=True) -> Tuple[List[str], List[str]]:
        """
        直接更新某个mid的别名，返回添加和删除的别名
        """
        cur_aliases = self.get_aliases(mid)
        to_add = [a for a in aliases if a not in cur_aliases]
        to_remove = [a for a in cur_aliases if a not in aliases]
        for alias in to_add:
            self._add(mid, alias)
        for alias in to_remove:
            self._remove(alias)
        self._save()
        if verbose:
            log_msg = f"更新歌曲 {mid} 的别名"
            if not to_add and not to_remove:
                log_msg += "，没有更改"
            if to_add:
                log_msg += f"，添加 {len(to_add)} 条: {to_add}"
            if to_remove:
                log_msg += f"，删除 {len(to_remove)} 条: {to_remove}"
            logger.info(log_msg)
        return to_add, to_remove

    def backup(self):
        """
        备份别名库
        """
        shutil.copyfile(MUSIC_ALIAS_DB_PATH, MUSIC_ALIAS_DB_BACKUP_PATH)

    @classmethod
    def on_add(cls):
        """
        添加别名添加回调的装饰器
        """
        def wrapper(func: Callable):
            cls._on_add_funcs.append(func)
            return func
        return wrapper

    @classmethod
    def on_remove(cls):
        """
        添加别名删除回调的装饰器
        """
        def wrapper(func: Callable):
            cls._on_remove_funcs.append(func)
            return func
        return wrapper

    def get_aliases(self, mid: int) -> List[str]:
        """
        获取mid的所有别名
        """
        mid = int(mid)
        return self.mid_alias.get(mid, [])

    def get_mid(self, alias: str) -> Optional[int]:
        """
        获取别名的mid
        """
        return self.alias_mid.get(alias)



MUSIC_ALIAS_SYNC_CONFIG_PATH = f"{SEKAI_DATA_DIR}/music_alias/sync_config.yaml"

@dataclass
class SyncMusicAliasConfig:
    regions: List[str]
    url: str
    sync_times: List[Tuple[str, str, str]]
    sync_batch_interval: int
    sync_batch_size: int

    @classmethod
    def get(cls) -> 'SyncMusicAliasConfig':
        return cls(**(Config('sekai.music_alias_sync').get_all()))


# 通过haruki api，同步歌曲别名
async def sync_music_alias():
    cfg = SyncMusicAliasConfig.get()
    mids = []
    for region in cfg.regions:
        ctx = SekaiHandlerContext.from_region(region)
        mids += [m['id'] for m in await ctx.md.musics.get()]
    mids = list(set(mids))
    logger.info(f"开始从haruki同步 {cfg.regions} 的 {len(mids)} 首歌曲的别名")
    alias_db = MusicAliasDB.get_instance()
    alias_db.backup()
    async def sync(mid: int) -> bool:
        try:
            url = cfg.url.format(mid=mid)
            data = await download_json(url)
            await asyncio.sleep(cfg.sync_batch_interval)  
            assert data['music_id'] == mid
            aliases = data['aliases']
            # 排除韩语别名
            aliases = [a for a in aliases if not any('\uac00' <= c <= '\ud7af' for c in a)]
            added, removed = alias_db.update(mid, aliases, verbose=False)
            if added or removed:
                log_msg = f"同步歌曲 {mid} 的别名"
                if added: log_msg += f"，添加 {len(added)} 条: {added}"
                if removed: log_msg += f"，删除 {len(removed)} 条: {removed}"
                logger.info(log_msg)
                return True
            return False
        except Exception as e:
            logger.warning(f"同步歌曲 {mid} 的别名失败: {get_exc_desc(e)}")
    updated_num = sum(await batch_gather(*[sync(mid) for mid in mids], batch_size=cfg.sync_batch_size))
    logger.info(f"别名同步完成，{updated_num} 首歌曲的别名发生变更")
    

# ======================= 搜索歌曲 ======================= #

MUSIC_SEARCH_HELP = """
请输入要查询的曲目，支持以下查询方式:
1. 直接使用曲目名称或别名
2. 曲目ID: id123
3. 曲目负数索引: 例如 -1 表示最新的曲目，-1leak 则会包含未公开的曲目
4. 活动id: event123
5. 箱活: ick1
""".strip()

@dataclass
class MusicSearchOptions:
    use_id: bool = True
    use_nidx: bool = True
    use_title: bool = True
    use_alias: bool = True
    use_distance: bool = True
    use_emb: bool = True
    use_event_id: bool = True
    use_ban_event: bool = True
    max_num: int = 4
    search_num: int = None
    diff: str = None
    raise_when_err: bool = True
    distance_sim_threshold: float = 0.7
    debug: bool = False
    verbose: bool = True

@dataclass
class MusicSearchResult:
    music: Dict = None
    candidates: List[Dict] = field(default_factory=list)
    candidate_msg: str = None
    search_type: str = None
    err_msg: str = None

alias_mid_for_search: Dict[str, List[int]] = {}

@MusicAliasDB.on_add()
def add_music_alias_for_search(mid: int, alias: str):
    global alias_mid_for_search
    alias = clean_name(alias)
    alias_mid_for_search.setdefault(alias, []).append(mid)

@MusicAliasDB.on_remove()
def remove_music_alias_for_search(mid: int, alias: str):
    global alias_mid_for_search
    alias = clean_name(alias)
    if alias in alias_mid_for_search:
        alias_mid_for_search[alias].remove(mid)
        if not alias_mid_for_search[alias]:
            del alias_mid_for_search[alias]

# 根据参数查询曲目
async def search_music(ctx: SekaiHandlerContext, query: str, options: MusicSearchOptions = None) -> MusicSearchResult:
    global alias_mid_for_search
    region_name = get_region_name(ctx.region)

    options = options or MusicSearchOptions()

    def log(msg: str):
        if options.verbose:
            logger.info(msg)
    log(f"查询曲目: \"{query}\" options={options}")

    query = query.strip()
    clean_q = clean_name(query)

    diff = options.diff
    musics = await ctx.md.musics.get()

    alias_db = MusicAliasDB.get_instance()

    ret_musics: List[dict] = []
    sims: List[float] = None
    search_type: str = None
    err_msg: str = None
    candidate_msg: str = ""
    additional_msg: str = ""

    # 检测空
    if not query:
        search_type = "failed"
        err_msg = "搜索文本为空"

    # id匹配
    if not search_type and options.use_id:
        start_time = time.time()
        try: 
            mid = int(query.replace("id", "").strip())
            assert mid > 0
            music = await ctx.md.musics.find_by_id(mid)
        except: 
            music = None
        if music:
            search_type = "id"
            if diff and not await check_music_has_diff(ctx, mid, diff):
                err_msg = f"歌曲{ctx.region.upper()}-{mid}没有{diff}难度"
            else:
                ret_musics.append(music)
        if options.debug:
            log(f"id匹配耗时: {time.time() - start_time:.4f}s")

    # 负数索引匹配
    if not search_type and options.use_nidx:
        start_time = time.time()
        try:
            leak = False
            if '剧透' in query or 'leak' in query:
                leak = True
                query = query.replace('剧透', '').replace('leak', '')
            idx = int(query)
            assert idx < 0
        except:
            idx = None
        if idx:
            sorted_musics = sorted(musics, key=lambda x: x['publishedAt'])
            if not leak:
                while datetime.fromtimestamp(sorted_musics[-1]['publishedAt'] / 1000) > datetime.now():
                    sorted_musics.pop()
            search_type = "nidx"
            if -idx > len(sorted_musics):
                err_msg = f"找不到{region_name}第{-idx}新的歌曲(只有{len(sorted_musics)}首)"
            else:
                ret_musics.append(sorted_musics[idx])
        if options.debug:
            log(f"负数索引匹配耗时: {time.time() - start_time:.4f}s")

    # 活动id匹配
    if not search_type and options.use_event_id:
        start_time = time.time()
        try:
            assert "event" in query
            event_id = int(query.replace("event", ""))
        except:
            event_id = None
        if event_id:
            music = await get_music_of_event(ctx, event_id)
            search_type = "event_id"
            if music:
                ret_musics.append(music)
            else:
                err_msg = f"活动{ctx.region.upper()}-{event_id}没有书下曲"
        if options.debug:
            log(f"活动id匹配耗时: {time.time() - start_time:.4f}s")

    # 箱活匹配
    if not search_type and options.use_ban_event:
        start_time = time.time()
        try:
            event, _ = await extract_ban_event(ctx, query)
        except:
            event = None
        if event:
            music = await get_music_of_event(ctx, event['id'])
            search_type = "ban_event"
            if music:
                ret_musics.append(music)
            else:
                err_msg = f"箱活{ctx.region.upper()}-{event['id']}没有书下曲"
        if options.debug:
            log(f"箱活匹配耗时: {time.time() - start_time:.4f}s")

    # 曲名精确匹配
    if not search_type and options.use_title:
        start_time = time.time()
        for music in musics:
            titles = [clean_name(music['title'])]
            if cn_title := await get_music_trans_title(music['id'], 'cn'):
                titles.append(clean_name(cn_title))
            if en_title := await get_music_trans_title(music['id'], 'en'):
                titles.append(clean_name(en_title))
            if clean_q in titles:
                search_type = "title"
                if diff and not await check_music_has_diff(ctx, music['id'], diff):
                    err_msg = f"名称为\"{query}\"的{region_name}歌曲没有{diff}难度"
                else:
                    ret_musics.append(music)
                break
        if options.debug:
            log(f"曲名精确匹配耗时: {time.time() - start_time:.4f}s")

    # 别名精确匹配
    if not search_type and options.use_alias:
        start_time = time.time()
        mid = alias_mid_for_search.get(clean_q)
        if mid: mid = mid[0]
        music = await ctx.md.musics.find_by_id(mid) 
        if music:
            search_type = "alias"
            if diff and not await check_music_has_diff(ctx, music['id'], diff):
                err_msg = f"别名为\"{query}\"的{region_name}歌曲没有{diff}难度"
            else:
                ret_musics.append(music)
        if options.debug:
            log(f"别名精确匹配耗时: {time.time() - start_time:.4f}s")

    # 编辑距离匹配
    if not search_type and options.use_distance:
        start_time = time.time()
        
        music_sims = []
        for music in musics:
            if diff and not await check_music_has_diff(ctx, music['id'], diff):
                continue
            names = set()
            names.add(music['title'])
            names.add(music['pronunciation'])
            if cn_title := await get_music_trans_title(music['id'], 'cn'):
                names.add(cn_title)
            if en_title := await get_music_trans_title(music['id'], 'en'):
                names.add(en_title)
            for alias in alias_db.get_aliases(music['id']):
                names.add(alias)
            min_dist = 1e9
            for name in names:
                name = clean_name(name)
                dist = 1e9
                # 首先判断是否为子串
                if clean_q in name:
                    dist = -len(clean_q) / len(name) if len(name) else 0 # 目标串越短越好
                else:
                    dist = 1.0 - rapidfuzz.fuzz.ratio(clean_q, name) / 100.0
                min_dist = min(min_dist, dist)
            # 计算相似度
            if min_dist < 0:
                sim = 1.0 + 1.0 * -min_dist  # 子串匹配长度相同时相似度为2.0
            else:
                sim = 1.0 - min_dist
            if sim > options.distance_sim_threshold:
                music_sims.append((music, sim))
        music_sims.sort(key=lambda x: x[1], reverse=True)
        music_sims = music_sims[:options.max_num]
        if music_sims:
            search_type = "distance"
            ret_musics = [m[0] for m in music_sims]
            sims = [m[1] for m in music_sims]
        if options.debug:
            log(f"子串/编辑距离匹配耗时: {time.time() - start_time:.4f}s")
        
    # 语义匹配
    if not search_type and options.use_emb:
        start_time = time.time()
        search_type = "emb"
        if not query:
            err_msg = "搜索文本为空"
        else:
            if not options.search_num:
                search_num = options.max_num * 5
            log(f"搜索曲名: {query}")
            res, scores = await query_music_by_emb(ctx, query, search_num)
            res = deepcopy(res)
            for m, s in zip(res, scores):
                # 把 0 到 无穷的距离映射到 0 到 1 的相似度 
                m['sim'] = max(m.get('sim', 0), math.exp(-s))
            res = unique_by(res, "id")
            res = [m for m in res if diff is None or (await check_music_has_diff(ctx, int(m['id']), diff))]
            res = res[:options.max_num]
            if len(res) == 0:
                err_msg = f"没有找到相关{region_name}歌曲"
            sims = [m['sim'] for m in res]
            ret_musics.extend(res)
        if options.debug:
            log(f"语义匹配耗时: {time.time() - start_time:.4f}s")
                    
    music = ret_musics[0] if len(ret_musics) > 0 else None
    candidates = ret_musics[1:] if len(ret_musics) > 1 else []
    if music and sims:
        sim_type = ""
        if search_type == "emb":
            sim_type = "语义"
        elif search_type == "distance":
            sim_type = "文本"
        candidate_msg += f"{sim_type}相似度{sims[0]:.2f}" 
    if candidates:
        if candidate_msg:
            candidate_msg += "，"
        candidate_msg += "候选曲目: " 
        for m, s in zip(candidates, sims[1:]):
            candidate_msg += f"\n【{m['id']}】{m['title']} ({s:.2f})"
        candidate_msg = candidate_msg.strip()
    
    if additional_msg:
        candidate_msg += "\n" + additional_msg
    
    if music:
        log(f"查询曲目: \"{query}\" 结果: type={search_type} id={music['id']} len(candidates)={len(candidates)}")
    else:
        log(f"查询曲目: \"{query}\" 结果: type={search_type} err_msg={err_msg}")

    if options.raise_when_err and err_msg:
        raise Exception(err_msg)

    return MusicSearchResult(
        music=music, 
        candidates=candidates, 
        candidate_msg=candidate_msg, 
        search_type=search_type, 
        err_msg=err_msg
    )


# ======================= 定数获取 ======================= #

_music_constants: dict[tuple[int, str], float] = {}
_music_constants_mtime: int = None

# 获取定数表
def get_music_constants() -> dict[tuple[int, str], float]:
    """
    获取定数表
    """
    global _music_constants, _music_constants_mtime
    if not config.get('music.constant.enabled'):
        raise ReplyException("定数相关功能暂不可用")
    csv_path = config.get('music.constant.csv_path')
    if not csv_path:
        return {}
    try:
        mtime = os.path.getmtime(csv_path)
        if _music_constants_mtime is None or _music_constants_mtime != mtime:
            df = pd.read_csv(csv_path)
            _music_constants = {}
            for _, row in df.iterrows():
                mid = int(row['id'])
                diff = row['difficulty'].lower()
                constant = float(row['constant'])
                _music_constants[(mid, diff)] = constant
            _music_constants_mtime = mtime
            logger.info(f"成功加载歌曲定数数据，共 {len(_music_constants)} 条记录")
    except Exception as e:
        logger.print_exc(f"加载歌曲定数数据失败")
    return _music_constants

# 获取定数说明卡片
def get_music_constants_info_widget(font_size: int = 20, padding: int = 16, additional_text = None) -> Widget | None:
    info_text = config.get('music.constant.info_text', "")
    if _music_constants_mtime is not None:
        info_text = f"定数更新时间: {datetime.fromtimestamp(_music_constants_mtime).strftime('%Y-%m-%d %H:%M')}\n" + info_text
    if additional_text:
        info_text += "\n" + additional_text
    if not info_text:
        return None
    w = TextBox(info_text, TextStyle(font=DEFAULT_FONT, size=font_size, color=BLACK), use_real_line_count=True)
    w.set_padding(padding).set_bg(roundrect_bg())
    return w


# ======================= 歌曲分数排行榜 ======================= #

LEADERBOARD_TARGET_KEYS = {
    'score': '{live_type}_score',
    'pt': '{live_type}_pt',
    'pt/time': '{live_type}_pt_per_hour',
    'tps': 'tps',
    'time': 'music_time',
}
LEADERBOARD_ALL_LIVE_TYPES = (
    'solo',
    'auto',
    'multi',
)
LEADERBOARD_DIFF_PRIORITY = {
    "master": 6,
    "append": 5,
    "expert": 4,
    "hard": 3,
    "normal": 2,
    "easy": 1,
}

# 获取歌曲分数排行榜数据
async def get_music_leaderboard_data(
    region: str,
    skills: list[float],
    skill_strategy: str,
    deck_bonus: float,
    play_interval: float,
    power: int,
    keep_one_diff_per_music: bool,
    ascend: bool,
    target: str | list[str] = 'all',
    live_type: str | list[str] = 'all',
) -> list[dict]:
    musicmetas_json = get_musicmetas_json(region)
    musicmetas = await musicmetas_json.get(raise_on_no_data=False)
    if not musicmetas:
        return []

    if target == 'all':
        target_keys = LEADERBOARD_TARGET_KEYS
    else:
        if isinstance(target, str):
            target = [target]
        target_keys = {t: LEADERBOARD_TARGET_KEYS[t] for t in target if t in LEADERBOARD_TARGET_KEYS}

    if live_type == 'all':
        live_types = LEADERBOARD_ALL_LIVE_TYPES
    else:
        if isinstance(live_type, str):
            live_type = [live_type]
        live_types = [lt for lt in live_type if lt in LEADERBOARD_ALL_LIVE_TYPES]

    if skill_strategy == 'max':
        sorted_skills = sorted(skills, reverse=True)
    elif skill_strategy == 'min':
        sorted_skills = sorted(skills)
    elif skill_strategy == 'avg':
        avg_skill = sum(skills) / len(skills)
        sorted_skills = [avg_skill] * 5

    # 计算各歌曲数据
    rows: list[dict] = []
    for meta in musicmetas:
        mid = meta['music_id']
        diff = meta['difficulty']
        music_time = meta['music_time']
        tap_count = meta['tap_count']
        event_rate = meta['event_rate']
        base_score = meta['base_score']
        base_score_auto = meta['base_score_auto']
        skill_score_solo = meta['skill_score_solo']
        skill_score_auto = meta['skill_score_auto']
        skill_score_multi = meta['skill_score_multi']
        fever_score = meta['fever_score']

        tps = tap_count / music_time

        best_skill_order_solo = list(range(5))
        best_skill_order_solo.sort(key=lambda x: skill_score_solo[x], reverse=True)
        
        solo_skill = 0.0
        sorted_skill_score_solo = sorted(skill_score_solo[:5], reverse=True)
        for i in range(5):
            solo_skill += sorted_skill_score_solo[i] * sorted_skills[i]
        solo_skill += skill_score_solo[5] * skills[0]

        auto_skill = 0.0
        sorted_skill_score_auto = sorted(skill_score_auto[:5], reverse=True)
        for i in range(5):
            auto_skill += sorted_skill_score_auto[i] * sorted_skills[i]
        auto_skill += skill_score_auto[5] * skills[0]

        multi_skill = 0.0
        sorted_skill_score_multi = sorted(skill_score_multi[:5], reverse=True)
        for i in range(5):
            multi_skill += sorted_skill_score_multi[i] * sorted_skills[i]
        multi_skill += skill_score_multi[5] * skills[0]

        solo_score = base_score + solo_skill
        auto_score = base_score_auto + auto_skill
        multi_score = base_score + multi_skill + fever_score * 0.5 + 0.01875

        solo_skill_account = solo_skill / solo_score
        auto_skill_account = auto_skill / auto_score
        multi_skill_account = multi_skill / multi_score

        def calc_real_score_and_pt(d: dict, live_type: str, power: int):
            active_bonus = 0.0
            if live_type == 'multi':
                active_bonus = 5 * 0.015 * power
            d[f'{live_type}_real_score'] = int(d[f'{live_type}_score'] * power * 4 + active_bonus)

            event_rate = d['event_rate'] / 100.0
            deck_rate = deck_bonus / 100.0 + 1

            match live_type:
                case 'solo':
                    base = 100 + int(d['solo_real_score'] / 20000)
                    d['solo_pt'] = int(base * event_rate * deck_rate)
                case 'auto':
                    base = 100 + int(d['auto_real_score'] / 20000)
                    d['auto_pt'] = int(base * event_rate * deck_rate)
                case 'multi':
                    other_score = d['multi_real_score'] * 4
                    base = 110 + int(d['multi_real_score'] / 17000) + min(13, int(other_score / 340000))
                    d['multi_pt'] = int(base * event_rate * deck_rate)

            play_time = d['music_time'] + play_interval
            d['play_count_per_hour'] = 60 * 60 / play_time
            d[f'{live_type}_pt_per_hour'] = d[f'{live_type}_pt'] * d['play_count_per_hour']
            
        data = {
            'music_id': mid,
            'difficulty': diff,
            'diff_priority': LEADERBOARD_DIFF_PRIORITY[diff] * (-1 if ascend else 1),
            'music_time': music_time,
            'tps': tps,
            'event_rate': event_rate,
            'solo_score': solo_score,
            'auto_score': auto_score,
            'multi_score': multi_score,
            'solo_skill_account': solo_skill_account,
            'auto_skill_account': auto_skill_account,
            'multi_skill_account': multi_skill_account,
        }

        for live_type_key in live_types:
            calc_real_score_and_pt(data, live_type_key, power)
        rows.append(data)

    # 排序并记录排名
    def sort_rows(target: str, live_type: str):
        sort_key = target_keys[target]
        if '{live_type}' in sort_key:
            sort_key = sort_key.format(live_type=live_type)
        rows.sort(key=lambda x: (x[sort_key], x['diff_priority']), reverse=not ascend)
        rank_key = f"{live_type}_{target}_rank"
        if keep_one_diff_per_music:
            seen_mids, cur_rank = set(), 1
            for row in rows:
                mid = row['music_id']
                if mid in seen_mids:
                    row[rank_key] = None
                else:
                    seen_mids.add(mid)
                    row[rank_key] = cur_rank
                    cur_rank += 1
        else:
            for i, row in enumerate(rows):
                row[rank_key] = i + 1

    for live_type in live_types:
        for target in target_keys.keys():
            sort_rows(target, live_type)

    return rows

# 用于歌曲详情中排行榜的缓存
_last_musicmeta_hash = None
_last_music_leaderboard_info: dict[tuple[str, str, int], dict] = {}

# 用于歌曲详情中排行榜的参数
LEADERBOARD_TARGET_NAMES = { 'score': "分数", 'pt': "PT", 'pt/time': "时速" }
LEADERBOARD_LIVETYPE_NAMES = { 'solo': "单人", 'multi': "多人", 'auto': "AUTO" }
LEADERBOARD_LIVETYPE_PLAY_INTERVAL = { 'solo': 28.0, 'auto': 28.0, 'multi': 45.2 }
LEADERBOARD_LIVETYPE_SKILLS = { 'solo': [1.2] * 5, 'auto': [1.2] * 5, 'multi': [2.0] * 5 }
LEADERBOARD_DECK_BONUS = 400.0
LEADERBOARD_POWER = 300000


# ======================= 处理逻辑 ======================= #

# 获取歌曲限定时间
async def get_music_limited_times(ctx: SekaiHandlerContext, mid: int) -> list[tuple[datetime, datetime]]:
    ret = []
    for item in await ctx.md.limited_time_musics.find_by('musicId', mid, mode='all'):
        start = datetime.fromtimestamp(item['startAt'] / 1000)
        end = datetime.fromtimestamp(item['endAt'] / 1000)
        ret.append((start, end))
    return ret

# 检查是否有效歌曲
async def is_valid_music(ctx: SekaiHandlerContext, mid: int, leak=False, diff: str = None) -> bool:
    m = await ctx.md.musics.find_by_id(mid)
    now = datetime.now()
    if not m:
        return False
    if not leak and datetime.fromtimestamp(m['publishedAt'] / 1000) > now:
        return False
    if limited_times := await get_music_limited_times(ctx, mid):
        if not any(start <= now <= end for start, end in limited_times):
            return False
    if m['id'] in (241, 290):
        return False
    if diff:
        diff_info = await get_music_diff_info(ctx, mid)
        if diff not in diff_info.level:
            return False
    return True

# 获取有效歌曲列表
async def get_valid_musics(ctx: SekaiHandlerContext, leak=False) -> List[Dict]:
    musics = await ctx.md.musics.get()
    ret = []
    for m in musics:
        if await is_valid_music(ctx, m['id'], leak=leak):
            ret.append(m)
    return ret

# 在所有服务器根据id检索歌曲（优先在ctx.region)
async def find_music_by_id_all_region(ctx: SekaiHandlerContext, mid: int) -> Optional[Dict]:
    regions = ALL_SERVER_REGIONS.copy()
    regions.remove(ctx.region)
    regions.insert(0, ctx.region)
    for region in regions:
        region_ctx = SekaiHandlerContext.from_region(region)
        if music := await region_ctx.md.musics.find_by_id(mid):
            return music
    return None

# 根据歌曲id获取封面缩略图
async def get_music_cover_thumb(ctx: SekaiHandlerContext, mid: int) -> Image.Image:
    music = await ctx.md.musics.find_by_id(mid)
    assert_and_reply(music, f"歌曲ID={mid}不存在")
    asset_name = music['assetbundleName']
    return await ctx.rip.img(f"music/jacket/{asset_name}_rip/{asset_name}.png", use_img_cache=True, img_cache_max_res=80*80)

# 获取曲目翻译名 lang in ['cn', 'en']
async def get_music_trans_title(mid: int, lang: str, default: str=None) -> str:
    if lang == 'cn':
        return (await music_cn_titles.get()).get(str(mid), default)
    elif lang == 'en':
        return (await music_en_titles.get()).get(str(mid), default)
    raise Exception(f"不支持的语言: {lang}")

# 更新曲名语义库
async def update_music_name_embs(ctx: SekaiHandlerContext):
    try:
        await ctx.block_region()
        region = ctx.region
        musics = await ctx.md.musics.get()
        update_list: List[Tuple[str, str]] = []
        for music in musics:
            mid = music['id']
            title = music['title']
            pron = music['pronunciation']
            update_list.append((f"{mid} {region} title", title))
            update_list.append((f"{mid} {region} pron", pron))
            if cn_title := await get_music_trans_title(mid, 'cn'):
                update_list.append((f"{mid} cn_trans title", cn_title))
            if en_title := await get_music_trans_title(mid, 'en'):
                update_list.append((f"{mid} en_trans title", en_title))
        keys = [item[0] for item in update_list]
        texts = [item[1] for item in update_list]
        await music_name_retriever.batch_update_embs(keys, texts, skip_exist=True)
    except Exception as e:
        logger.print_exc(f"更新曲名语义库失败")

# 从字符串中获取难度 返回(难度名, 去掉难度后缀的字符串)
def extract_diff(text: str, default: str="master") -> Tuple[str, str]:
    all_names = []
    for names in DIFF_NAMES:
        for name in names:
            all_names.append((names[0], name))
    all_names.sort(key=lambda x: len(x[1]), reverse=True)
    for first_name, name in all_names:
        if name in text:
            return first_name, text.replace(name, "").strip()
    return default, text

# 根据曲目id获取曲目难度信息 格式: 
async def get_music_diff_info(ctx: SekaiHandlerContext, mid: int) -> MusicDiffInfo:
    diffs = await ctx.md.music_diffs.find_by('musicId', mid, mode='all')
    ret = MusicDiffInfo()
    for diff in diffs:
        d = diff['musicDifficulty']
        ret.level[d] = diff['playLevel']
        ret.note_count[d] = diff['totalNoteCount']
        if d == 'append': 
            ret.has_append = True
    return ret

# 根据歌曲id和难度获取等级，不存在该难度返回None
async def get_music_diff_level(ctx: SekaiHandlerContext, mid: int, diff: str) -> Optional[int]:
    diff_info = await get_music_diff_info(ctx, mid)
    return diff_info.level.get(diff, None)

# 检查歌曲是否有某个难度
async def check_music_has_diff(ctx: SekaiHandlerContext, mid: int, diff: str) -> bool:
    diff_info = await get_music_diff_info(ctx, mid)
    return diff in diff_info.level

# 根据曲名语义查询歌曲 返回歌曲列表和相似度
async def query_music_by_emb(ctx: SekaiHandlerContext, text: str, limit: int=5):
    await update_music_name_embs(ctx)
    def filter(key: str):
        _, t, _ = key.split()
        return t in ['cn_trans', 'en_trans', ctx.region]
    query_result = await music_name_retriever.find(text, limit, filter=filter)
    ids = [int(item[0].split()[0]) for item in query_result]
    result_musics = await ctx.md.musics.collect_by_ids(ids)
    scores = [item[1] for item in query_result]
    logger.info(f"曲名语义嵌入查询结果: {[(r['id'], r['title'], s) for r, s in zip(result_musics, scores)]}")
    return result_musics, scores

# 获取活动歌曲 不存在返回None
async def get_music_of_event(ctx: SekaiHandlerContext, event_id: int) -> Dict:
    assert_and_reply(await ctx.md.events.find_by_id(event_id), f"活动ID={event_id}不存在")
    em = await ctx.md.event_musics.find_by('eventId', event_id)
    if not em:
        return None
    return await ctx.md.musics.find_by_id(em['musicId'])

# 获取歌曲活动 不存在返回None
async def get_event_of_music(ctx: SekaiHandlerContext, mid: int) -> Dict:
    em = await ctx.md.event_musics.find_by('musicId', mid)
    if not em:
        return None
    return await ctx.md.events.find_by_id(em['eventId'])

# 获取歌曲详情图片
async def compose_music_detail_image(ctx: SekaiHandlerContext, mid: int, title: str=None, title_style: TextStyle=None, title_shadow=False):
    music = await ctx.md.musics.find_by_id(mid)
    assert_and_reply(music, f"歌曲{mid}不存在")
    asset_name = music['assetbundleName']
    cover_img = await ctx.rip.img(f"music/jacket/{asset_name}_rip/{asset_name}.png")
    name    = music["title"]
    cn_name = await get_music_trans_title(mid, 'cn', None)
    composer        = music["composer"]
    lyricist        = music["lyricist"]
    arranger        = music["arranger"]
    mv_info         = music['categories']
    publish_time    = datetime.fromtimestamp(music['publishedAt'] / 1000).strftime('%Y-%m-%d %H:%M:%S')

    if music['isFullLength']:
        name += " [FULL]"

    async def get_audio_len():
        try:
            audio_len = await get_music_audio_length(ctx, mid)
            if not audio_len:
                return "?"
            else:
                secs = audio_len.total_seconds()
                return f"  {secs:.1f}秒（{int(secs) // 60}分{secs % 60.:.1f}秒）"
        except Exception as e:
            logger.warning(f"获取歌曲{mid}音频长度失败: {get_exc_desc(e)}")
            return "?"

    async def get_main_bpm():
        try:
            bpm_info = await get_chart_bpm(ctx, mid)
            if not bpm_info:
                return "?"
            if bpm_info.bpm_main.is_integer():
                return str(int(bpm_info.bpm_main))
            else:
                return f"{bpm_info.bpm_main:.2f}"
        except Exception as e:
            logger.warning(f"获取歌曲{mid}BPM信息失败: {get_exc_desc(e)}")
            return "?"
        
    audio_len, bpm_main = await batch_gather(get_audio_len(), get_main_bpm())

    diff_info   = await get_music_diff_info(ctx, mid)
    diffs       = ['easy', 'normal', 'hard', 'expert', 'master', 'append']
    diff_lvs    = [diff_info.level.get(diff, None) for diff in diffs]
    diff_counts = [diff_info.note_count.get(diff, None) for diff in diffs]
    has_append  = diff_info.has_append

    event = await get_event_of_music(ctx, mid)
    if event:
        event_id = event['id']
        event_banner = await ctx.rip.img(f"home/banner/{event['assetbundleName']}_rip/{event['assetbundleName']}.png")

    caption_vocals = {}
    for item in await ctx.md.music_vocals.find_by('musicId', mid, mode='all'):
        vocal = {}
        caption = VOCAL_CAPTION_MAP_DICT.get(item['caption'], item['caption'].removesuffix("ver."))
        vocal['chara_imgs'] = []
        vocal['vocal_name'] = None
        for chara in item['characters']:
            cid = chara['characterId']
            if chara['characterType'] == 'game_character':
                vocal['chara_imgs'].append(get_chara_icon_by_chara_id(cid))
            elif chara['characterType'] == 'outside_character':
                vocal['vocal_name'] = (await ctx.md.outside_characters.find_by_id(cid))['name']
        if caption not in caption_vocals:
            caption_vocals[caption] = []
        caption_vocals[caption].append(vocal)

    limited_times = await get_music_limited_times(ctx, mid)

    # 更新歌曲排行缓存
    global _last_musicmeta_hash, _last_music_leaderboard_info
    musicmeta_hash = None
    musicmetas_json = get_musicmetas_json(ctx.region)
    if musicmetas_json:
        musicmeta_hash = await musicmetas_json.get_hash()
    live_type_keys = list(LEADERBOARD_LIVETYPE_NAMES.keys())
    target_keys = list(LEADERBOARD_TARGET_NAMES.keys())
    if musicmeta_hash and musicmeta_hash != _last_musicmeta_hash:
        _last_musicmeta_hash = musicmeta_hash
        _last_music_leaderboard_info = {}
        for live_type in live_type_keys:
            for target in target_keys:
                leaderboard = await get_music_leaderboard_data(
                    region=ctx.region,
                    skills=LEADERBOARD_LIVETYPE_SKILLS[live_type],
                    skill_strategy='avg',
                    deck_bonus=LEADERBOARD_DECK_BONUS,
                    play_interval=LEADERBOARD_LIVETYPE_PLAY_INTERVAL[live_type],
                    power=LEADERBOARD_POWER,
                    keep_one_diff_per_music=True,
                    ascend=False,
                    target=target,
                    live_type=live_type,
                )
                for item in leaderboard:
                    rank = item.get(f"{live_type}_{target}_rank", None)
                    if rank:
                        info = { 'rank': rank, 'diff': item['difficulty'] }
                        match target:
                            case 'score':   info['value'] = f"{item[f'{live_type}_score']*100:.1f}%"
                            case 'pt':      info['value'] = str(item[f'{live_type}_pt'])
                            case 'pt/time': info['value'] = f"{item[f'{live_type}_pt_per_hour']/10000:.2f}w/h"
                        _last_music_leaderboard_info[(live_type, target, item['music_id'])] = info
    
    leaderboard_music_num = len(_last_music_leaderboard_info) // (len(LEADERBOARD_LIVETYPE_NAMES) * len(LEADERBOARD_TARGET_NAMES))
    leaderboard_matrix: list[list[dict | None]] = []
    for live_type in live_type_keys:
        leaderboard_matrix.append([])
        for target in target_keys:
            leaderboard_matrix[-1].append(_last_music_leaderboard_info.get((live_type, target, mid), None))
            
    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16).set_item_bg(roundrect_bg()):
                # 附加标题
                if title and title_style:
                    if title_shadow:
                        TextBox(title, TextStyle(title_style.font, title_style.size, title_style.color, use_shadow=True, shadow_offset=2)) \
                            .set_padding(16).set_omit_parent_bg(True).set_bg(roundrect_bg())
                    else:
                        TextBox(title, title_style).set_padding(16).set_omit_parent_bg(True).set_bg(roundrect_bg())

                # 歌曲标题
                name_text = f"【{ctx.region.upper()}-{mid}】{name}"
                if cn_name: name_text += f"  ({cn_name})"
                TextBox(name_text, TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(20, 20, 20)), use_real_line_count=True).set_padding(16).set_w(800)

                with HSplit().set_content_align('c').set_item_align('c').set_sep(16):
                    # 封面
                    with Frame().set_padding(32):
                        ImageBox(cover_img, size=(None, 300), shadow=True)

                    # 信息
                    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(50, 50, 50))
                    style2 = TextStyle(font=DEFAULT_FONT, size=30, color=(70, 70, 70))
                    with HSplit().set_padding(16).set_sep(32).set_content_align('c').set_item_align('c'):
                        with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(0):
                            TextBox(f"作曲", style1)
                            TextBox(f"作词", style1)
                            TextBox(f"编曲", style1)
                            TextBox(f"MV", style1)
                            TextBox(f"时长", style1)
                            TextBox(f"发布时间", style1)
                            TextBox(f"BPM", style1)

                        with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(0):
                            TextBox(composer, style2)
                            TextBox(lyricist, style2)
                            TextBox(arranger, style2)
                            mv_text = ""
                            for item in mv_info:
                                if item == 'original': mv_text += "原版MV & "
                                if item == 'mv': mv_text += "3DMV & "
                                if item == 'mv_2d': mv_text += "2DMV & "
                            mv_text = mv_text[:-3]
                            if not mv_text: mv_text = "无"
                            TextBox(mv_text, style2)
                            TextBox(audio_len, style2)
                            TextBox(publish_time, style2)
                            TextBox(bpm_main, style2)

                # 限定时间
                if limited_times:
                    with HSplit().set_content_align('l').set_item_align('l').set_sep(16).set_padding(16):
                        TextBox("限定时间", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                        with VSplit().set_content_align('l').set_item_align('l').set_sep(4):
                            for start, end in limited_times:
                                TextBox(f"{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%Y-%m-%d %H:%M')}", 
                                        TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)))
                
                with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_omit_parent_bg(True).set_item_bg(roundrect_bg()):
                    # 难度等级/物量
                    vs = 4
                    hs = 8 if has_append else 20
                    with HSplit().set_content_align('c').set_item_align('c').set_sep(vs).set_padding(32):
                        with Grid(col_count=(6 if has_append else 5), item_size_mode='fixed').set_sep(hsep=hs, vsep=vs):
                            # 难度等级
                            for i, (diff, color) in enumerate(DIFF_COLORS.items()):
                                if diff_lvs[i] is not None:
                                    t = TextBox(f"{diff_lvs[i]}", TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=WHITE))
                                    t.set_bg(roundrect_bg(fill=color, radius=12)).set_size((64, 64)).set_content_align('c').set_overflow('clip')     
                            # 物量
                            for i, (count, color) in enumerate(zip(diff_counts, DIFF_COLORS.values())):
                                if count is None: continue
                                style = TextStyle(DEFAULT_BOLD_FONT, 18, (80, 80, 80, 255), use_shadow=True, 
                                                  shadow_offset=1, shadow_color=color.c1 if isinstance(color, LinearGradient) else color)
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(1):
                                    TextBox(f"{count}", style).set_size((64, None)).set_content_align('c').set_overflow('clip')
                                    TextBox("combo", style.replace(size=14)).set_size((64, None)).set_content_align('c').set_overflow('clip')

                    # 排行榜
                    with Grid(row_count=len(live_type_keys)+1, item_size_mode='flex').set_sep(4, 4).set_padding(16).set_content_align('c').set_item_align('c'):
                        th_w, th_h = 80, 36
                        tr_w, tr_h = 130, 36
                        # 表头
                        Spacer(w=th_w, h=th_h).set_bg(FillBg((255, 255, 255, 100)))
                        for target in target_keys:
                            TextBox(LEADERBOARD_TARGET_NAMES[target], TextStyle(DEFAULT_BOLD_FONT, 18, (50, 50, 50))) \
                                .set_bg(FillBg((255, 255, 255, 100))).set_size((tr_w, th_h)).set_content_align('c')
                        # 内容
                        for i, live_type in enumerate(live_type_keys):
                            TextBox(LEADERBOARD_LIVETYPE_NAMES[live_type], TextStyle(DEFAULT_BOLD_FONT, 18, (50, 50, 50))) \
                                .set_bg(FillBg((255, 255, 255, 50))).set_size((th_w, th_h)).set_content_align('c')
                            for j, target in enumerate(target_keys):
                                info = leaderboard_matrix[i][j]
                                if info:
                                    rank_ratio = (info['rank'] - 1) / (leaderboard_music_num - 1)
                                    text1, text2 = f"#{info['rank']}", info['value']
                                    text_color = DIFF_COLORS[info['diff']]
                                else:
                                    rank_ratio = 0.5
                                    text1, text2 = "-", None
                                    text_color = (50, 50, 50)

                                green, yellow, red = (200, 255, 200, 75), (255, 200, 150, 75), (255, 150, 150, 50)
                                bg_color = lerp_color(green, yellow, rank_ratio) if rank_ratio <= 0.5 else lerp_color(yellow, red, rank_ratio - 0.5)

                                with Frame().set_bg(FillBg(bg_color)).set_size((tr_w, tr_h)).set_content_align('c'):
                                    with HSplit().set_content_align('b').set_item_align('b').set_sep(2):
                                        TextBox(text1, TextStyle(DEFAULT_BOLD_FONT, 18, text_color, use_shadow=True))
                                        if text2:
                                            TextBox(text2, TextStyle(DEFAULT_FONT, 12, (50, 50, 50))).set_offset((0, -1))
                        
                # 别名
                aliases = MusicAliasDB.get_instance().get_aliases(mid)
                if aliases:
                    alias_text = "，". join(aliases)
                    font_size = max(10, 24 - get_str_display_length(alias_text) // 40 * 1)
                    with HSplit().set_content_align('l').set_item_align('l').set_sep(16).set_padding(16):
                        TextBox("歌曲别名", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                        TextBox(alias_text, TextStyle(font=DEFAULT_FONT, size=font_size, color=(70, 70, 70)), use_real_line_count=True).set_w(840)    

                def draw_vocal(width: int):
                    # 歌手
                    with Flow().set_content_align('lt').set_item_align('lt').set_sep(8, 8).set_padding(16).set_w(width):
                        for caption, vocals in sorted(caption_vocals.items(), key=lambda x: len(x[1])):
                            with HSplit().set_padding(0).set_sep(4).set_content_align('c').set_item_align('c'):
                                TextBox(caption + "  ver.", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                                Spacer(w=8)
                                for vocal in vocals:
                                    with HSplit().set_content_align('c').set_item_align('c').set_sep(2).set_padding(4) \
                                        .set_bg(roundrect_bg(fill=(255, 255, 255, 75), radius=8)):
                                        if vocal_name := vocal.get('vocal_name'):
                                            font_size = int(24 * min(1.0, 50 / get_str_display_length(vocal_name)))
                                            TextBox(vocal['vocal_name'], TextStyle(font=DEFAULT_FONT, size=font_size, color=(70, 70, 70)))
                                        else:
                                            for img in vocal['chara_imgs']:
                                                ImageBox(img, size=(32, 32), use_alphablend=True)
                def draw_event():
                    # 活动
                    with HSplit().set_sep(8).set_content_align('c').set_item_align('c').set_padding(16):
                        with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                            TextBox("关联活动", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                            TextBox(f"ID: {event_id}", TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)))
                        ImageBox(event_banner, size=(None, 100))

                if event:
                    with HSplit().set_omit_parent_bg(True).set_item_bg(roundrect_bg()).set_padding(0).set_sep(16):
                        draw_vocal(600)
                        draw_event()
                else:
                    draw_vocal(964)
                    
    add_watermark(canvas)
    return await canvas.get_img()    

# 合成歌曲列表图片
async def compose_music_list_image(
    ctx: SekaiHandlerContext, diff: str, lv_musics: List[Tuple[int, List[Dict]]], qid: int, 
    show_id: bool, show_leak: bool, play_result_filter: List[str] = None, show_constant: bool = False,
) -> Image.Image:
    for i in range(len(lv_musics)):
        lv, musics = lv_musics[i]
        covers = await batch_gather(*[get_music_cover_thumb(ctx, m['id']) for m in musics])
        for m, cover in zip(musics, covers):
            m['cover_img'] = cover
        
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userMusicResults'),
        raise_exc=play_result_filter is not None)

    if play_result_filter is None:
        play_result_filter = ['clear', 'not_clear', 'fc', 'ap']

    if profile:
        music_results: dict[tuple[int, str], list] = {}
        for result in profile.get('userMusicResults', []):
            mid = result['musicId']
            result_diff = result.get('musicDifficultyType') or result.get('musicDifficulty')
            if result_diff != diff:
                continue
            music_results.setdefault((mid, result_diff), []).append(result)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            if profile:
                await get_detailed_profile_card(ctx, profile, err_msg)

            if show_constant:
                constants = get_music_constants()
                get_music_constants_info_widget()
            else:
                constants = {}

            with VSplit().set_bg(roundrect_bg()).set_padding(16).set_sep(16):
                lv_musics.sort(key=lambda x: x[0], reverse=False)
                for lv, musics in lv_musics:
                    musics.sort(key=lambda x: (constants.get((x['id'], diff), lv + 1), x['publishedAt']), reverse=False)

                    # 获取游玩结果并过滤
                    filtered_musics = []
                    for music in musics:
                        # 过滤剧透
                        is_leak = datetime.fromtimestamp(music['publishedAt'] / 1000) > datetime.now()
                        music['is_leak'] = is_leak
                        if is_leak and not show_leak:
                            continue
                        # 获取游玩结果
                        result_type = None
                        if profile:
                            if results := music_results.get((music['id'], diff), None):
                                has_clear, full_combo, all_prefect = False, False, False
                                for item in results:
                                    has_clear = has_clear or item["playResult"] != 'not_clear'
                                    full_combo = full_combo or item["fullComboFlg"]
                                    all_prefect = all_prefect or item["fullPerfectFlg"]
                                result_type = "clear" if has_clear else "not_clear"
                                if full_combo: result_type = "fc"
                                if all_prefect: result_type = "ap"
                            # 过滤游玩结果(无结果视为not_clear)
                            if (result_type or "not_clear") not in play_result_filter:
                                continue
                        music['play_result'] = result_type
                        filtered_musics.append(music)

                    if not filtered_musics: continue

                    with VSplit().set_bg(roundrect_bg()).set_padding(8).set_item_align('lt').set_sep(8):
                        lv_text = TextBox(f"{diff.upper()} {lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=WHITE))
                        lv_text.set_padding((10, 5)).set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=5))
                        
                        with Grid(col_count=10).set_sep(5):
                            for music in filtered_musics:
                                with VSplit().set_sep(2):
                                    with Frame():
                                        ImageBox(music['cover_img'], size=(64, 64), image_size_mode='fill')
                                        if music['is_leak']:
                                            TextBox("LEAK", TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=RED)) \
                                                .set_bg(roundrect_bg(radius=4)).set_offset((64, 64)).set_offset_anchor('rb')
                                        if music['play_result']:
                                            result_img = ctx.static_imgs.get(f"icon_{music['play_result']}.png")
                                            ImageBox(result_img, size=(16, 16), image_size_mode='fill').set_offset((64 - 10, 64 - 10))
                                    if show_constant:
                                        constant = constants.get((music['id'], diff), None)
                                        constant = str(constant) if constant is not None else f"{lv}.?"
                                        TextBox(constant, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK, 
                                                                    use_shadow=True, shadow_color=DIFF_COLORS[diff])).set_padding(4)
                                    if show_id:
                                        TextBox(f"{music['id']}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK)).set_w(64)
                                
    add_watermark(canvas)
    return await canvas.get_img()

# 合成打歌进度图片
async def compose_play_progress_image(ctx: SekaiHandlerContext, diff: str, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userMusicResults'),
        raise_exc=True)

    count = { lv: PlayProgressCount() for lv in range(1, 40) }

    music_results: dict[tuple[int, str], list] = {}
    for result in profile.get('userMusicResults', []):
        mid = result['musicId']
        result_diff = result.get('musicDifficultyType') or result.get('musicDifficulty')
        if result_diff != diff:
            continue
        music_results.setdefault((mid, result_diff), []).append(result)

    for music in await get_valid_musics(ctx, leak=False):
        mid = music['id']
        level = await get_music_diff_level(ctx, mid, diff)
        if not level: 
            continue
        count[level].total += 1

        result_type = 0
        if results := music_results.get((mid, diff), None):
            has_clear, full_combo, all_prefect = False, False, False
            for item in results:
                has_clear = has_clear or item["playResult"] != 'not_clear'
                full_combo = full_combo or item["fullComboFlg"]
                all_prefect = all_prefect or item["fullPerfectFlg"]
            if has_clear: result_type = 1
            if full_combo: result_type = 2
            if all_prefect: result_type = 3
        if result_type:
            count[level].not_clear += 1
            if result_type >= 1: count[level].clear += 1
            if result_type >= 2: count[level].fc += 1
            if result_type >= 3: count[level].ap += 1

    count = [(lv, c) for lv, c in count.items() if c.total > 0]

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            if profile:
                await get_detailed_profile_card(ctx, profile, err_msg)

            bar_h, item_h, w = 200, 48, 48
            font_sz = 24

            with HSplit().set_content_align('c').set_item_align('c').set_bg(roundrect_bg()) \
                .set_padding(64).set_sep(8):

                def draw_icon(path):
                    with Frame().set_size((w, item_h)).set_content_align('c'):
                        ImageBox(path, size=(w // 2, w // 2))
                
                # 第一列：进度条的占位 难度占位 not_clear clear fc ap 图标
                with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                    Spacer(w=w, h=bar_h)
                    Spacer(w=w, h=item_h)
                    draw_icon(ctx.static_imgs.get("icon_not_clear.png"))
                    draw_icon(ctx.static_imgs.get("icon_clear.png"))
                    draw_icon(ctx.static_imgs.get("icon_fc.png"))
                    draw_icon(ctx.static_imgs.get("icon_ap.png"))

                # 之后的几列：进度条 难度 各个类型的数量
                for lv, c in count:
                    with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                        # 进度条
                        def draw_bar(color, h, blurglass=False):
                            return Frame().set_size((w, h)).set_bg(RoundRectBg(fill=color, radius=4, blurglass=blurglass))
                        with draw_bar(PLAY_RESULT_COLORS['not_clear'], bar_h, blurglass=True).set_content_align('b') as f:
                            if c.clear: draw_bar(PLAY_RESULT_COLORS['clear'], int(bar_h * c.clear / c.total))
                            if c.fc:    draw_bar(PLAY_RESULT_COLORS['fc'],    int(bar_h * c.fc / c.total))
                            if c.ap:    draw_bar(PLAY_RESULT_COLORS['ap'],    int(bar_h * c.ap / c.total))

                        # 难度
                        TextBox(f"{lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=font_sz, color=WHITE), overflow='clip') \
                            .set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=16)) \
                            .set_size((w, item_h)).set_content_align('c')
                        # 数量 (第一行虽然图标是not_clear但是实际上是total)
                        color = PLAY_RESULT_COLORS['not_clear']
                        ap      = c.ap
                        fc      = c.fc - c.ap
                        clear   = c.clear - c.fc
                        total   = c.total - c.clear
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=False)
                        TextBox(f"{total}", style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg())
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=True, shadow_color=PLAY_RESULT_COLORS['clear'], shadow_offset=2)
                        TextBox(f"{clear}", style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg())
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=True, shadow_color=PLAY_RESULT_COLORS['fc'], shadow_offset=2)
                        TextBox(f"{fc}",    style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg())
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=True, shadow_color=PLAY_RESULT_COLORS['ap'], shadow_offset=2)
                        TextBox(f"{ap}",    style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg())

    add_watermark(canvas)
    return await canvas.get_img()

# 获取任意一个歌曲音频mp3地址
async def get_music_audio_mp3_path(ctx: SekaiHandlerContext, mid: int) -> Optional[str]:
    vocal = await ctx.md.music_vocals.find_by('musicId', mid)
    if not vocal:
        return None
    asset_name = vocal['assetbundleName']
    return await ctx.rip.get_asset_cache_path(f"music/long/{asset_name}/{asset_name}.mp3")

# 获取歌曲长度并缓存
async def get_music_audio_length(ctx: SekaiHandlerContext, mid: int) -> Optional[timedelta]:
    # 尝试从缓存获取
    key = f"music_audio_lengths.jp_{mid}"
    if length := file_db.get(key, None):
        return timedelta(seconds=length)
    # 尝试从music_meta获取
    music_metas = None
    musicmetas_json = get_musicmetas_json(ctx.region)
    if musicmetas_json:
        music_metas = await musicmetas_json.get(raise_on_no_data=False)
    if music_metas and (item := find_by(music_metas, 'music_id', mid)):
        length = item['music_time']
    else:
        # 尝试从音频文件获取
        path = await get_music_audio_mp3_path(ctx, mid)
        if not path:
            jp_ctx = SekaiHandlerContext.from_region("jp")
            path = await get_music_audio_mp3_path(jp_ctx, mid)
        if not path:
            return None
        music = await ctx.md.musics.find_by_id(mid)
        assert_and_reply(music, f'曲目 {mid} 不存在')
        filler_sec = music.get('fillerSec', 0)
        import pydub
        audio = pydub.AudioSegment.from_mp3(path)
        length = len(audio) / 1000 - filler_sec
    file_db.set(key, length)
    return timedelta(seconds=length)

# 获取谱面时长
async def get_music_chart_length(ctx: SekaiHandlerContext, music_id: int, difficulty: str) -> Optional[timedelta]:
    music = await ctx.md.musics.find_by_id(music_id)
    assert_and_reply(music, f'曲目 {music_id} 不存在')

    sus_path = await ctx.rip.get_asset_cache_path(f"music/music_score/{music_id:04d}_01_rip/{difficulty}")
    if not sus_path:
        return None
    from src.pjsekai import scores as pjsekai_scores
    score = pjsekai_scores.Score.open(sus_path, encoding='UTF-8')
    bar = max(note.bar for note in score.notes)
    return timedelta(seconds=float(score.get_time(bar)))

# 合成简要歌曲列表图片
async def compose_music_brief_list_image(
    ctx: SekaiHandlerContext, musics_or_mids: List[Union[int, Dict]],
    title: str=None, title_style: TextStyle=None, title_shadow=False,
    hide_too_far: bool=False,
):
    MAX_NUM = 50

    musics = []
    too_far_num = 0
    for m_or_mid in musics_or_mids:
        if isinstance(m_or_mid, int):
            music = await ctx.md.musics.find_by_id(m_or_mid)
            assert_and_reply(music, f"曲目 {m_or_mid} 不存在")
        else:
            music = m_or_mid
        # 过滤过远的未发布歌曲
        if hide_too_far:
            publish_time = datetime.fromtimestamp(music['publishedAt'] / 1000)
            if publish_time - datetime.now() > timedelta(days=1000):
                too_far_num += 1
                continue
        musics.append(music)

    hide_num = max(0, len(musics) - MAX_NUM)
    musics = musics[:MAX_NUM]
            
    covers = await batch_gather(*[get_music_cover_thumb(ctx, m['id']) for m in musics])
    diff_infos = [await get_music_diff_info(ctx, m['id']) for m in musics]

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg()):
            if title and title_style:
                if title_shadow:
                    TextBox(title, TextStyle(title_style.font, title_style.size, title_style.color, use_shadow=True, shadow_offset=2)) \
                        .set_padding(8)
                else:
                    TextBox(title, title_style).set_padding(8)

            for m, cover, diff_info in zip(musics, covers, diff_infos):
                mid, music_name = m['id'], m['title']
                publish_time = datetime.fromtimestamp(m['publishedAt'] / 1000)
                publish_dlt = get_readable_timedelta(publish_time - datetime.now(), precision='d')
                diffs    = ['easy', 'normal', 'hard', 'expert', 'master', 'append']
                diff_lvs = [diff_info.level.get(diff, None) for diff in diffs]

                style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50))
                style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
                style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE)

                with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(16):
                    ImageBox(cover, size=(80, 80), shadow=True)
                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                        TextBox(f"【{ctx.region.upper()}-{mid}】{music_name}", style1).set_w(250)
                        time_text = f"  {publish_time.strftime('%Y-%m-%d %H:%M:%S')}"
                        if publish_time > datetime.now():
                            time_text += f" ({publish_dlt}后)"
                        TextBox(time_text, style2)
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(4):
                            Spacer(w=2)
                            for diff, level in zip(diffs, diff_lvs):
                                if level is not None:
                                    TextBox(str(level), style3, overflow='clip').set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=8)) \
                                        .set_content_align('c').set_size((28, 28))
                                    
            if too_far_num:
                TextBox(f"{too_far_num}首歌曲距离发布>1000天", TextStyle(font=DEFAULT_FONT, size=20, color=(20, 20, 20))).set_padding(8)
                                    
            if hide_num:
                TextBox(f"{hide_num}首歌曲未显示", TextStyle(font=DEFAULT_FONT, size=20, color=(20, 20, 20))).set_padding(8)

    add_watermark(canvas)
    return await canvas.get_img()

# 合成歌曲奖励图片
async def compose_music_rewards_image(ctx: SekaiHandlerContext, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userMusicAchievements'),
        raise_exc=False)
    # 获取有效歌曲id
    mids = [m['id'] for m in await get_valid_musics(ctx, leak=False)]

    gw, gh = 80, 40
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50)) # 表头
    style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(75, 75, 75)) # 表项
    jewel_icon = ctx.static_imgs.get("jewel.png")
    shard_icon = ctx.static_imgs.get("shard.png")
    def draw_text_icon(text: str, icon: Image, style: TextStyle) -> HSplit:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(4) as hs:
            if text is not None:
                TextBox(str(text), style, overflow='clip')
            ImageBox(icon, size=(None, gh))
        return hs

    # 有抓包的模式
    if profile:
        # 按照歌曲分组
        umas: Dict[int, List[int]] = { mid: [] for mid in mids }
        # 按照歌曲id分组
        for item in profile['userMusicAchievements']:
            if item['musicId'] in umas:
                umas[item['musicId']].append(item['musicAchievementId'])
        # 乐曲评级奖励
        rank_rewards = 0
        for mid in mids:
            for i in MUSIC_RANK_REWARDS:
                if i not in umas[mid]:
                    rank_rewards += MUSIC_RANK_REWARDS[i].jewel
        # 不同难度不同等级连击奖励
        combo_rewards: Dict[str, Dict[int, int]] = { 'hard': {}, 'expert': {}, 'master': {}, 'append': {} }
        for mid in mids:
            diff_info = await get_music_diff_info(ctx, mid)
            for diff in combo_rewards:
                if diff not in diff_info.level: continue
                lv = diff_info.level[diff]
                combo_rewards[diff].setdefault(lv, 0)
                for i in MUSIC_COMBO_REWARDS[diff]:
                    if i not in umas[mid]:
                        combo_rewards[diff][lv] += MUSIC_COMBO_REWARDS[diff][i].jewel \
                            if diff != 'append' else MUSIC_COMBO_REWARDS[diff][i].shard
        # 绘图
        with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                await get_detailed_profile_card(ctx, profile, err_msg)
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg()):
                    # 乐曲评级奖励
                    with HSplit().set_content_align('lt').set_item_align('lt').set_sep(24).set_padding(16).set_bg(roundrect_bg()):
                        TextBox("歌曲评级奖励(S)", style1).set_size((None, gh)).set_content_align('c')
                        draw_text_icon(rank_rewards, jewel_icon, style2).set_size((None, gh))
                    # 连击奖励
                    with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
                        for diff in combo_rewards:
                            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16):
                                # 难度
                                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                                    Spacer(w=gw, h=gh)
                                    for lv in sorted(combo_rewards[diff].keys()):
                                        TextBox(str(lv), TextStyle(DEFAULT_BOLD_FONT, 24, WHITE), overflow='clip').set_size((gh, gh)) \
                                            .set_content_align('c').set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=8))
                                # 奖励
                                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                                    ImageBox(jewel_icon if diff != 'append' else shard_icon, size=(None, gh))
                                    for lv in sorted(combo_rewards[diff].keys()):
                                        reward = combo_rewards[diff][lv]
                                        TextBox(str(reward), style2, overflow='clip').set_size((gw, gh)).set_content_align('l')
                                # 累计奖励
                                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                                    TextBox("累计", style1).set_size((gw, gh)).set_content_align('l') 
                                    acc = 0
                                    for lv in sorted(combo_rewards[diff].keys()):
                                        acc += combo_rewards[diff][lv]
                                        TextBox(str(acc), style2, overflow='clip').set_size((gw, gh)).set_content_align('l')           
        
    # 无抓包的模式
    else:
        profile = await get_basic_profile(ctx, get_player_bind_id(ctx))
        avatar_info = await get_player_avatar_info_by_basic_profile(ctx, profile)

        music_num = len(mids)
        append_music_num = 0
        for mid in mids:
            diff_info = await get_music_diff_info(ctx, mid)
            if 'append' in diff_info.level:
                append_music_num += 1

        clear_count, fc_count = {}, {}
        for item in profile.get('userMusicDifficultyClearCount', {}):
            clear_count[item['musicDifficultyType']] = item['liveClear']
            fc_count[item['musicDifficultyType']] = item['fullCombo']

        # 假设clear最多的难度的数量就是打过歌的数量，并假设打过的就是S
        rank_s_num = max(clear_count.values(), default=0)
        rank_rewards = (sum(r.jewel for r in MUSIC_RANK_REWARDS.values()), music_num - rank_s_num)

        # 假设没fc的歌都没有连击奖励
        combo_rewards: Dict[str, Tuple[int, int]] = {}
        for diff in ['hard', 'expert', 'master', 'append']:
            single_reward = sum(r.jewel for r in MUSIC_COMBO_REWARDS[diff].values()) \
                if diff != 'append' else sum(r.shard for r in MUSIC_COMBO_REWARDS[diff].values())
            combo_rewards[diff] = (single_reward, (music_num if diff != 'append' else append_music_num) - fc_count.get(diff, 0))

        def get_mul_text(x):
            return f"{x[0]*x[1]} ({x[0]}×{x[1]}首)"

        # 绘图
        with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                await get_basic_profile_card(ctx, profile)
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg()):
                    # 说明
                    TextBox(f"{err_msg}\n" \
                            f"仅显示简略估计数据（假设Clear的歌曲都是S评级，未FC的歌曲都没拿到连击奖励）",
                            TextStyle(DEFAULT_FONT, 20, (200, 75, 75)), use_real_line_count=True).set_w(480)
                    # 乐曲评级奖励
                    with HSplit().set_content_align('lt').set_item_align('lt').set_sep(24).set_padding(16).set_bg(roundrect_bg()):
                        TextBox("歌曲评级奖励(S)", style1).set_size((None, gh)).set_content_align('c')
                        draw_text_icon(get_mul_text(rank_rewards), jewel_icon, style2).set_size((None, gh))
                    # 连击奖励
                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                        for diff in combo_rewards:
                            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(24):
                                TextBox(f"{diff.upper()}", TextStyle(DEFAULT_BOLD_FONT, 24, WHITE), overflow='clip') \
                                    .set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=8)).set_size((120, gh)).set_content_align('c')
                                TextBox("连击奖励", style1).set_size((None, gh)).set_content_align('l')
                                draw_text_icon(get_mul_text(combo_rewards[diff]), jewel_icon if diff != 'append' else shard_icon, style2) \
                                    .set_size((None, gh))

    add_watermark(canvas)
    return await canvas.get_img()

# 获取铺面bpm
async def get_chart_bpm(ctx: SekaiHandlerContext, mid: int, timeout: float=5.0):
    # from https://gitlab.com/pjsekai/musics/-/blob/main/music_bpm.py
    try:
        sus_path = await ctx.rip.get_asset_cache_path(
            f"music/music_score/{mid:04d}_01_rip/expert", 
            allow_error=False, timeout=timeout,
        )
        r = Path(sus_path).read_text(encoding='utf-8')
    except:
        try:
            sus_path = await ctx.rip.get_asset_cache_path(
                f"music/music_score/{mid:04d}_01_rip/append", 
                allow_error=False, timeout=timeout,
            )
            r = Path(sus_path).read_text(encoding='utf-8')
        except:
            return ChartBpmData(
                main_bpm=None,
                bpm_events=[{'time': 0, 'bpm': None}],
                bar_count=0,
                duration=None,
            )

    score = {}
    bar_count = 0
    for line in r.split('\n'):
        match: re.Match = re.match(r'#(...)(...?)\s*\:\s*(\S*)', line)
        if match:
            bar, key, value = match.groups()
            score[(bar, key)] = value
            if bar.isdigit():
                bar_count = max(bar_count, int(bar) + 1)

    bpm_palette = {}
    for bar, key in score:
        if bar == 'BPM':
            bpm_palette[key] = float(score[(bar, key)])

    bpm_events = {}
    for bar, key in score:
        if bar.isdigit() and key == '08':
            value = score[(bar, key)]
            length = len(value) // 2

            for i in range(length):
                bpm_key = value[i*2:(i+1)*2]
                if bpm_key == '00':
                    continue
                bpm = bpm_palette[bpm_key]
                t = int(bar) + i / length
                bpm_events[t] = bpm

    bpm_events = [{
        'bar': bar,
        'bpm': bpm,
    } for bar, bpm in sorted(bpm_events.items())]

    for i in range(len(bpm_events)):
        if i > 0 and bpm_events[i]['bpm'] == bpm_events[i-1]['bpm']:
            bpm_events[i]['deleted'] = True

    bpm_events = [bpm_event for bpm_event in bpm_events if bpm_event.get('deleted') != True]

    bpms = {}
    for i in range(len(bpm_events)):
        bpm = bpm_events[i]['bpm']
        if bpm not in bpms:
            bpms[bpm] = 0.0

        if i+1 < len(bpm_events):
            bpm_events[i]['duration'] = (bpm_events[i+1]['bar'] - bpm_events[i]['bar']) / bpm * 4 * 60
        else:
            bpm_events[i]['duration'] = (bar_count - bpm_events[i]['bar']) / bpm * 4 * 60

        bpms[bpm] += bpm_events[i]['duration']

    sorted_bpms = sorted([(bpms[bpm], bpm) for bpm in bpms], reverse=True)
    bpm_main = sorted_bpms[0][1]
    duration = sum([bpm[0] for bpm in sorted_bpms])

    return ChartBpmData(
        bpm_main=bpm_main,
        bpm_events=bpm_events,
        bar_count=bar_count,
        duration=duration,
    )

# 合成best30图片
async def compose_best30_image(ctx: SekaiHandlerContext, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, qid, 
        filter=get_detailed_profile_card_filter('userMusicResults'),
        raise_exc=True,
    )

    # 数据获取
    with ProfileTimer('b30.get_data'):
        constants = get_music_constants()
        constant_results: list[dict] = []

        music_diff_infos: dict[int, MusicDiffInfo] = {}
        music_results: dict[tuple[int, str], list] = {}
        for result in profile.get('userMusicResults', []):
            mid = result['musicId']
            diff = result.get('musicDifficultyType') or result.get('musicDifficulty')
            music_results.setdefault((mid, diff), []).append(result)
            if mid not in music_diff_infos:
                music_diff_infos[mid] = await get_music_diff_info(ctx, mid)

        for (mid, diff), results in music_results.items():
            if not await is_valid_music(ctx, mid, leak=False):
                continue
            music = await ctx.md.musics.find_by_id(mid)
            level = music_diff_infos[mid].level.get(diff, None)
            if not level or not music:
                continue
            result_type = None
            if results:
                has_clear, full_combo, all_prefect = False, False, False
                for item in results:
                    has_clear = has_clear or item["playResult"] != 'not_clear'
                    full_combo = full_combo or item["fullComboFlg"]
                    all_prefect = all_prefect or item["fullPerfectFlg"]
                result_type = "clear" if has_clear else "not_clear"
                if full_combo: result_type = "fc"
                if all_prefect: result_type = "ap"
            rating = None
            if result_type == 'ap':
                rating = constants.get((mid, diff), level)
            elif result_type == 'fc':
                rating = constants.get((mid, diff), level) - (1 if level >= 33 else 1.5)
            constant_text = str((constants.get((mid, diff), f"{level}.?")))
            if rating is not None:
                constant_results.append({
                    'mid': mid, 'diff': diff, 'level': level,
                    'title': music['title'], 'rating': rating,
                    'result_type': result_type, 'constant_text': constant_text,
                })

        constant_results.sort(key=lambda x: x['rating'], reverse=True)
        constant_results = constant_results[:30]
        user_rating = sum([cr['rating'] for cr in constant_results]) / 30

    music_covers = await batch_gather(*[get_music_cover_thumb(ctx, cr['mid']) for cr in constant_results])
    for cr, cover in zip(constant_results, music_covers):
        cr['cover'] = cover
    
    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('c').set_item_align('c').set_sep(16):
            with HSplit().set_content_align('l').set_item_align('l').set_sep(16).set_item_bg(roundrect_bg()):
                (await get_detailed_profile_card(ctx, profile, err_msg)).set_bg(None)
                with VSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(16):
                    shadow_color = LinearGradient(PLAY_RESULT_COLORS['ap'], PLAY_RESULT_COLORS['fc'], (0, 0), (1, 1))
                    style = TextStyle(DEFAULT_BOLD_FONT, 24, BLACK, use_shadow=True, shadow_color=shadow_color, shadow_offset=3)
                    TextBox(f"Rating", style)
                    TextBox(f"{user_rating:.2f}", style.replace(size=48))
                get_music_constants_info_widget(additional_text="计算方式: 33及以上FC-1，以下-1.5，AP±0").set_bg(None)

            with Grid(col_count=3, hsep=16, vsep=16).set_item_bg(roundrect_bg()).set_content_align('lt').set_content_align('lt'):
                for cr in constant_results:
                    diff_color = DIFF_COLORS[cr['diff']]
                    with HSplit().set_content_align('l').set_item_align('l').set_sep(16).set_padding((24, 20)):
                        with Frame().set_content_align('lt'):
                            ImageBox(cr['cover'], size=(80, 80), shadow=True)
                            TextBox(str(cr['level']), TextStyle(DEFAULT_BOLD_FONT, 18, WHITE), overflow='clip') \
                                .set_bg(roundrect_bg(fill=diff_color, radius=16)).set_offset((-16, -16)).set_content_align('c').set_size((32, 32))

                        with VSplit().set_content_align('l').set_item_align('l').set_sep(8):
                            TextBox(f"{cr['title']}", TextStyle(DEFAULT_BOLD_FONT, 24, BLACK)).set_w(230)
                            with HSplit().set_content_align('l').set_item_align('l').set_sep(8):
                                TextBox(cr['constant_text'], TextStyle(DEFAULT_BOLD_FONT, 24, WHITE)) \
                                    .set_bg(roundrect_bg(fill=diff_color, radius=4)).set_padding(6)
                                TextBox(f"→", TextStyle(DEFAULT_BOLD_FONT, 32, BLACK))
                                TextBox(f"{cr['rating']:.1f}", TextStyle(DEFAULT_BOLD_FONT, 24, BLACK, 
                                                                         use_shadow=True, shadow_color=PLAY_RESULT_COLORS[cr['result_type']], shadow_offset=2))
                            play_result_img_path = "fc_text.png" if cr['result_type'] == 'fc' else "ap_text.png"
                            ImageBox(ctx.static_imgs.get(play_result_img_path), size=(None, 20), use_alphablend=True, shadow=True).set_padding((0, 2))
   
    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令处理 ======================= #

# 设置歌曲别名
pjsk_alias_set = SekaiCmdHandler([
    "/pjsk alias add", "/pjskalias add",
    "/添加歌曲别名", "/歌曲别名添加", 
])
pjsk_alias_set.check_cdrate(cd).check_wblist(gbl)
@pjsk_alias_set.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    try:
        query, aliases = args.split(maxsplit=1)
        music = (await search_music(ctx, query, MusicSearchOptions(use_id=True, use_emb=False))).music
        assert music is not None
        assert aliases
        aliases = aliases.replace("，", ",")
        aliases = aliases.split(",")
        assert aliases
        mid = music['id']
        title = music["title"]
    except:
        return await ctx.asend_reply_msg(f"使用方式:\n{ctx.original_trigger_cmd} 歌曲ID/名称 别名1，别名2...")

    ok_aliases     = []
    failed_aliases = []
    for alias in aliases:
        mid2 = MusicAliasDB.get_instance().add(mid, alias)
        if mid2 is not None:
            title2 = (await find_music_by_id_all_region(ctx, mid2))['title']
            failed_aliases.append((alias, f"已经是【{mid2}】{title2} 的别名"))
        else:
            with open(USER_MUSIC_ALIAS_LOG_PATH, "a") as f:
                f.write(f"{datetime.now()} {ctx.user_id}@{ctx.group_id} set \"{alias}\" to {mid}\n") 
            logger.info(f"群聊 {ctx.group_id} 的用户 {ctx.user_id} 为歌曲 {mid} 设置了别名 {alias}")
            ok_aliases.append(alias)

    msg = ""
    if ok_aliases:
        msg += f"为【{mid}】{title} 设置别名: "
        msg += "，".join(ok_aliases)
    if failed_aliases:
        msg += "\n以下别名设置失败:\n"
        for alias, reason in failed_aliases:
            msg += f"{alias}: {reason}\n"

    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 查看歌曲别名
pjsk_alias = SekaiCmdHandler([
    "/pjsk alias", "/music alias", 
    "/歌曲别名", "/查歌曲别名",
])
pjsk_alias.check_cdrate(cd).check_wblist(gbl)
@pjsk_alias.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    try:
        music = (await search_music(ctx, args, MusicSearchOptions(use_id=True, use_emb=False))).music
        assert music is not None
    except:
        return await ctx.asend_reply_msg("请输入正确的歌曲ID/歌曲名")

    aliases = MusicAliasDB.get_instance().get_aliases(music['id'])
    if not aliases:
        return await ctx.asend_reply_msg(f"【{music['id']}】{music['title']} 还没有别名")

    msg = f"【{music['id']}】{music['title']} 的别名: "
    msg += "，".join(aliases)

    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 删除歌曲别名
pjsk_alias_del = SekaiCmdHandler([
    "/pjsk alias del", "/pjskalias del",
    "/删除歌曲别名", "/歌曲别名删除",
])
pjsk_alias_del.check_cdrate(cd).check_wblist(gbl)
@pjsk_alias_del.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    try:
        args = args.replace("，", ",")
        aliases = args.split(",")
        assert aliases
    except:
        return await ctx.asend_reply_msg(f"使用方式:\n{ctx.original_trigger_cmd} 别名1 别名2...")

    ok_aliases: Dict[int, List[str]] = {}
    failed_aliases = []
    for alias in aliases:
        mid = MusicAliasDB.get_instance().remove(alias)
        if mid is not None:
            ok_aliases.setdefault(mid, []).append(alias)
            with open(USER_MUSIC_ALIAS_LOG_PATH, "a") as f:
                f.write(f"{datetime.now()} {ctx.user_id}@{ctx.group_id} del \"{alias}\" of {mid}\n") 
            logger.info(f"群聊 {ctx.group_id} 的用户 {ctx.user_id} 删除了歌曲 {mid} 的别名 {alias}")
        else:
            failed_aliases.append((alias, "没有这个别名"))
    
    msg = ""
    if ok_aliases:
        msg += f"成功删除别名: "
        for mid, aliases in ok_aliases.items():
            title = (await find_music_by_id_all_region(ctx, mid))['title']
            msg += f"\n【{mid}】{title} 的别名: "
            msg += "，".join(aliases)
    if failed_aliases:
        msg += "\n以下别名删除失败:\n"
        for alias, reason in failed_aliases:
            msg += f"{alias}: {reason}\n"
    
    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 查曲
pjsk_song = SekaiCmdHandler([
    "/pjsk song", "/pjsk music", "/song", "/music",
    "/查曲", "/查歌", "/歌曲", "/查歌曲",
])
pjsk_song.check_cdrate(cd).check_wblist(gbl)
@pjsk_song.handle()
async def _(ctx: SekaiHandlerContext):
    query = ctx.get_args().strip()
    if not query:
        return await ctx.asend_reply_msg("请输入要查询的歌曲名或ID")
    
    # 查询泄漏曲
    if query.lower() == "leak":
        leak_musics = [
            m for m in await ctx.md.musics.get() 
            if datetime.fromtimestamp(m['publishedAt'] / 1000) > datetime.now()
        ]
        assert_and_reply(leak_musics, f"当前{get_region_name(ctx.region)}没有leak曲目")
        leak_musics = sorted(leak_musics, key=lambda x: (x['publishedAt'], x['id']))
        return await ctx.asend_reply_msg(await get_image_cq(
            await compose_music_brief_list_image(ctx, leak_musics, hide_too_far=True),
            low_quality=True,
        ))
    
    # 查询多曲
    try:
        mids = list(map(int, query.split()))
        assert len(mids) > 1
    except:
        mids = None
    if mids:
        return await ctx.asend_reply_msg(await get_image_cq(
            await compose_music_brief_list_image(ctx, mids),
            low_quality=True,
        ))

    # 查询单曲
    ret = await search_music(ctx, query, MusicSearchOptions())
    msg = await get_image_cq(await compose_music_detail_image(ctx, ret.music['id']))
    msg += ret.candidate_msg
    return await ctx.asend_reply_msg(msg)


# 物量查询
pjsk_note_num = SekaiCmdHandler([
    "/pjsk note num", "/pjsk note count",
    "/物量", "/查物量",
])
pjsk_note_num.check_cdrate(cd).check_wblist(gbl)
@pjsk_note_num.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    try:
        note_count = int(args)
    except:
        return await ctx.asend_reply_msg("请输入物量数值")
    diffs = await ctx.md.music_diffs.find_by("totalNoteCount", note_count, mode="all")
    assert_and_reply(diffs, f"没有找到物量为{note_count}的谱面")
    msg = ""
    for diff in diffs:
        mid = diff["musicId"]
        d = diff['musicDifficulty']
        lv = diff['playLevel']
        title = (await ctx.md.musics.find_by_id(mid))['title']
        msg += f"【{mid}】{title} - {d} {lv}\n"
    return await ctx.asend_reply_msg(msg.strip())


# 歌曲列表
MUSIC_LIST_CMDS = ["/pjsk song list", "/pjsk music list", "/歌曲列表", "/歌曲一览",]
MUSIC_CONSTANT_CMDS = ["/pjsk music constant", "/难度排行", "/定数表", '/歌曲定数',]
pjsk_music_list = SekaiCmdHandler(MUSIC_LIST_CMDS + MUSIC_CONSTANT_CMDS)
pjsk_music_list.check_cdrate(cd).check_wblist(gbl)
@pjsk_music_list.handle()
async def _(ctx: SekaiHandlerContext):
    help_msg = """
使用方式: 
所有歌曲: /歌曲列表 ma 
某个等级歌曲: /歌曲列表 ma 32 
某个范围歌曲: /歌曲列表 ma 24 32
显示歌曲ID: /歌曲列表 ma 32 id
过滤游玩结果: /歌曲列表 ma 32 fc     
按定数表排行: /难度排行 ma 32                   
""".strip()

    args = ctx.get_args().strip()
    show_id = False
    show_leak = False
    play_result_filter=None
    show_constant = False

    try:
        diff, args = extract_diff(args)

        if 'id' in args:
            args = args.replace('id', '', 1)
            show_id = True
        if 'leak' in args:
            args = args.replace('leak', '', 1)
            show_leak = True

        if '未clear' in args:
            args = args.replace('未clear', '', 1)
            play_result_filter = ['not_clear']
        elif '未fc' in args:
            args = args.replace('未fc', '', 1)
            play_result_filter = ['not_clear', 'clear']
        elif '未ap' in args:
            args = args.replace('未ap', '', 1)
            play_result_filter = ['not_clear', 'clear', 'fc']
        elif any(x in args for x in ['clear', 'fc', 'ap']):
            play_result_filter = []
            if 'clear' in args:
                args = args.replace('clear', '', 1)
                play_result_filter.append('clear')
            if 'fc' in args:
                args = args.replace('fc', '', 1)
                play_result_filter.append('fc')
            if 'ap' in args:
                args = args.replace('ap', '', 1)
                play_result_filter.append('ap')

        if ctx.trigger_cmd in MUSIC_CONSTANT_CMDS:
            show_constant = True
        else:
            if '定数' in args:
                args = args.replace('定数', '', 1)
                show_constant = True

    except:
        return await ctx.asend_reply_msg(help_msg)

    args = args.strip()
    
    lv, ma_lv, mi_lv = None, None, None
    try: 
        lvs = args.split()
        assert len(lvs) == 2
        lvs = list(map(int, lvs))
        ma_lv = max(lvs)
        mi_lv = min(lvs)
    except:
        ma_lv = mi_lv = None
        try: 
            lv = int(args)
        except: 
            # 只有空参数允许解析失败
            if args:
                return await ctx.asend_reply_msg(help_msg)

    musics = await get_valid_musics(ctx, leak=show_leak)

    logger.info(f"查询歌曲列表 diff={diff} lv={lv} ma_lv={ma_lv} mi_lv={mi_lv}")
    lv_musics = {}

    for music in musics:
        mid = music["id"]
        diff_info = await get_music_diff_info(ctx, mid)
        if diff not in diff_info.level: continue
        music_lv = diff_info.level[diff]
        if ma_lv and music_lv > ma_lv: continue
        if mi_lv and music_lv < mi_lv: continue
        if lv and lv != music_lv: continue
        if music_lv not in lv_musics:
            lv_musics[music_lv] = []
        lv_musics[music_lv].append(music)
    
    assert_and_reply(lv_musics, "没有找到符合条件的曲目")
    lv_musics = sorted(lv_musics.items(), key=lambda x: x[0], reverse=True)

    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_music_list_image(
            ctx, diff, lv_musics, ctx.user_id, 
            show_id, show_leak, play_result_filter,
            show_constant,
        ),
        low_quality=True,
    ))


# 打歌进度
pjsk_play_progress = SekaiCmdHandler([
    "/pjsk progress",
    "/pjsk进度", "/打歌进度", "/歌曲进度", "/打歌信息",
])
pjsk_play_progress.check_cdrate(cd).check_wblist(gbl)
@pjsk_play_progress.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    diff, _ = extract_diff(args)
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_play_progress_image(ctx, diff, ctx.user_id),
        low_quality=True,
    ))


# 同步歌曲别名
pjsk_sync_music_alias = CmdHandler([
    "/sync music alias", "/sma",
    "/同步歌曲别名", 
], logger)
pjsk_sync_music_alias.check_cdrate(cd).check_wblist(gbl).check_superuser()
@pjsk_sync_music_alias.handle()
async def _(ctx: HandlerContext):
    await ctx.block(timeout=0)
    await ctx.asend_reply_msg("开始同步歌曲别名...")
    await sync_music_alias()
    await ctx.asend_reply_msg("同步完成")


# 歌曲奖励
pjsk_music_rewards = SekaiCmdHandler([
    "/pjsk music rewards",
    "/歌曲奖励", "/打歌奖励", "/歌曲挖矿", "/打歌挖矿",
])  
pjsk_music_rewards.check_cdrate(cd).check_wblist(gbl)
@pjsk_music_rewards.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_music_rewards_image(ctx, ctx.user_id),
        low_quality=True,
    ))


# bpm查询
pjsk_bpm = SekaiCmdHandler([
    "/pjsk bpm", "/查bpm", "/查BPM",
])
pjsk_bpm.check_cdrate(cd).check_wblist(gbl)
@pjsk_bpm.handle()
async def _(ctx: SekaiHandlerContext):
    query = ctx.get_args().strip()
    ret = await search_music(ctx, query, MusicSearchOptions())
    assert_and_reply(ret.music, f"未找到歌曲\"{query}\"")

    cover_cq = await get_image_cq(
        await get_music_cover_thumb(ctx, ret.music['id']), 
        low_quality=True
    )
    msg = f"{cover_cq}【{ret.music['id']}】{ret.music['title']}\n{ret.candidate_msg}".strip()
    
    bpm = await get_chart_bpm(ctx, ret.music['id'])
    msg += "\n---\nBPM: "
    for event in bpm.bpm_events:
        bpm = event.get('bpm')
        if bpm is None:
            msg += "无数据 - "
        else:
            if bpm.is_integer():
                bpm = int(bpm)
            msg += f"{bpm} - "
    msg = msg.rstrip(" - ")
    return await ctx.asend_reply_msg(msg)


# 查曲绘
pjsk_music_cover = SekaiCmdHandler([
    "/pjsk music cover",
    "/查曲绘", "/曲绘",
])
pjsk_music_cover.check_cdrate(cd).check_wblist(gbl)
@pjsk_music_cover.handle()
async def _(ctx: SekaiHandlerContext):
    query = ctx.get_args().strip()
    ret = await search_music(ctx, query, MusicSearchOptions(raise_when_err=True))
    asset_name = ret.music['assetbundleName']
    title = ret.music['title']
    mid = ret.music['id']
    cover = await ctx.rip.img(f"music/jacket/{asset_name}_rip/{asset_name}.png")
    msg = await get_image_cq(cover) + (f"【{mid}】{title}\n" + ret.candidate_msg).strip()
    return await ctx.asend_reply_msg(msg)


# best30
pjsk_best30 = SekaiCmdHandler([
    "/pjsk b30", "/b30", "/pjsk rating",
])
pjsk_best30.check_cdrate(cd).check_wblist(gbl)
@pjsk_best30.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_best30_image(ctx, ctx.user_id),
        low_quality=True,
    ))


# ======================= 定时任务 ======================= #

# 新曲上线提醒
@repeat_with_interval(60, '新曲上线提醒', logger)
async def new_music_notify():
    notified_musics = file_db.get("notified_new_musics", {})
    updated = False

    for region in ALL_SERVER_REGIONS:
        region_name = get_region_name(region)
        ctx = SekaiHandlerContext.from_region(region)
        musics = await ctx.md.musics.get()
        now = datetime.now()

        need_send_musics = []
        for music in musics:
            mid = music["id"]
            publish_time = datetime.fromtimestamp(music["publishedAt"] / 1000)
            if mid in notified_musics.get(region, []): continue
            if now - publish_time > timedelta(hours=6): continue
            if publish_time - now > timedelta(minutes=1): continue
            need_send_musics.append(music)

        BATCH_SEND_THRESHOLD = 4
        if len(need_send_musics) >= BATCH_SEND_THRESHOLD:
            # 批量发送
            logger.info(f"发送批量新曲上线提醒: {region} {len(need_send_musics)}首新曲")

            img = await compose_music_brief_list_image(
                ctx, need_send_musics, title=f"{region_name}新曲上线-{len(need_send_musics)}首", 
                title_style=TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(20, 20, 20, 255)),
            )
            msg = await get_image_cq(img)

            for group_id in music_group_sub.get_all(region):
                if not gbl.check_id(group_id): continue
                try:
                    group_msg = msg
                    for uid in music_user_sub.get_all(region, group_id):
                        group_msg += f"[CQ:at,qq={uid}]"
                    await send_group_msg_by_bot(group_id, group_msg.strip())
                except:
                    logger.print_exc(f"发送新曲上线提醒: {region} 到群 {group_id} 失败")
                    continue

            if region not in notified_musics:
                notified_musics[region] = []
            notified_musics[region].extend(m["id"] for m in need_send_musics)
            updated = True
        
        else:
            # 分别发送
            for music in need_send_musics:
                mid = music["id"]
                publish_time = datetime.fromtimestamp(music["publishedAt"] / 1000)
                logger.info(f"发送新曲上线提醒: {region} {music['id']} {music['title']}")

                img = await compose_music_detail_image(
                    ctx, mid, title=f"{region_name}新曲上线", 
                    title_style=TextStyle(font=DEFAULT_BOLD_FONT, size=35, color=LinearGradient(
                        c1=(0, 0, 0, 255), c2=(0, 0, 0, 255), p1=(0, 0), p2=(1, 1),
                    )), title_shadow=False,
                )
                msg = await get_image_cq(img)

                for group_id in music_group_sub.get_all(region):
                    if not gbl.check_id(group_id): continue
                    try:
                        group_msg = msg
                        for uid in music_user_sub.get_all(region, group_id):
                            group_msg += f"[CQ:at,qq={uid}] "
                        await send_group_msg_by_bot(group_id, group_msg.strip())
                    except:
                        logger.print_exc(f"发送新曲新曲上线提醒: {region} {music['id']} 到群 {group_id} 失败")
                        continue
                
                if region not in notified_musics:
                    notified_musics[region] = []
                notified_musics[region].append(mid)
                updated = True

    if updated:
        file_db.set("notified_new_musics", notified_musics)


# 新APD上线提醒
@repeat_with_interval(60, '新APD上线提醒', logger)
async def new_apd_notify():
    no_apd_musics = file_db.get("no_apd_musics", {})
    notified_new_apd = file_db.get("notified_new_apd", {})
    updated = False

    SEND_LIMIT = 5
    total_send = 0

    for region in ALL_SERVER_REGIONS:
        region_name = get_region_name(region)
        ctx = SekaiHandlerContext.from_region(region)
        musics = await ctx.md.musics.get()

        for music in musics:
            mid = music["id"]
            diff_info = await get_music_diff_info(ctx, mid)
            # 之前已经通知过: 忽略
            if mid in notified_new_apd.get(region, []): 
                continue
            # 歌曲本身无APPEND: 忽略，并尝试添加到no_append_musics中
            if not diff_info.has_append:
                if mid not in no_apd_musics.get(region, []):
                    if region not in no_apd_musics:
                        no_apd_musics[region] = []
                    no_apd_musics[region].append(mid)
                    updated = True
                continue
            # 歌曲本身有APPEND，但是之前不在no_append_musics中，即一开始就有APPEND了，忽略，并且认为已经通知过
            if mid not in no_apd_musics.get(region, []):
                if mid not in notified_new_apd.get(region, []):
                    if region not in notified_new_apd:
                        notified_new_apd[region] = []
                    notified_new_apd[region].append(mid)
                    updated = True
                continue
            
            logger.info(f"发送新APPEND上线提醒: {region} {music['id']} {music['title']}")

            total_send += 1
            
            if total_send <= SEND_LIMIT:
                img = await compose_music_detail_image(
                    ctx, mid, title=f"新{region_name}APPEND谱面上线", 
                    title_style=TextStyle(font=DEFAULT_BOLD_FONT, size=35, color=DIFF_COLORS['append']),
                    title_shadow=False,
                )
                msg = await get_image_cq(img)

                for group_id in apd_group_sub.get_all(region):
                    if not gbl.check_id(group_id): continue
                    try:
                        group_msg = msg
                        for uid in apd_user_sub.get_all(region, group_id):
                            group_msg += f"[CQ:at,qq={uid}] "
                        await send_group_msg_by_bot(group_id, group_msg.strip())
                    except:
                        logger.print_exc(f"发送新APPEND上线提醒: {region} {music['id']} 到群 {group_id} 失败")
                        continue
            
            # 从无APPEND列表中移除
            if region in no_apd_musics:
                no_apd_musics[region].remove(mid)
            # 添加到已通知列表中
            if region not in notified_new_apd:
                notified_new_apd[region] = []
            notified_new_apd[region].append(mid)
            updated = True

    if updated:
        file_db.set("no_apd_musics", no_apd_musics)
        file_db.set("notified_new_apd", notified_new_apd)


# 自动同步歌曲别名
for hour, minute, second in SyncMusicAliasConfig.get().sync_times:
    @scheduler.scheduled_job("cron", hour=hour, minute=minute, second=second)
    async def cron_statistic():
        logger.info("触发歌曲别名自动同步")
        await sync_music_alias()