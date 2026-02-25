from ...utils import *
from ..common import *
from ..handler import *
from .event import (
    get_current_event, 
    get_wl_chapter_cid,
    get_wl_events,
)
from .sk_sql import (
    Ranking, 
    query_ranks_with_interval,
)
import pandas as pd
import sys


# ============================= 数据获取 ============================= #

FORECAST_DATA_DIR = f"{SEKAI_DATA_DIR}/sk_forecast"


@dataclass
class ForecastRanking:
    score: int
    ts: int

@dataclass
class RankForecastData:
    final_score: int | None = None
    history_final_score: list[ForecastRanking] | None = None
    future_rankings: list[ForecastRanking] | None = None

@dataclass
class ForecastData:
    source: str
    region: str
    event_id: int
    mtime: int = None
    forecast_ts: int = None
    rank_data: dict[int, RankForecastData] = field(default_factory=dict)

    def get_save_path(self) -> str:
        return f"{FORECAST_DATA_DIR}/{self.source}/{self.region}/forecast/{self.event_id}.json"

    def load_from_local(self, update_interval_minutes: int | None = None) -> bool:
        """
        尝试从本地加载预测数据，失败或过期则返回 False
        """
        path = self.get_save_path()
        if not os.path.exists(path):
            return False
        mtime = int(os.path.getmtime(path))
        if update_interval_minutes is not None and int(time.time()) - mtime > update_interval_minutes * 60:
            return False
        data = load_json(path)
        self.mtime = int(mtime)
        self.forecast_ts = data['forecast_ts']
        self.rank_data = {}
        try:
            for rank_str, rank_info in data['rank_data'].items():
                rank = int(rank_str)
                future_rankings = None
                if rank_info.get('future_rankings'):
                    future_rankings = []
                    for fr in rank_info['future_rankings']:
                        future_rankings.append(ForecastRanking(
                            score=fr['score'],
                            ts=fr['ts'],
                        ))
                history_final_score = None
                if rank_info.get('history_final_score'):
                    history_final_score = []
                    for hr in rank_info['history_final_score']:
                        history_final_score.append(ForecastRanking(
                            score=hr['score'],
                            ts=hr['ts'],
                        ))
                self.rank_data[rank] = RankForecastData(
                    final_score=rank_info['final_score'],
                    history_final_score=history_final_score,
                    future_rankings=future_rankings,
                )
        except Exception as e:
            logger.warning(f"加载预测数据 {path} 失败: {get_exc_desc(e)}")
            return False
        return True

    def load_and_update_history(self):
        """
        加载历史数据到当前对象，并且追加最新的最终分数到历史数据中
        """
        old = ForecastData(
            source=self.source,
            region=self.region,
            event_id=self.event_id,
        )
        old.load_from_local()

        try:
            # 追加new到old
            for rank, data in self.rank_data.items():
                if data.final_score is not None:
                    old_history = []
                    if rank in old.rank_data and old.rank_data[rank].history_final_score is not None:
                        old_history = old.rank_data[rank].history_final_score or []
                    if not old_history or old_history[-1].ts != self.forecast_ts:
                        old_history.append(ForecastRanking(
                            score=data.final_score,
                            ts=self.forecast_ts,
                        ))
                    data.history_final_score = old_history
            # 加载new没有预测但是old有的历史
            for rank in old.rank_data:
                if rank not in self.rank_data:
                    self.rank_data[rank] = RankForecastData()
                if old.rank_data[rank].history_final_score is not None:
                    self.rank_data[rank].history_final_score = old.rank_data[rank].history_final_score
                    
        except Exception as e:
            logger.print_exc(f"追加 {self.source} {self.region}_{self.event_id} 历史数据失败: {get_exc_desc(e)}")

    def save_to_local(self):
        """
        将预测数据保存到本地
        """
        path = self.get_save_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'source': self.source,
            'region': self.region,
            'event_id': self.event_id,
            'forecast_ts': self.forecast_ts,
            'rank_data': {},
        }
        for rank, rank_info in self.rank_data.items():
            future_rankings = None
            if rank_info.future_rankings is not None:
                future_rankings = []
                for fr in rank_info.future_rankings:
                    future_rankings.append({
                        'score': fr.score,
                        'ts': fr.ts,
                    })
            history_final_score = None
            if rank_info.history_final_score is not None:
                history_final_score = []
                for hr in rank_info.history_final_score:
                    history_final_score.append({
                        'score': hr.score,
                        'ts': hr.ts,
                    })
            data['rank_data'][str(rank)] = {
                'final_score': rank_info.final_score,
                'history_final_score': rank_info.history_final_score,
                'future_rankings': future_rankings,
            }
        dump_json(data, path)
        self.mtime = int(os.path.getmtime(path))
        logger.info(f"已保存 {self.source} {self.region}_{self.event_id} 预测数据")



class GetForecastException(Exception):
    pass


async def get_local_forecast_data(region: str, event_id: int, chapter_id: int) -> ForecastData:
    return await run_local_forecast(region, event_id)

async def get_33kit_forecast_data(region: str, event_id: int, chapter_id: int) -> ForecastData | None:
    cfg = config.get('sk.forecast.33kit')
    data = ForecastData(
        source='33kit',
        region=region,
        event_id=event_id,
    )
    predict_data = await download_json(cfg['url'])
    event_id = predict_data['event']['id']
    if event_id != data.event_id:
        raise GetForecastException("最新活动预测未更新")
    data.forecast_ts = int(predict_data['data']['ts'] / 1000)
    for rank, score in predict_data['data'].items():
        if rank != 'ts':
            data.rank_data[int(rank)] = RankForecastData(final_score=score)
    return data

async def get_moe_forecast_data(region: str, event_id: int, chapter_id: int | None = None) -> ForecastData | None:
    cfg = config.get('sk.forecast.moe')
    data = ForecastData(
        source='moe',
        region=region,
        event_id=event_id,
    )
    # 从网页爬取
    # async with TempBotOrInternetFilePath('html', cfg['url']) as html_path:
    #     with open(html_path, 'r', encoding='utf-8') as f:
    #         html = f.read()

    #     if f'event_{event_id}' not in html:
    #         raise GetForecastException("最新活动预测未更新")

    #     rank_preds = {}

    #     pattern = re.compile(r'"Rank":(\d+),"CurrentScore":\d+,"PredictedScore":(\d+),')
    #     for match in pattern.finditer(html):
    #         rank = int(match.group(1))
    #         pred_score = int(match.group(2))
    #         rank_preds[rank] = pred_score
        
    #     data.forecast_ts = int(time.time())
    #     for rank, pred in rank_preds.items():
    #         data.rank_data[rank] = RankForecastData(final_score=pred)

    # 从公开API获取
    resp = await download_json(cfg['url'].format(region=region + '/' if region != 'cn' else '', event_id=event_id))
    for item in resp.get('data', {}).get('charts', []):
        rank = int(item['Rank'])
        if rank not in cfg['ranks']:
            continue
        pred_score = int(item['PredictedScore'])
        data.rank_data[rank] = RankForecastData(final_score=pred_score)
    if not data.rank_data:
        raise GetForecastException("最新活动预测未更新")
    data.forecast_ts = int(resp['timestamp'] / 1000)

    return data

async def get_sekarun_forecast_data(region: str, event_id: int, chapter_id: int | None = None) -> ForecastData | None:
    cfg = config.get('sk.forecast.sekarun')
    data = ForecastData(
        source='sekarun',
        region=region,
        event_id=event_id,
    )
    url = cfg['url'].format(region=region + '/' if region != 'jp' else '')
    async with TempBotOrInternetFilePath('text', url) as path:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        start = text.find("[[") + 2
        end = text.rfind("]]")
        cur = start
        while cur < end:
            stop = text.find("], [", cur)
            if stop == -1:
                stop = end
            row_text = text[cur:stop]
            if row_text.startswith(f'\"{event_id}'):
                values = row_text.replace("[", "").replace("]", "").split(", ")
                values = [v.strip().strip("'\"") for v in values]
                row_type = values[1]
                rank = int(values[5])
                if row_type == 'p' and rank in cfg['ranks']:
                    ts = int(values[6])
                    predict_lower = float(values[8])
                    predict_upper = float(values[9])
                    predict = int((predict_lower + predict_upper) / 2)
                    if not data.forecast_ts:
                        data.forecast_ts = ts
                    if not data.rank_data.get(rank):
                        data.rank_data[rank] = RankForecastData(final_score=0)
                    data.rank_data[rank].final_score = max(data.rank_data[rank].final_score, predict)
            cur = stop + 4
    if not data.forecast_ts:
        raise GetForecastException("最新活动预测未更新")
    return data


FORECAST_DATA_GET_FUNCS = {
    'local': get_local_forecast_data,
    '33kit': get_33kit_forecast_data,
    'moe': get_moe_forecast_data,
    'sekarun': get_sekarun_forecast_data,
}

_forecast_locks: dict[str, asyncio.Lock] = {
    source + region: asyncio.Lock() 
    for source in FORECAST_DATA_GET_FUNCS.keys() 
    for region in ALL_SERVER_REGIONS
}
_forecast_last_error_time: dict[str, datetime] = {}

    
async def get_forecast_data(region: str, event_id: int, chapter_id: int | None = None) -> list[ForecastData]:
    """
    获取指定活动的预测数据
    """
    async def task(region: str, source: str):
        sr = source + region
        async with _forecast_locks[sr]:
            try:
                cfg = config.get(f'sk.forecast.{source}')

                if not cfg.get('enabled', False):
                    return None
                if region not in cfg.get('regions'):
                    return None
                if chapter_id and not cfg.get('support_wl'):
                    return None

                # 尝试从本地获取
                data = ForecastData(
                    source=source,
                    region=region,
                    event_id=event_id,
                )
                if data.load_from_local(cfg['update_interval_minutes']):
                    return data
                
                # 尝试从数据源获取
                last_error_time = _forecast_last_error_time.get(sr, datetime.min)
                if datetime.now() - last_error_time < timedelta(minutes=cfg['error_retry_minutes']):
                    # logger.warning(f"{source} {region}预测距离上次获取失败时间不足 {cfg['error_retry_minutes']} 分钟，跳过本次获取")
                    if data.load_from_local():
                        return data
                    return None
                
                event = await SekaiHandlerContext.from_region(region).md.events.find_by_id(event_id)
                start_time = datetime.fromtimestamp(event['startAt'] / 1000)
                end_time = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
                if datetime.now() - start_time < timedelta(hours=config.get('sk.start_forecast_hours_after_event_start')):
                    raise GetForecastException(f"活动还没开始或刚开始")
                if end_time - datetime.now() < timedelta(hours=config.get('sk.stop_forecast_hours_before_event_end')):
                    raise GetForecastException(f"活动即将结束或已经结束")

                if source not in FORECAST_DATA_GET_FUNCS:
                    raise Exception(f"该来源 {source} {region} 未实现获取函数")
                data: ForecastData | None = await FORECAST_DATA_GET_FUNCS[source](region, event_id, chapter_id)
                if data is None:
                    raise Exception(f"该来源 {source}{region} 获取函数返回空数据")
                data.load_and_update_history()
                data.save_to_local()
                return data

            except GetForecastException as e:
                logger.warning(f"获取 {source} {region} 预测数据失败: {str(e)}")
                _forecast_last_error_time[sr] = datetime.now()
                if data.load_from_local():
                    return data
                return None

            except Exception as e:
                logger.print_exc(f"获取 {source} {region} 预测数据失败: {get_exc_desc(e)}")
                _forecast_last_error_time[sr] = datetime.now()
                if data.load_from_local():
                    return data
                return None

    results: list[ForecastData] = await batch_gather(*[
        task(region, source) for source in config.get('sk.forecast').keys()
    ])
    return [r for r in results if r is not None]


@repeat_with_interval(60, '更新预测数据', logger, every_output=False, error_limit=1)
async def _update_forecast_data():
    for region in ALL_SERVER_REGIONS:
        ctx = SekaiHandlerContext.from_region(region)

        event = await get_current_event(ctx)
        if event:
            await get_forecast_data(region, event['id'], None)

            wl_events = await get_wl_events(ctx, event['id'])
            if wl_events:
                for wl_event in wl_events:
                    chapter_id = wl_event['id'] // 1000
                    await get_forecast_data(region, event['id'], chapter_id)


# ============================= 本地预测 ============================= #

def get_local_forecast_history_csv_path(region: str, event_id: int) -> str:
    """
    获取指定活动的历史排名数据 CSV 文件路径
    """
    return f"{FORECAST_DATA_DIR}/local/{region}/history/{event_id}.csv"

async def save_rankings_to_csv(region: str, event_id: int, save_path: str):
    """
    将指定活动的历史排名数据保存为 CSV 文件用于本地预测
    """
    cfg = config.get('sk.forecast.local')
    sample_interval_seconds = cfg['sample_interval_minutes'] * 60
    ranks = cfg['ranks']
    data = await query_ranks_with_interval(
        region=region,
        event_id=event_id,
        ranks=ranks,
        sample_interval_seconds=sample_interval_seconds,
    )

    ctx = SekaiHandlerContext.from_region(region)
    event = await ctx.md.events.find_by_id(event_id)
    event_start = datetime.fromtimestamp(event['startAt'] / 1000)
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    
    data = [{
        'event_id': event_id,
        'to_end_hour': (event_end - x.time).total_seconds() / 3600,
        'from_start_hour': (x.time - event_start).total_seconds() / 3600,
        'score': x.score,
        'rank': x.rank,
        'timestamp': int(x.time.timestamp()),
    } for x in data]
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    tmp_path = save_path + ".tmp"
    pd.DataFrame(data).to_csv(tmp_path, index=False)
    os.replace(tmp_path, save_path)
    logger.info(f"已保存活动 {event_id} 的历史排名数据到 {save_path}")

async def run_local_forecast(region: str, event_id: int) -> ForecastData | None:
    """
    运行本地预测
    """
    cfg = config.get('sk.forecast.local')

    base_dir = f"{FORECAST_DATA_DIR}/local/{region}"
    history_csvs = glob.glob(f"{base_dir}/history/*.csv")
    result_csv = f"{base_dir}/forecast/{event_id}_future.csv"

    ctx = SekaiHandlerContext.from_region(region)
    event = await ctx.md.events.find_by_id(event_id)
    event_start = datetime.fromtimestamp(event['startAt'] / 1000)
    event_end = datetime.fromtimestamp(event['aggregateAt'] / 1000 + 1)
    if datetime.now() - event_start < timedelta(hours=cfg['start_after_hours']):
        raise GetForecastException(f"活动开始不足 {cfg['start_after_hours']} 小时，取消本地预测")
    if event_end - datetime.now() < timedelta(hours=cfg['end_before_hours']):
        raise GetForecastException(f"距离活动结束不足 {cfg['end_before_hours']} 小时，取消本地预测")

    with TempFilePath('.csv') as current_csv:
        await save_rankings_to_csv(region, event_id, current_csv)
        args = [
            sys.executable, "src/services/sk_forecast/cli.py", 
            "--history_csvs", ",".join(history_csvs),
            "--current_csv", current_csv,
            "--result_csv", result_csv,
            "--ranks", ",".join(map(str, cfg['ranks'])),
        ]

        logger.info(f"运行本地预测: {' '.join(args)}")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode().splitlines():
            logger.info(f"[Forecast] {line}")
        if proc.returncode != 0:
            raise Exception(f"returncode={proc.returncode}")
        
        logger.info(f"本地预测完成")
        
    data = ForecastData(
        source='local',
        region=region,
        event_id=event_id,
        forecast_ts=int(time.time()),
    )

    result = pd.read_csv(result_csv)
    
    for rank in result['rank'].unique():
        rank_data = result[result['rank'] == rank]
        final_score = int(rank_data.iloc[-1]['score'])
        future_rankings = []
        for _, row in rank_data.iterrows():
            future_rankings.append(ForecastRanking(
                score=int(row['score']),
                ts=int(row['timestamp']),
            ))
        data.rank_data[int(rank)] = RankForecastData(
            final_score=final_score,
            future_rankings=future_rankings,
        )

    if os.path.exists(result_csv):
        os.remove(result_csv)

    data.load_and_update_history()
    return data
