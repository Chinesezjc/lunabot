from utils import *
from config import *

from sekai_deck_recommend_cpp import (
    SekaiDeckRecommend, 
    DeckRecommendOptions, 
    DeckRecommendCardConfig, 
    DeckRecommendSingleCardConfig,
    DeckRecommendResult,
    DeckRecommendUserData,
)
from hashlib import md5
import multiprocessing as mp
from multiprocessing import Queue, Process
from concurrent.futures import ThreadPoolExecutor
import setproctitle


class Worker:
    def log(self, *args, **kwargs):
        log(f"[worker-{self.worker_id}]", *args, **kwargs)

    def error(self, *args, **kwargs):
        error(f"<{self.worker_id}>", *args, **kwargs)

    def __init__(self, worker_id: int, worker_num: int):
        self.worker_id = worker_id
        self.worker_num = worker_num
        self.deckrec_seq_top = worker_id
        self.inited = False

    def init(self):
        if self.inited:
            return
        self.recommender = SekaiDeckRecommend()
        self.masterdata_version: dict[str, str] = {}
        self.masterdata_state: dict[str, str] = {}
        self.musicmetas_update_ts: dict[str, int] = {}
        self.userdata_cache: list[tuple[str, DeckRecommendUserData]] = []
        self.inited = True

    def _deckrec_options_to_str(self, userdata_hash: str, options: DeckRecommendOptions) -> str:
        def fmtbool(b: bool):
            return int(bool(b))
        def cardconfig2str(cfg: DeckRecommendCardConfig):
            return f"{fmtbool(cfg.disable)}{fmtbool(cfg.level_max)}{fmtbool(cfg.episode_read)}{fmtbool(cfg.master_max)}{fmtbool(cfg.skill_max)}"
        def singlecardcfg2str(cfg: List[DeckRecommendSingleCardConfig]):
            if not cfg:
                return "[]"
            return "[" + ", ".join(f"{c.card_id}:{cardconfig2str(c)}" for c in cfg) + "]"
        log = "("
        log += f"region={options.region}, "
        log += f"userdata_hash={userdata_hash}, "
        log += f"alg={options.algorithm}, "
        log += f"type={options.live_type}, "
        log += f"mid={options.music_id}, "
        log += f"mdiff={options.music_diff}, "
        log += f"eid={options.event_id}, "
        log += f"wl_cid={options.world_bloom_character_id}, "
        log += f"custom_bonus_attr={getattr(options, 'custom_bonus_attr', None)}, "
        custom_bonus_cids = getattr(options, 'custom_bonus_character_ids', None) or []
        log += f"custom_bonus_cids={list(custom_bonus_cids)}, "
        custom_bonus_support_units = getattr(options, "custom_bonus_character_support_units", None)
        log += f"custom_bonus_support_units={custom_bonus_support_units}, "
        log += f"challenge_cid={options.challenge_live_character_id}, "
        log += f"limit={options.limit}, "
        # log += f"member={options.member}, "
        # log += f"rarity1={cardconfig2str(options.rarity_1_config)}, "
        # log += f"rarity2={cardconfig2str(options.rarity_2_config)}, "
        # log += f"rarity3={cardconfig2str(options.rarity_3_config)}, "
        # log += f"rarity4={cardconfig2str(options.rarity_4_config)}, "
        # log += f"rarity_bd={cardconfig2str(options.rarity_birthday_config)}, "
        # log += f"single_card_cfg={singlecardcfg2str(options.single_card_configs)}, "
        log += f"fixed_cards={options.fixed_cards})"
        return log

    def _update_data(self, region: str):
        db = load_json(DB_PATH, default={})

        masterdata_version = db.get('masterdata_version', {}).get(region)
        masterdata_state = db.get('masterdata_state', {}).get(region)
        version_changed = self.masterdata_version.get(region) != masterdata_version
        state_changed = masterdata_state and self.masterdata_state.get(region) != masterdata_state
        if version_changed or state_changed:
            local_md_dir = pjoin(DATA_DIR, 'masterdata', region)
            self.recommender.update_masterdata(local_md_dir, region)
            self.masterdata_version[region] = masterdata_version
            if masterdata_state:
                self.masterdata_state[region] = masterdata_state
            reason = "指纹变化" if (not version_changed and state_changed) else "版本变化"
            self.log(f"加载 {region} MasterData: v{masterdata_version} ({reason})")

        musicmetas_update_ts = db.get('musicmetas_update_ts', {}).get(region)
        if self.musicmetas_update_ts.get(region) != musicmetas_update_ts:
            local_mm_path = pjoin(DATA_DIR, f'musicmetas_{region}.json')
            self.recommender.update_musicmetas(local_mm_path, region)
            self.musicmetas_update_ts[region] = musicmetas_update_ts
            self.log(f"加载 {region} MusicMetas: {datetime.fromtimestamp(musicmetas_update_ts).strftime('%Y-%m-%d %H:%M:%S')}")

    def cache_userdata(self, userdata_bytes: bytes) -> dict:
        self.init()
        try:
            hash = md5(userdata_bytes).hexdigest()
            for h, _ in self.userdata_cache:
                if h == hash:
                    return {
                        'status': 'success',
                        'userdata_hash': hash,
                    }
            userdata = DeckRecommendUserData()
            userdata.load_from_bytes(userdata_bytes)
            self.userdata_cache.append((hash, userdata))
            # self.log(f"缓存用户数据: hash={hash}")
            while len(self.userdata_cache) > USERDATA_CACHE_NUM:
                h, _ = self.userdata_cache.pop(0)
                # self.log(f"移除用户数据缓存: hash={h}")
            return {
                'status': 'success',
                'userdata_hash': hash,
            }
        except BaseException as e:
            self.error("缓存用户数据失败:", get_exc_desc(e))
            return {
                'status': 'error',
                'message': get_exc_desc(e),
            }
    
    def recommend(self, region: str, options: dict, userdata_hash: str) -> dict:
        self.init()
        seq = self.deckrec_seq_top
        self.deckrec_seq_top += self.worker_num
        
        try:
            self._update_data(region)

            if not self.masterdata_version.get(region) or not self.musicmetas_update_ts.get(region):
                return {
                    'status': 'error',
                    'message': '组卡服务端数据未初始化完成，请稍后再试'
                }
            
            user_data = None
            for h, data in self.userdata_cache:
                if h == userdata_hash:
                    user_data = data
                    break
            if user_data is None:
                return {
                    'status': 'error',
                    'message': '组卡服务端找不到对应的用户数据缓存'
                }

            # 兼容字段名：custom_bonus_support_units -> custom_bonus_character_support_units
            if (
                "custom_bonus_character_support_units" not in options
                and "custom_bonus_support_units" in options
            ):
                options["custom_bonus_character_support_units"] = options.get("custom_bonus_support_units")

            # 标准化 key 为 int
            support_units = options.get("custom_bonus_character_support_units")
            if isinstance(support_units, dict):
                normalized_support_units = {}
                for k, v in support_units.items():
                    try:
                        normalized_support_units[int(k)] = v
                    except Exception:
                        continue
                options["custom_bonus_character_support_units"] = normalized_support_units if normalized_support_units else None

            options = DeckRecommendOptions.from_dict(options)
            options.user_data = user_data
            self.log(f"组卡任务#{seq}: {self._deckrec_options_to_str(userdata_hash, options)}")

            start_time = datetime.now()
            res = self.recommender.recommend(options)
            cost_time = datetime.now() - start_time

            self.log(f"组卡任务#{seq}完成，耗时 {cost_time.total_seconds():.3f} 秒")

            return {
                'status': 'success',
                'result': res.to_dict(),
                'cost_time': cost_time.total_seconds(),
            }
        except BaseException as e:
            self.error(f"组卡任务#{seq}失败:", get_exc_desc(e))
            return {
                'status': 'error',
                'message': get_exc_desc(e),
            }


class WorkerContext:
    all_workers: dict[int, Worker] = {}
    all_processes: dict[int, Process] = {}
    available_workers: asyncio.Queue[Worker] = {}
    task_queues: dict[int, Queue] = {}
    result_queues: dict[int, Queue] = {}
    thread_pool: ThreadPoolExecutor = None

    @staticmethod
    def worker_loop(worker: Worker, task_queue: Queue, result_queue: Queue):
        setproctitle.setproctitle(f'lunabot-deckrec-worker-{worker.worker_id}')
        worker.log("Worker已启动")
        while True:
            task: tuple[str, tuple, dict] = task_queue.get()
            method_name, args, kwargs = task
            try:
                method = getattr(worker, method_name)
                result = method(*args, **kwargs)
                result_queue.put(result)
            except BaseException as e:
                worker.error(f"Worker 执行任务 {method_name} 失败:", get_exc_desc(e))
                result_queue.put({ 
                    'status': 'error',
                    'message': get_exc_desc(e),
                })

    @classmethod
    def init_workers(cls, worker_num: int):
        if mp.current_process().name == 'MainProcess':
            setproctitle.setproctitle('lunabot-deckrec-main')
        cls.all_workers = {}
        mp_ctx = mp.get_context('spawn')
        for i in range(worker_num):
            worker = Worker(i, worker_num)
            cls.all_workers[i] = worker
            cls.task_queues[i] = mp_ctx.Queue()
            cls.result_queues[i] = mp_ctx.Queue()
            p = mp_ctx.Process(
                target=cls.worker_loop, 
                args=(worker, cls.task_queues[i], cls.result_queues[i]),
            )
            p.start()
            cls.all_processes[i] = p
            
        cls.available_workers = asyncio.Queue()
        for w in cls.all_workers.values():
            cls.available_workers.put_nowait(w)
        cls.thread_pool = ThreadPoolExecutor(max_workers=worker_num)
        
    def __init__(self, task_timeout: float = 60) -> None:
        self.worker: Worker | None = None
        self.task_timeout = task_timeout

    async def __aenter__(self):
        if not self.available_workers:
            raise RuntimeError("Please call WorkerContext.init_workers() first")
        self.worker = await self.available_workers.get()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.available_workers.put_nowait(self.worker)

    @classmethod
    def workers(cls):
        for w in cls.all_workers.values():
            ctx = WorkerContext()
            ctx.worker = w
            yield ctx

    async def _get_result(self):
        try:
            return await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    self.thread_pool, 
                    self.result_queues[self.worker.worker_id].get,
                ),
                timeout=self.task_timeout,
            )
        except asyncio.TimeoutError:
            self.task_queues[self.worker.worker_id] = mp.get_context('spawn').Queue()
            self.result_queues[self.worker.worker_id] = mp.get_context('spawn').Queue()
            raise RuntimeError("Worker任务超时，队列已重置")

    async def cache_userdata(self, userdata_bytes: bytes) -> dict:
        self.task_queues[self.worker.worker_id].put(('cache_userdata', (userdata_bytes,), {},))
        return await self._get_result()
    
    async def recommend(self, region: str, options: dict, userdata_hash: str) -> dict:
        self.task_queues[self.worker.worker_id].put(('recommend', (region, options, userdata_hash,), {},))
        return await self._get_result()

