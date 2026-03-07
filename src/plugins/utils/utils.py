from ..common.config import *
from datetime import datetime, timedelta, timezone

# ============================ 启动时性能分析 ============================ #

if _profile_at_startup := global_config.get('profile_at_starup.enable'):
    import yappi
    _profile_at_startup_clock_type = global_config.get('profile_at_starup.clock_type')
    _profile_at_startup_seconds = global_config.get('profile_at_starup.seconds')
    yappi.set_clock_type(_profile_at_startup_clock_type)
    yappi.start()
    print(f"启动时性能分析已开启 (clocktype={_profile_at_startup_clock_type}, seconds={_profile_at_startup_seconds})", flush=True)

if _memray_at_startup := global_config.get('memray_at_startup.enable'):
    from memray import Tracker
    _memray_at_startup_seconds = global_config.get('memray_at_startup.seconds')
    _memray_save_path = f"data/misc/memray/{datetime.now().strftime('%Y%m%d_%H%M%S')}_memray.bin"
    os.makedirs(osp.dirname(_memray_save_path), exist_ok=True)
    _memray_tracker = Tracker(_memray_save_path, native_traces=True)
    _memray_tracker.__enter__()
    print(f"启动时内存分析已开启 (seconds={_memray_at_startup_seconds})", flush=True)


# ============================ 模块导入 ============================ #

from typing import Optional, List, Tuple, Dict, Union, Any, Set, Callable
import os
import os.path as osp
from os.path import join as pjoin
from pathlib import Path
from copy import deepcopy
import traceback
import orjson
import yaml
from uuid import uuid4
from dataclasses import dataclass, field, asdict
from tenacity import retry, stop_after_attempt, wait_fixed
import asyncio
import base64
import aiohttp
import random
import shutil
import re
import math
import io
import time
import zstandard

import faulthandler
faulthandler.enable()


from ..common.logger import get_logger, Logger, NumLimitLogger

utils_logger = get_logger('Utils')
profile_logger = get_logger('Profile')


# ============================ 启动/停止hook ============================ #

from nonebot import get_driver
_nonebot_driver = get_driver()

def on_startup():
    """
    注册启动时执行的函数装饰器
    """
    def wrapper(func: Callable):
        _nonebot_driver.on_startup(func)
        return func
    return wrapper

def on_shutdown():
    """
    注册停止时执行的函数装饰器
    """
    def wrapper(func: Callable):
        _nonebot_driver.on_shutdown(func)
        return func
    return wrapper


# ============================ 基础 ============================ #

class HttpError(Exception):
    def __init__(self, status_code: int = 500, message: str = ''):
        self.status_code = status_code
        self.message = message

    def __str__(self):
        return f"{self.status_code}: {truncate(self.message, 512)}"

def get_exc_desc(e: Exception) -> str:
    et = f"{type(e).__name__}" if type(e).__name__ not in ['Exception', 'AssertionError', 'ReplyException'] else ''
    e = str(e)
    if et and e: return f"{et}: {e}"
    else: return et + e

class ProfileTimer:
    def __init__(self, name: str = None):
        self.name = name
        self.start_time = None
        self.end_time = None

    def get(self) -> float:
        if self.start_time is None:
            raise Exception("Timer not started")
        if self.end_time is None:
            return (datetime.now() - self.start_time).total_seconds()
        else:
            return (self.end_time - self.start_time).total_seconds()

    def start(self):
        self.start_time = datetime.now()
    
    def end(self):
        self.end_time = datetime.now()
        if self.name and global_config.get('timer.enable'):
            log_prefixes = global_config.get('timer.log_prefix', [])
            if log_prefixes and self.name.startswith(tuple(log_prefixes)):
                profile_logger.profile(f"<{self.name}> cost {self.get():.3f}s ({self.start_time.strftime('%M:%S.%f')} - {self.end_time.strftime('%M:%S.%f')})")

    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb): 
        self.end()


# ============================ 集合操作 ============================ #

def count_dict(d: dict, level: int) -> int:
    """
    计算字典某个层级的元素个数
    """
    if level == 1:
        return len(d)
    else:
        return sum(count_dict(v, level-1) for v in d.values())

class Counter:
    def __init__(self):
        self.count = {}
    def inc(self, key, value=1):
        self.count[key] = self.count.get(key, 0) + value
    def get(self, key):
        return self.count.get(key, 0)
    def items(self):
        return self.count.items()
    def keys(self):
        return self.count.keys()
    def values(self):
        return self.count.values()
    def __len__(self):
        return len(self.count)
    def __str__(self):
        return str(self.count)
    def clear(self):
        self.count.clear()
    def __getitem__(self, key):
        return self.count.get(key, 0)
    def __setitem__(self, key, value):
        self.count[key] = value
    def keys(self):
        return self.count.keys()

def find_by(lst: List[Dict[str, Any]], key: str, value: Any, mode="first", use_str_compare=False):
    """
    用某个key查找某个dict列表中的元素 mode=first/last/all
    查找单个时找不到返回None, 查找多个时找不到返回空列表
    """
    # 检查是否出现类型不匹配
    if lst and key in lst[0]:
        if type(lst[0][key]) != type(value):
            utils_logger.warning(f"find_by 发现类型不匹配:\n{traceback.format_stack()}")

    if mode == "first":
        for item in lst:
            if key in item:
                if use_str_compare:
                    if str(item[key]) == str(value):
                        return item
                else:
                    if item[key] == value:
                        return item
        return None
    elif mode == "last":
        for item in reversed(lst):
            if key in item:
                if use_str_compare:
                    if str(item[key]) == str(value):
                        return item
                else:
                    if item[key] == value:
                        return item
        return None
    elif mode == "all":
        if use_str_compare:
            return [item for item in lst if key in item and str(item[key]) == str(value)]
        else:
            return [item for item in lst if key in item and item[key] == value]
    else:
        raise Exception("find_by mode must be first/last/all")

def unique_by(lst: List[Dict[str, Any]], key: str):
    """
    获取按某个key去重后的dict列表
    """
    val_set = set()
    ret = []
    for item in lst:
        if item[key] not in val_set:
            val_set.add(item[key])
            ret.append(item)
    return ret

def unique_idx_by(lst: List[Dict[str, Any]], key: str) -> List[int]:
    """
    获取按某个key去重后的dict列表，返回索引
    """
    val_set = set()
    ret = []
    for idx, item in enumerate(lst):
        if item[key] not in val_set:
            val_set.add(item[key])
            ret.append(idx)
    return ret

def remove_by(lst: List[Dict[str, Any]], key: str, value: Any):
    """
    获取删除某个key为某个值的所有项的dict列表
    """
    return [item for item in lst if key not in item or item[key] != value]

def find_by_predicate(lst: List[Any], predicate: Callable, mode="first"):
    """
    用某个条件查找某个列表中的元素 mode=first/last/all
    查找单个时找不到返回None, 查找多个时找不到返回空列表
    """
    if mode not in ["first", "last", "all"]:
        raise Exception("find_by_func mode must be first/last/all")
    ret = [item for item in lst if predicate(item)]
    if not ret: 
        return None if mode != "all" else []
    if mode == "first":
        return ret[0]
    if mode == "last":
        return ret[-1]
    return ret

def unique_by_predicate(lst: List[Any], predicate: Callable):
    """
    获取按某个条件去重后的dict列表
    """
    val_set = set()
    ret = []
    for item in lst:
        if predicate(item) not in val_set:
            val_set.add(predicate(item))
            ret.append(item)
    return ret

def remove_by_predicate(lst: List[Any], predicate: Callable):
    """
    获取删除某个条件的dict列表
    """
    return [item for item in lst if not predicate(item)]


# ============================ http session ============================ #

_global_client_session: Optional[aiohttp.ClientSession] = None

def get_client_session() -> aiohttp.ClientSession:
    global _global_client_session
    if _global_client_session is None or _global_client_session.closed:
        _global_client_session = aiohttp.ClientSession(trust_env=True)
    return _global_client_session

@on_shutdown()
async def _close_session():
    if _global_client_session is not None and not _global_client_session.closed:
        await _global_client_session.close()


# ============================ 异步和任务 ============================ #

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except:
    print("uvloop not installed, using default asyncio event loop")

from nonebot_plugin_apscheduler import scheduler

from concurrent.futures import ThreadPoolExecutor
_default_pool_executor = ThreadPoolExecutor(max_workers=global_config.get('default_thread_pool_size'))

_pending_startup_tasks: List[Any] = []

STARTUP_TASK_MIN_DELAY = global_config.get('startup_task_delay_seconds.min')
STARTUP_TASK_MAX_DELAY = global_config.get('startup_task_delay_seconds.max')

async def run_in_pool(func, *args, pool=None):
    if pool is None:
        global _default_pool_executor
        pool = _default_pool_executor
    return await asyncio.get_event_loop().run_in_executor(pool, func, *args)

def run_in_pool_nowait(func, *args):
    return asyncio.get_event_loop().run_in_executor(_default_pool_executor, func, *args)

def start_repeat_with_interval(
    interval: int | ConfigItem,
    func: Callable,
    logger: 'Logger',
    name: str,
    every_output=False, 
    error_output=True, 
    error_limit=5, 
    delay=None
):
    """
    开始重复执行某个异步任务
    """
    if delay is None:
        delay = random.uniform(STARTUP_TASK_MIN_DELAY, STARTUP_TASK_MAX_DELAY)
    async def task():
        await asyncio.sleep(delay)
        try:
            error_count = 0
            logger.info(f'开始循环执行 {name} 任务', flush=True)
            next_time = datetime.now() + timedelta(seconds=1)
            while True:
                now_time = datetime.now()
                if next_time > now_time:
                    try:
                        await asyncio.sleep((next_time - now_time).total_seconds())
                    except asyncio.exceptions.CancelledError:
                        return
                    except Exception as e:
                        logger.print_exc(f'循环执行 {name} sleep失败')
                next_time = next_time + timedelta(seconds=get_cfg_or_value(interval))
                try:
                    if every_output:
                        logger.debug(f'开始执行 {name}')
                    await call_common_or_async(func)
                    if every_output:
                        logger.info(f'执行 {name} 成功')
                    if error_output and error_count > 0:
                        logger.info(f'循环执行 {name} 从错误中恢复, 累计错误次数: {error_count}')
                    error_count = 0
                except Exception as e:
                    if error_output and error_count < error_limit - 1:
                        logger.warning(f'循环执行 {name} 失败: {e} (失败次数 {error_count + 1})')
                    elif error_output and error_count == error_limit - 1:
                        logger.print_exc(f'循环执行 {name} 失败 (达到错误次数输出上限)')
                    error_count += 1

        except Exception as e:
            logger.print_exc(f'循环执行 {name} 任务失败')
    _pending_startup_tasks.append(task)

def repeat_with_interval(
    interval_secs: int | ConfigItem, 
    name: str, 
    logger: 'Logger', 
    every_output=False, 
    error_output=True, 
    error_limit=5, 
    delay=None
):
    """
    重复执行某个任务的装饰器
    """
    def wrapper(func):
        start_repeat_with_interval(interval_secs, func, logger, name, every_output, error_output, error_limit, delay)
        return func
    return wrapper

def start_async_task(func: Callable, logger: 'Logger', name: str, delay=None):   
    """
    开始异步执行某个任务
    """
    if delay is None:
        delay = random.uniform(STARTUP_TASK_MIN_DELAY, STARTUP_TASK_MAX_DELAY)
    async def task():
        await asyncio.sleep(delay)
        try:
            logger.info(f'开始异步执行 {name} 任务', flush=True)
            await call_common_or_async(func)
        except Exception as e:
            logger.print_exc(f'异步执行 {name} 任务失败')
    _pending_startup_tasks.append(task)

def async_task(name: str, logger: 'Logger', delay=None):
    """
    异步执行某个任务的装饰器
    """
    def wrapper(func):
        start_async_task(func, logger, name, delay)
        return func
    return wrapper  

async def batch_gather(*futs_or_coros, batch_size=32) -> List[Any]:
    """
    批量执行异步任务，分批处理以避免过多并发导致性能下降
    """
    results = []
    for i in range(0, len(futs_or_coros), batch_size):
        results.extend(await asyncio.gather(*futs_or_coros[i:i + batch_size]))
    return results

# 启动时_pending_start_tasks
@scheduler.scheduled_job('date', run_date=datetime.now(), misfire_grace_time=60)
async def _create_pending_startup_tasks():
    for task in _pending_startup_tasks:
        asyncio.create_task(task())
    _pending_startup_tasks.clear()

async def call_common_or_async(func: Callable, *args, **kwargs):
    """
    调用一个可能是异步的函数
    """
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    else:
        return func(*args, **kwargs)


# ============================ 字符串 ============================ #

from zhon import hanzi
_clean_name_pattern = rf"[{re.escape(hanzi.punctuation)}\s]"
def clean_name(s: str) -> str:
    """
    获取用于搜索匹配的干净字符串
    """
    s = re.sub(_clean_name_pattern, "", s).lower()
    import zhconv
    s = zhconv.convert(s, 'zh-cn')
    return s

def get_md5(s: Union[str, bytes]) -> str:
    import hashlib
    m = hashlib.md5()
    if isinstance(s, str): s = s.encode()
    m.update(s)
    return m.hexdigest()

def levenshtein_distance(s1: str, s2: str) -> int:
    """
    计算两个字符串之间的Levenshtein距离
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)

    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]

def get_readable_file_size(size: int) -> str:
    """
    将文件大小(byte)转换为可读字符串
    """
    if size < 1024:
        return f"{size}B"
    size /= 1024
    if size < 1024:
        return f"{size:.2f}KB"
    size /= 1024
    if size < 1024:
        return f"{size:.2f}MB"
    size /= 1024
    return f"{size:.2f}GB"

def get_readable_datetime(t: datetime, show_original_time=True, use_en_unit=False):
    """
    将时间点转换为可读字符串
    """
    day_unit, hour_unit, minute_unit, second_unit = ("天", "小时", "分钟", "秒") if not use_en_unit else ("d", "h", "m", "s")
    now = datetime.now()
    diff = t - now
    text, suffix = "", "后"
    if diff.total_seconds() < 0:
        suffix = "前"
        diff = -diff
    if diff.total_seconds() < 60:
        text = f"{int(diff.total_seconds())}{second_unit}"
    elif diff.total_seconds() < 60 * 60:
        text = f"{int(diff.total_seconds() / 60)}{minute_unit}"
    elif diff.total_seconds() < 60 * 60 * 24:
        text = f"{int(diff.total_seconds() / 60 / 60)}{hour_unit}{int(diff.total_seconds() / 60 % 60)}{minute_unit}"
    else:
        text = f"{diff.days}{day_unit}"
    text += suffix
    if show_original_time:
        text = f"{t.strftime('%Y-%m-%d %H:%M:%S')} ({text})"
    return text

def get_readable_timedelta(delta: timedelta, precision: str = 'm', use_en_unit=False) -> str:
    """
    将时间段转换为可读字符串
    """
    match precision:
        case 's': precision = 3
        case 'm': precision = 2
        case 'h': precision = 1
        case 'd': precision = 0

    s = int(delta.total_seconds())
    if s <= 0: return "0秒" if not use_en_unit else "0s"
    d = s // (24 * 3600)
    s %= (24 * 3600)
    h = s // 3600
    s %= 3600
    m = s // 60
    s %= 60

    ret = ""
    if d > 0: 
        ret += f"{d}天" if not use_en_unit else f"{d}d"
    if h > 0 and (precision >= 1 or not ret): 
        ret += f"{h}小时" if not use_en_unit else f"{h}h"
    if m > 0 and (precision >= 2 or not ret):
        ret += f"{m}分钟" if not use_en_unit else f"{m}m"
    if s > 0 and (precision >= 3 or not ret):
        ret += f"{s}秒"   if not use_en_unit else f"{s}s"
    return ret

def truncate(s: str, limit: int) -> str:
    """
    截断字符串到指定长度，中文字符算两个字符
    """
    s = str(s)
    if s is None: return "<None>"
    l = 0
    for i, c in enumerate(s):
        if l >= limit:
            return s[:i] + "..."
        l += 1 if ord(c) < 128 else 2
    return s

def get_str_display_length(s: str) -> int:
    """
    获取字符串的显示长度，中文字符算两个字符
    """
    l = 0
    for c in s:
        l += 1 if ord(c) < 128 else 2
    return l

def get_str_line_count(s: str, line_length: int) -> int:
    """
    获取字符串在指定行长度下的行数
    """
    lines = [""]
    for c in s:
        if c == '\n':
            lines.append("")
            continue
        if get_str_display_length(lines[-1] + c) > line_length:
            lines.append("")
        lines[-1] += c
    return len(lines)

def get_float_str(value: float, precision: int = 2, remove_zero: bool = True) -> str:
    """
    将浮点数转换为字符串，保留指定小数位数，并可选择去除末尾的零
    """
    ret = f"{value:.{precision}f}"
    if remove_zero:
        ret = ret.rstrip('0').rstrip('.')
    return ret

def get_date_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def compress_zstd(b: bytes):
    return zstandard.ZstdCompressor().compress(b)

def decompress_zstd(b: bytes):
    return zstandard.ZstdDecompressor().decompress(b, max_output_size=100*1024*1024)


# ============================ 文件 ============================ #

def load_json(file_path: str) -> dict:
    with open(file_path, 'rb') as file:
        return orjson.loads(file.read())
    
def dump_json(data: dict, file_path: str, indent: bool = True) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    # 首先保存到临时文件，保存成功后再替换原文件，避免写入过程中程序崩溃导致文件损坏
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, 'wb') as file:
        buffer = orjson.dumps(data, option=orjson.OPT_INDENT_2 if indent else 0)
        file.write(buffer)
    os.replace(tmp_path, file_path)
    try: os.remove(tmp_path)
    except: pass

def loads_json(s: str | bytes) -> dict:
    return orjson.loads(s)

def dumps_json(data: dict, indent: bool = True) -> str:
    return orjson.dumps(data, option=orjson.OPT_INDENT_2 if indent else 0).decode('utf-8')

def dump_bytes_json(data: dict) -> bytes:
    return orjson.dumps(data)

def create_folder(folder_path) -> str:
    """
    创建文件夹，返回文件夹路径
    """
    folder_path = str(folder_path)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def create_parent_folder(file_path) -> str:
    """
    创建文件所在的文件夹，返回文件路径
    """
    parent_folder = os.path.dirname(file_path)
    create_folder(parent_folder)
    return file_path

def remove_folder(folder_path):
    folder_path = str(folder_path)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)

def remove_file(file_path):
    if os.path.exists(file_path):
        os.remove(file_path)

def rand_filename(ext: str) -> str:
    if ext.startswith('.'):
        ext = ext[1:]
    return f'{uuid4()}.{ext}'

TEMP_FILE_DIR = 'data/utils/tmp'
_tmp_files_to_remove: list[Tuple[str, datetime]] = []

class TempFilePath:
    """
    临时文件路径
    remove_after为None表示使用后立即删除，否则延时删除
    """
    def __init__(self, ext: str, remove_after: timedelta = None):
        self.ext = ext
        self.path = os.path.abspath(pjoin(TEMP_FILE_DIR, rand_filename(ext)))
        self.remove_after = remove_after
        create_parent_folder(self.path)

    def __enter__(self) -> str:
        return self.path
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.remove_after is None:
            # utils_logger.info(f'删除临时文件 {self.path}')
            remove_file(self.path)
        else:
            _tmp_files_to_remove.append((self.path, datetime.now() + self.remove_after))

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_file(url, file_path):
    """
    下载文件到指定路径
    """
    async with get_client_session().get(url, verify_ssl=False) as resp:
        if resp.status != 200:
            raise Exception(f"下载文件 {truncate(url, 32)} 失败: {resp.status} {resp.reason}")
        with open(file_path, 'wb') as f:
            f.write(await resp.read())

class TempDownloadFilePath(TempFilePath):
    def __init__(self, url, ext: str = None, remove_after: timedelta = None):
        self.url = url
        if ext is None:
            ext = url.split('.')[-1]
        super().__init__(ext, remove_after)

    async def __aenter__(self) -> str:
        await download_file(self.url, self.path)
        return super().__enter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return super().__exit__(exc_type, exc_val, exc_tb)

def read_file_as_base64(file_path) -> str:
    """
    读取文件并返回base64编码的字符串
    """
    with open(file_path, 'rb') as f:
        return base64.b64encode(f.read()).decode()

async def aload_json(path: str) -> dict:
    """
    异步加载json文件
    """
    return await run_in_pool(load_json, path)

async def adump_json(data: dict, path: str):
    """
    异步保存json文件
    """
    return await run_in_pool(dump_json, data, path)

async def download_json(url: str) -> dict:
    """
    异步下载json文件
    """
    headers = {
        'Accept-Language': 'en',
    }
    async with get_client_session().get(url, headers=headers, verify_ssl=False) as resp:
        if resp.status != 200:
            try:
                detail = await resp.text()
                detail = loads_json(detail)['detail']
            except:
                pass
            utils_logger.error(f"下载 {url} 失败: {resp.status} {detail}")
            raise HttpError(resp.status, detail)
        if "text/plain" in resp.content_type:
            return loads_json(await resp.text())
        if "application/octet-stream" in resp.content_type:
            import io
            return loads_json(io.BytesIO(await resp.read()).read())
        return await resp.json()

def load_json_zstd(file_path: str) -> dict:
    with open(file_path, 'rb') as file:
        data = zstandard.ZstdDecompressor().decompress(file.read())
        return orjson.loads(data)

def dump_json_zstd(data: dict, file_path: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp_path = file_path + ".tmp"
    with open(tmp_path, 'wb') as file:
        buffer = orjson.dumps(data)
        compressed = zstandard.ZstdCompressor().compress(buffer)
        file.write(compressed)
    os.replace(tmp_path, file_path)
    try: os.remove(tmp_path)
    except: pass

async def aload_json_zstd(path: str) -> dict:
    """
    异步加载zstd压缩的json文件
    """
    return await run_in_pool(load_json_zstd, path)

async def adump_json_zstd(data: dict, path: str):
    """
    异步保存zstd压缩的json文件
    """
    return await run_in_pool(dump_json_zstd, data, path)


# ============================ 文件数据库 ============================ #

FILE_DB_SAVE_INTERVAL_CFG = global_config.item('file_db.save_interval_seconds')

class FileDB:
    _updated_dbs: set['FileDB'] = set()

    def __init__(self, path: str, logger: Logger):
        self.path = os.path.abspath(path)
        self.data = {}
        self.logger = logger
        self.loaded = False

    def __hash__(self) -> int:
        return hash(self.path)
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileDB):
            return False
        return self.path == other.path

    def _ensure_load(self):
        if self.loaded:
            return
        try:
            self.data = load_json(self.path)
            self.logger.debug(f'加载数据库 {self.path} 成功')
        except:
            self.logger.debug(f'加载数据库 {self.path} 失败 使用空数据')
            self.data = {}
        self.loaded = True

    def _after_change(self):
        if FILE_DB_SAVE_INTERVAL_CFG.get() == 0:
            self.save()
        else:
            FileDB._updated_dbs.add(self)

    def _get_last_dict_and_key(self, key: str, create_path: bool = False) -> tuple[dict | None, str | None]:
        """
        - 从多层key获取最后一层dict和key
        - 设置create_path时找不到会创建直到last_dict的路径，否则返回(None,None)
        - 设置create_path后需要自行保证_after_change被调用
        """
        assert isinstance(key, str), f'key: "{key}" 必须是字符串，当前类型: {type(key)}'
        self._ensure_load()
        key = key.replace("\.", "&#46;")
        keys = key.split('.')
        last_dict = self.data
        last_key = keys.pop()
        for k in keys:
            k = k.replace("&#46;", ".")
            if k not in last_dict or not isinstance(last_dict[k], dict):
                if create_path:
                    last_dict[k] = {}
                else:
                    return None, None
            last_dict = last_dict[k]
        return last_dict, last_key
    

    def keys(self) -> Set[str]:
        """
        - 获取所有第一层key的集合
        """
        self._ensure_load()
        return self.data.keys()

    def save(self):
        """
        - 保存数据到文件，在修改后会被自动调用，一般不需要手动调用
        """
        try:
            self._ensure_load()
            dump_json(self.data, self.path)
            self.logger.debug(f'保存数据库 {self.path}')
        except:
            self.logger.print_exc(f'保存数据库 {self.path} 失败')

    def get(self, key: str, default: Any=None) -> Any:
        """
        - 获取某个key的值，找不到返回default
        - 支持多层key，用点号分隔，如"a.b.c"
        - 直接返回缓存对象，若要进行修改又不影响DB内容则必须自行deepcopy，或者使用get_copy方法
        """
        self._ensure_load()
        d, k = self._get_last_dict_and_key(key)
        if d is None:
            return default
        return d.get(k, default)

    def get_copy(self, key: str, default: Any=None) -> Any:
        """
        - 获取某个key的值的深拷贝，找不到返回default的深拷贝
        - 支持多层key，用点号分隔，如"a.b.c"
        """
        self._ensure_load()
        d, k = self._get_last_dict_and_key(key)
        if d is None:
            return deepcopy(default)
        return deepcopy(d.get(k, default))

    def set(self, key: str, value: Any):
        """
        - 设置某个key的值，会自动保存
        - 支持多层key，用点号分隔，如"a.b.c"
        """
        self._ensure_load()
        self.logger.debug(f'设置数据库 {self.path} {key}')
        d, k = self._get_last_dict_and_key(key, create_path=True)
        d[k] = value
        self._after_change()

    def delete(self, key: str):
        """
        - 删除某个key的值，会自动保存
        - 支持多层key，用点号分隔，如"a.b.c"
        """
        self._ensure_load()
        self.logger.debug(f'删除数据库 {self.path} {key}')
        d, k = self._get_last_dict_and_key(key)
        if d is not None and k in d:
            del d[k]
            self._after_change()

    @classmethod
    def save_all_changed(cls):
        """
        - 保存所有修改过的数据库
        """
        for db in list(cls._updated_dbs):
            db.save()
        cls._updated_dbs.clear()


@repeat_with_interval(FILE_DB_SAVE_INTERVAL_CFG, '保存文件数据库', utils_logger)
async def _save_changed_file_dbs():
    FileDB.save_all_changed()

@on_shutdown()
def _save_all_file_dbs_on_shutdown():
    FileDB.save_all_changed()


_file_dbs: Dict[str, FileDB] = {}
def get_file_db(path: str, logger: Logger) -> FileDB:
    global _file_dbs
    if path not in _file_dbs:
        _file_dbs[path] = FileDB(path, logger)
    return _file_dbs[path]

utils_file_db = get_file_db('data/utils/db.json', utils_logger)


# ============================ Playwright ============================ #

from playwright.async_api import (
    async_playwright, 
    Browser, 
    Playwright, 
    BrowserType, 
    BrowserContext, 
    Page,
    Error as PlaywrightError
)

_playwright_instance: Playwright | None = None
_browser_type: BrowserType | None = NotImplementedError
_playwright_browser: Browser | None = None

MAX_CONTEXTS = global_config.get("playwright.context_num")
_context_semaphore = asyncio.Semaphore(MAX_CONTEXTS)

class PlaywrightPage:
    """
    异步上下文管理器，用于管理 Playwright 的context。
    """
    def __init__(self, context_options: dict | None = None):
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.context_options: dict = context_options if context_options is not None else { 
            'locale': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        }

    async def __aenter__(self) -> Page:
        global _playwright_instance, _browser_type, _playwright_browser
        # 检查浏览器的情况
        if _playwright_browser is None or not _playwright_browser.is_connected():

            if _playwright_instance is None: 
                # 启动async_playwright实例
                _playwright_instance = await async_playwright().start()
                utils_logger.info("初始化 Playwright 异步 API")
                pass
            
            # 获取配置
            pw_cfg = global_config.get("playwright", {})
            remote_url :str = pw_cfg.get("remote_url", "")
            browser_type_str = pw_cfg.get("browser_type", "chromium")
            
            _browser_type = getattr(_playwright_instance, browser_type_str)

            if remote_url:
                utils_logger.info(f"正在连接远程 Playwright 浏览器: {remote_url}")
                if remote_url.startswith("ws://") or remote_url.startswith("wss://"):
                    _playwright_browser = await _browser_type.connect(remote_url, timeout=30000)
                else:
                    _playwright_browser = await _browser_type.connect_over_cdp(remote_url, timeout=30000)
                utils_logger.info(f"成功连接至远程浏览器")
            else:
                # 清除本地临时文件
                if os.system("rm -rf /tmp/rust_mozprofile*") != 0:
                    utils_logger.error(f"清空WebDriver临时文件失败")
                # 启动浏览器
                _playwright_browser = await _browser_type.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox'],
                )
                utils_logger.info(f"启动本地 Playwright Browser")
            pass
        # 限制context的数量
        await _context_semaphore.acquire()
        try:
            self.context = await _playwright_browser.new_context(**self.context_options)
        except PlaywrightError as pe:
            # 在新建context时就发生异常，可以认为playwright本身出了问题，重启一下
            try:
                _playwright_browser.close()
            except Exception as e:
                utils_logger.error(f"关闭 Playwright Browser 失败 {get_exc_desc(e)}")
            _playwright_browser = None
            _context_semaphore.release()
            raise pe
        except: 
            # 出现异常时释放信号
            _context_semaphore.release()
            raise
        self.page = await self.context.new_page()
        return self.page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 关闭上下文，自动清理
        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                utils_logger.error(f"关闭 Playwright Page 失败 {get_exc_desc(e)}")
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                utils_logger.error(f"关闭 Playwright Context 失败 {get_exc_desc(e)}")
            finally:# 释放信号
                _context_semaphore.release()
        self.page = None
        self.context = None
        return False


# ============================ 图片处理 ============================ #

from ..draw.plot import *
from ..draw.img_utils import *
import ffmpeg

def get_image_pixel_hash(img: Image.Image):
    return hashlib.md5(img.tobytes()).hexdigest()

def get_image_b64(image: Image.Image) -> str:
    """
    转化PIL图片为带 "data:image/jpeg;base64," 前缀的base64字符串
    """
    with TempFilePath('jpg') as tmp_path:
        image.convert('RGB').save(tmp_path, "JPEG")
        with open(tmp_path, "rb") as f:
            return f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode('utf-8')}"

def b64_to_image(b64_str: str) -> Image.Image:
    """
    将带 "data:image/xxx;base64," 前缀的base64字符串转化为PIL图片
    """
    if b64_str.startswith("data:image"):
        b64_str = b64_str.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(b64_str)))

async def download_image_to_b64(image_path) -> str:
    """
    下载并编码指定路径的图片为带 "data:image/jpeg;base64," 前缀的base64字符串
    """
    img = (await download_image(image_path))
    return get_image_b64(img)

def plt_fig_to_image(fig, transparent=True, tight=False) -> Image.Image:
    """
    matplot图像转换为PIL.Image对象
    """
    buf = io.BytesIO()
    if tight:
        fig.savefig(buf, transparent=transparent, format='png', bbox_inches='tight', pad_inches=0.1)
    else:
        fig.savefig(buf, transparent=transparent, format='png')
    buf.seek(0)
    img = Image.open(buf)
    img.load()
    return img

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_image(image_url, force_http=False) -> Image.Image:
    """
    下载图片并返回PIL.Image对象
    """
    if force_http and image_url.startswith("https"):
        image_url = image_url.replace("https", "http")
    async with get_client_session().get(image_url, verify_ssl=False) as resp:
        if resp.status != 200:
            utils_logger.error(f"下载图片 {image_url} 失败: {resp.status} {resp.reason}")
            raise HttpError(resp.status, f"下载图片 {image_url} 失败")
        image = await resp.read()
        return Image.open(io.BytesIO(image))

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_and_convert_svg(svg_url: str) -> Image.Image:
    """
    下载SVG图片并转换为PIL.Image对象
    """
    async with PlaywrightPage() as page: # Playwright Page 对象
        try:
            await page.goto(svg_url, wait_until="domcontentloaded")
            svg_locator = page.locator('svg').nth(0)

            bounding_box = await svg_locator.bounding_box()
            if not bounding_box:
                raise Exception("未找到SVG元素或元素不可见")
            
            width = int(bounding_box['width'])
            height = int(bounding_box['height'])
            await page.set_viewport_size({"width": width, "height": height})
            
            with TempFilePath('png') as path:
                await svg_locator.screenshot(path=path)
                return open_image(path)
        except Exception:
            utils_logger.print_exc(f'下载SVG图片失败')
            return None 

async def markdown_to_image(markdown_text: str, width: int = 600) -> Image.Image:
    """
    将markdown文本转换为图片
    """
    async with PlaywrightPage() as page: # WebDriver 返回 Playwright Page
        css_content = Path("data/utils/m2i/m2i.css").read_text()
        try:
            import mistune
            md_renderer = mistune.create_markdown()
            html = md_renderer(markdown_text)
            full_html = f"""
<html>
    <head><style>
        {css_content}
        .markdown-body {{
            padding: 32px;
        }}
    </style></head>
    <body class="markdown-body">{html}</body>
</html>"""

            with TempFilePath('html') as html_path:
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(full_html)
                
                await page.goto(f"file://{osp.abspath(html_path)}", wait_until="load")
                await page.set_viewport_size({"width": width, "height": 1})
                
                with TempFilePath('png') as img_path:
                    await page.screenshot(path=img_path, full_page=True)
                    return open_image(img_path)

        except Exception:
            utils_logger.print_exc(f'markdown转图片失败')
            return None

def convert_video_to_gif(video_path: str, save_path: str, max_fps=10, max_size=256, max_frame_num=200):
    """
    将视频转换为GIF格式
    """
    utils_logger.info(f'转换视频为GIF: {video_path}')
    probe = ffmpeg.probe(video_path)
    video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
    frame_num = int(video_stream['nb_frames'])
    fps = float(video_stream['avg_frame_rate'].split('/')[0]) / float(video_stream['avg_frame_rate'].split('/')[1])
    duration = frame_num / fps
    max_fps = max(min(max_fps, int(max_frame_num / duration)), 1)
    width, height = video_stream['width'], video_stream['height']
    if width > height:
        if width > max_size:
            height = int(height * max_size / width)
            width = max_size
    else:
        if height > max_size:
            width = int(width * max_size / height)
            height = max_size

    palette_stream = ffmpeg.input(video_path).filter_multi_output('split')[0].filter('palettegen')
    video_stream = ffmpeg.input(video_path)
    filtered_video_stream = video_stream.filter('fps', fps=fps).filter('scale', width=width, height=-1, flags='lanczos')
    stream = ffmpeg.filter([filtered_video_stream, palette_stream], 'paletteuse')
    stream = ffmpeg.output(stream, save_path)
    ffmpeg.run(stream, overwrite_output=True, quiet=True)
    
def concat_images(images: List[Image.Image], mode) -> Image.Image:
    """
    拼接图片，mode: 'v' 垂直拼接 'h' 水平拼接 'g' 网格拼接
    """
    if mode == 'v':
        max_w = max(img.width for img in images)
        images = [
            img if img.width == max_w 
            else img.resize((max_w, int(img.height * max_w / img.width))) 
            for img in images
        ]
        ret = Image.new('RGBA', (max_w, sum(img.height for img in images)))
        y = 0
        for img in images:
            img = img.convert('RGBA')
            ret.paste(img, (0, y), img)
            y += img.height
        return ret
    
    elif mode == 'h':
        max_h = max(img.height for img in images)
        images = [
            img if img.height == max_h 
            else img.resize((int(img.width * max_h / img.height), max_h)) 
            for img in images
        ]
        ret = Image.new('RGBA', (sum(img.width for img in images), max_h))
        x = 0
        for img in images:
            img = img.convert('RGBA')
            ret.paste(img, (x, 0), img)
            x += img.width
        return ret

    elif mode == 'g':
        max_w = max(img.width for img in images)
        max_h = max(img.height for img in images)
        cols = int(math.sqrt(len(images)))
        rows = (len(images) + cols - 1) // cols
        ret = Image.new('RGBA', (max_w * cols, max_h * rows))
        for i, img in enumerate(images):
            img = img.convert('RGBA')
            img = img.resize((max_w, max_h))
            x = (i % cols) * max_w
            y = (i // cols) * max_h
            ret.paste(img, (x, y), img)
        return ret

    else:
        raise Exception('concat mode must be v/h/g')

def frames_to_gif(frames: List[Image.Image], duration: int = 100, alpha_threshold: float = 0.5) -> Image.Image:
    """
    将帧列表转换为透明GIF图像
    """
    with TempFilePath('gif') as path:
        save_transparent_gif(frames, duration, path, alpha_threshold)
        return open_image(path)

def save_video_first_frame(video_path: str, save_path: str):
    """
    读取视频的第一帧并保存为图片
    """
    probe = ffmpeg.probe(video_path)
    video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
    if not video_stream:
        raise Exception(f'视频 {video_path} 没有视频流')
    width, height = video_stream['width'], video_stream['height']
    ffmpeg.input(video_path, ss=0).output(save_path, vframes=1, vf=f'scale={width}:{height}').run(overwrite_output=True, quiet=True)

def get_image_pixels(image: Image.Image | list[Image.Image]) -> int:
    """
    获取图片的像素数，动图按帧数计算
    """
    if isinstance(image, list):
        return image[0].width * image[0].height * len(image)
    if is_animated(image):
        return image.width * image.height * image.n_frames
    return image.width * image.height

def limit_image_by_pixels(image: Image.Image | list[Image.Image], max_pixels: int) -> Image.Image | list[Image.Image]:
    """
    根据最大像素数限制图片大小，输入可以是静态图、动图帧列表或动图对象
    """
    n = None
    if isinstance(image, list):
        n = len(image)
        w, h = image[0].width, image[0].height
    else:
        w, h = image.width, image.height
        if is_animated(image):
            n = image.n_frames
    pixels = get_image_pixels(image)
    if pixels <= max_pixels:
        return image
    if n is not None:
        # 仅>=10帧时才考虑抽帧 >=64*64时才考虑缩放
        old_n = n
        use_n_scale = n >= 10   
        use_wh_scale = w * h >= 64 * 64
        if use_n_scale and use_wh_scale:
            k = (pixels / max_pixels) ** (1 / 3)
            step = math.ceil(k)
            w, h = int(w / k), int(h / k)
        elif use_n_scale:
            k = (pixels / max_pixels)
            step = math.ceil(k)
        else:
            k = (pixels / max_pixels) ** 0.5
            step = 1
            w, h = int(w / k), int(h / k)
        if isinstance(image, Image.Image):
            frames = [img.resize((w, h), Image.Resampling.LANCZOS) for i, img in enumerate(ImageSequence.Iterator(image)) if i % step == 0]
            return frames_to_gif(frames, int(get_gif_duration(image) * old_n / len(frames)))
        else:
            return [img.resize((w, h), Image.Resampling.LANCZOS) for i, img in enumerate(image) if i % step == 0]
    else:
        k = (pixels / max_pixels) ** 0.5
        w, h = int(w / k), int(h / k)
        return image.resize((w, h), Image.Resampling.LANCZOS)


# ============================= 其他 ============================ #

from ..common.process_pool import ProcessPool

@on_shutdown()
def _shutdown_process_pools():
    ProcessPool.shutdown_all()


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def asend_mail(
    subject: str,
    recipient: str,
    body: str,
    smtp_server: str,
    port: int,
    username: str,
    password: str,
    from_email: str,
    logger: 'Logger',
    use_tls: bool,
):
    """
    异步发送邮件
    """
    logger.info(f'从 {username} 发送邮件到 {recipient} 主题: {subject} 内容: {body}')
    from email.message import EmailMessage
    import aiosmtplib
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    await aiosmtplib.send(
        message,
        hostname=smtp_server,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
    )
    logger.info(f'发送邮件到 {recipient} 成功')

async def asend_exception_mail(title: str, content: str, logger: 'Logger'):
    """
    通用发送异常通知函数
    """
    mail_config = global_config.get("exception_mail")
    if not content:
        content = ""
    content = content + f"\n({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
    
    for receiver in mail_config.get("receivers", []):
        try:
            await asend_mail(
                subject=f"【BOT异常通知】{title}",
                recipient=receiver,
                body=content,
                smtp_server=mail_config['host'],
                port=mail_config['port'],
                username=mail_config['user'],
                password=mail_config['pass'],
                from_email=mail_config.get('from', mail_config['user']),
                use_tls=mail_config.get('use_tls', False),
                logger=logger,
            )
        except Exception as e:
            logger.print_exc(f'发送异常邮件 {title} 到 {receiver} 失败')


@repeat_with_interval(60, '清除临时文件', utils_logger)
async def _():
    """
    定期删除过期的临时文件
    """
    global _tmp_files_to_remove
    now = datetime.now()
    new_list = []
    for path, remove_time in _tmp_files_to_remove:
        if now >= remove_time:
            try:
                if os.path.isfile(path):
                    # utils_logger.info(f'删除临时文件 {path}')
                    remove_file(path)
                elif os.path.isdir(path):
                    # utils_logger.info(f'删除临时文件夹 {path}')
                    remove_folder(path)
            except:
                utils_logger.print_exc(f'删除临时文件 {path} 失败')
        else:
            new_list.append((path, remove_time))
    _tmp_files_to_remove = new_list

    # 强制清理超过一天的文件
    files = glob.glob(pjoin(TEMP_FILE_DIR, '*'))
    for file in files:
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(file))
            if now - mtime > timedelta(days=1):
                if os.path.isfile(file):
                    # utils_logger.info(f'删除临时文件 {file}')
                    remove_file(file)
                elif os.path.isdir(file):
                    # utils_logger.info(f'删除临时文件夹 {file}')
                    remove_folder(file)
        except:
            utils_logger.print_exc(f'删除临时文件 {file} 失败')


if _profile_at_startup:
    @async_task("结束启动时性能分析", utils_logger, delay=_profile_at_startup_seconds)
    async def _stop_startup_profile():
        if not yappi.is_running():
            return
        yappi.stop()
        stats = yappi.get_func_stats()
        clock_type = yappi.get_clock_type()
        save_path = f"data/misc/profiler/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{clock_type}.prof"
        create_parent_folder(save_path)
        stats.save(save_path, type="pstat")
        print(f"启动时性能分析已保存到 {save_path}")
        yappi.clear_stats()

if _memray_at_startup:
    @async_task("结束启动时内存分析", utils_logger, delay=_memray_at_startup_seconds)
    async def _stop_startup_memray():
        global _memray_tracker
        if _memray_tracker is None:
            return
        _memray_tracker.__exit__(None, None, None)
        print(f"启动时内存分析已保存到 {_memray_save_path}")
        _memray_tracker = None
