from .utils import *
from .master import MasterDataManager
from .gameapi import get_gameapi_config, request_gameapi, close_session
from .sql import insert_rankings, Ranking, close_conn
from tenacity import retry, wait_fixed, stop_after_attempt


set_log_level('INFO')

# 已丢弃的区服
# ALL_SERVER_REGIONS = ['jp', 'cn', 'tw', 'kr', 'en']

ALL_SERVER_REGIONS = ['jp', 'cn']

RECORD_TIME_AFTER_EVENT_END_CFG = config.item('sk.record_time_after_event_end_minutes')
RECORD_INTERVAL_CFG = config.item('sk.record_interval_seconds')
HIGH_RES_RECORD_INTERVAL_CFG = config.item('sk.high_res_record.interval_seconds')

mds = MasterDataManager('data/sekai/assets/masterdata')

latest_rankings_cache: dict[str, dict[int, dict[int, Ranking]]] = {}


# ================================ 处理逻辑 ================================ #

def get_wl_chapter_cid(region: str, wl_id: int) -> Optional[int]:
    """获取wl_id对应的角色cid，wl_id对应普通活动则返回None"""
    event_id = wl_id % 1000
    chapter_id = wl_id // 1000
    if chapter_id == 0:
        return None
    chapters = mds.get(region, 'worldBlooms').find_by('eventId', event_id, mode='all')
    assert chapters, f"活动{region}-{event_id}并不是WorldLink活动"
    chapter = find_by(chapters, "chapterNo", chapter_id)
    assert chapter, f"活动{region}-{event_id}并没有章节{chapter_id}"
    cid = chapter.get('gameCharacterId', None)
    return cid

def get_current_event(region: str, fallback: Optional[str] = None) -> dict:
    """
    获取当前活动 当前无进行中活动时 fallback = None:返回None prev:选择上一个 next:选择下一个 prev_first:优先选择上一个 next_first: 优先选择下一个
    """
    assert fallback is None or fallback in ("prev", "next", "prev_first", "next_first")
    events = sorted(mds.get(region, 'events').get(), key=lambda x: x['aggregateAt'], reverse=False)
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

def parse_rankings(region: str, event_id: int, data: dict) -> tuple[list[Ranking], list[Ranking]]:
    """从榜线数据解析Rankings，返回top100, border"""
    data_top100 = data.get('top100', {})
    data_border = data.get('border', {})
    assert data_top100, "获取榜线Top100数据失败"
    assert data_border, "获取榜线Border数据失败"

    now = datetime.now()

    # 普通活动
    if event_id < 1000:
        top100 = [Ranking.from_sk(item, now) for item in data_top100['rankings']]
        border = [Ranking.from_sk(item, now) for item in data_border['borderRankings'] if item['rank'] != 100]
    
    # WL活动
    else:
        cid = get_wl_chapter_cid(region, event_id)
        top100_rankings = find_by(data_top100.get('userWorldBloomChapterRankings', []), 'gameCharacterId', cid)
        top100 = [Ranking.from_sk(item, now) for item in top100_rankings['rankings']]
        border_rankings = find_by(data_border.get('userWorldBloomChapterRankingBorders', []), 'gameCharacterId', cid)
        border = [Ranking.from_sk(item, now) for item in border_rankings['borderRankings'] if item['rank'] != 100]

    for item in top100:
        item.uid = str(item.uid)
    for item in border:
        item.uid = str(item.uid)
    
    return top100, border

def get_wl_events(region: str, event_id: int) -> list[dict]:
    """获取event_id对应的所有wl_event（时间顺序），如果不是wl则返回空列表"""
    event = mds.get(region, 'events').find_by_id(event_id)
    chapters = mds.get(region, 'worldBlooms').find_by('eventId', event['id'], mode='all')
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

def check_ranking_in_high_res(region: str, ranking: Ranking) -> bool:
    """判断某条榜线记录是否需要高精度记录"""
    for rank_min, rank_max in config.get('sk.high_res_record.ranks', {}).get(region, []):
        if rank_min <= ranking.rank <= rank_max:
            return True
    for uid in config.get('sk.high_res_record.uids', {}).get(region, []):
        if str(ranking.uid) == str(uid):
            return True
    return False

def check_region_need_high_res(region: str) -> bool:
    """判断某个服务器是否需要高精度记录"""
    return config.get('sk.high_res_record.ranks', {}).get(region, []) or \
        config.get('sk.high_res_record.uids', {}).get(region, [])


# ================================ 榜线更新 ================================ #

class EventTracker:
    def __init__(self, region: str):
        self.region = region


    def info(self, *args, **kwargs):
        info(f"[{self.region.upper()}]", *args, **kwargs)

    def warning(self, *args, **kwargs):
        warning(f"[{self.region.upper()}]", *args, **kwargs)

    def error(self, *args, **kwargs):
        error(f"[{self.region.upper()}]", *args, **kwargs)
    
    def debug(self, *args, **kwargs):
        debug(f"[{self.region.upper()}]", *args, **kwargs)


    @retry(wait=wait_fixed(3), stop=stop_after_attempt(3), reraise=True)
    async def request_rankings(self, eid: int, url: str) -> tuple[dict, float]:
        """
        请求榜线数据，返回 (数据，耗时)
        """
        try:
            t = datetime.now().timestamp()
            data = await request_gameapi(url.format(event_id=eid))
            return (data, datetime.now().timestamp() - t)
        except Exception:
            self.error(f"请求榜线数据失败")
            return (None, datetime.now().timestamp() - t)
        

    async def update_rankings(self, eid: int, data: dict, is_high_res: bool) -> tuple[int, int, float]:
        """
        更新总榜或WL单榜，返回 (活动id, 插入数量, 耗时)
        """
        t = datetime.now().timestamp()
        region = self.region
        try:
            top100, borders = parse_rankings(region, eid, data)

            # 高精度记录模式：只记录必要的榜线
            if is_high_res:
                top100 = [item for item in top100 if check_ranking_in_high_res(region, item)]
                borders = [item for item in borders if check_ranking_in_high_res(region, item)]

            # 和缓存进行比对并更新缓存
            if region not in latest_rankings_cache:
                latest_rankings_cache[region] = {}
            if eid not in latest_rankings_cache[region]:
                latest_rankings_cache[region][eid] = {}

            # 前100强制更新
            rankings_to_insert: list[Ranking] = top100.copy()
            for item in top100:
                latest_rankings_cache[region][eid][item.rank] = item

            # borders仅发现任意榜线有更新才更新
            border_need_update = False
            for item in borders:
                last = latest_rankings_cache[region][eid].get(item.rank, None)
                if not last or last.score != item.score or last.uid != item.uid:
                    border_need_update = True
                    break
            if border_need_update:
                rankings_to_insert.extend(borders)
                for item in borders:
                    latest_rankings_cache[region][eid][item.rank] = item

            # 插入数据库
            if rankings_to_insert:
                await insert_rankings(region, eid, rankings_to_insert)

            return (eid, len(rankings_to_insert), datetime.now().timestamp() - t)

        except Exception as e:
            self.error(f"插入 {eid} 榜线数据失败: {get_exc_desc(e)}")
            return (eid, 0, datetime.now().timestamp() - t)


    async def update_region_ranking_task(self, is_high_res: bool) -> dict:
        """更新一次指定服务器的榜线数据，返回结果信息"""
        ret = { 'request_time': 0, 'inserts': [] }
        region = self.region
        url = get_gameapi_config(region).ranking_api_url
        if not url:
            return ret
            
        # 获取当前运行中的活动
        try:
            if not (event := get_current_event(region, fallback="prev")):
                self.info(f"当前无进行中或已结束活动，跳过榜线更新")
                close_conn(region)
                return ret
            if datetime.now() > datetime.fromtimestamp(event['aggregateAt'] / 1000 + RECORD_TIME_AFTER_EVENT_END_CFG.get() * 60):
                self.info(f"当前活动 {event['id']} 已过榜线记录时间，跳过榜线更新")
                close_conn(region)
                return ret
        except Exception as e:
            self.warning(f"检查当前活动时失败: {get_exc_desc(e)}")

        # 清空并非当前活动的缓存榜线数据
        event_id = event['id']
        for key in list(latest_rankings_cache.get(region, {}).keys()):
            if key % 1000 != event_id:
                latest_rankings_cache[region].pop(key)
                self.info(f"清除非当前活动 {key} 的榜线缓存数据")

        data, request_time = await self.request_rankings(event_id, url)
        ret['request_time'] = request_time

        if not data:
            return ret

        tasks = []
        # 总榜
        tasks.append(self.update_rankings(event_id, data, is_high_res))
        # WL单榜
        wl_events = get_wl_events(region, event_id)
        if wl_events and len(wl_events) > 1:
            for wl_event in wl_events:
                if datetime.now() > datetime.fromtimestamp(wl_event['aggregateAt'] / 1000 + RECORD_TIME_AFTER_EVENT_END_CFG.get() * 60):
                    continue
                tasks.append(self.update_rankings(wl_event['id'], data, is_high_res))

        if not tasks: 
            return ret
        
        for event_id, insert_num, cost_time in  await asyncio.gather(*tasks):
            ret['inserts'].append({
                'event_id': event_id,
                'insert_num': insert_num,
                'cost_time': cost_time
            })
        return ret


    async def start_track(self):
        self.info(f"榜线更新任务已启动")

        next_record_time = datetime.now()
        next_highres_record_time = datetime.now()
        next_time = datetime.now()

        while True:
            try:
                while datetime.now() < next_time:
                    await asyncio.sleep(0.5)

                need_high_res = check_region_need_high_res(self.region)

                start = datetime.now()
                is_high_res = need_high_res and datetime.now() < next_record_time

                result = await self.update_region_ranking_task(is_high_res)

                now = datetime.now()
                if not is_high_res:
                    next_record_time = start + timedelta(seconds=get_cfg_or_value(RECORD_INTERVAL_CFG))
                if need_high_res:
                    next_highres_record_time = start + timedelta(seconds=get_cfg_or_value(HIGH_RES_RECORD_INTERVAL_CFG))
                else:
                    next_highres_record_time = datetime.max
                next_time = min(next_record_time, next_highres_record_time)

                log_msg = f"完成{'高精度' if is_high_res else ''}更新"
                log_msg += f" | {(now - start).total_seconds():.2f}s | next: {next_time.strftime('%H:%M:%S')} | req: {result['request_time']:.2f}s"
                for insert_info in result.get('inserts', []):
                    log_msg += f" | event{insert_info['event_id']} +{insert_info['insert_num']} ({insert_info['cost_time']:.2f}s)"
                self.info(log_msg)

            except asyncio.CancelledError:
                break
        await close_session()



async def main():
    trackers = { region: EventTracker(region) for region in ALL_SERVER_REGIONS }
    tasks = []
    for region in ALL_SERVER_REGIONS:
        tasks.append(trackers[region].start_track())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    print("\nStarting Event Tracker...")
    asyncio.run(main())