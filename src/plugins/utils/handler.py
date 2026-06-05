from .utils import *
from ..common.blacklist import HARDCODED_BLACKLIST_USERS
from nonebot import on_command
from nonebot import get_bot as nb_get_bot
from nonebot import get_bots as nb_get_bots
from nonebot.rule import to_me as rule_to_me
from nonebot.message import handle_event
from nonebot.compat import model_dump, type_validate_python
from nonebot.adapters.onebot.v11 import (
    Bot, 
    Event,
    MessageEvent, 
    GroupMessageEvent, 
    PrivateMessageEvent, 
    ActionFailed,
)
from nonebot.adapters.onebot.v11.event import Sender, Reply
from nonebot.adapters.onebot.v11.message import MessageSegment, Message
import nonebot.adapters.onebot.v11.bot as bot_module
from argparse import ArgumentParser
import requests


SUPERUSER_CFG = global_config.item('superuser')

DEFAULT_LQ_IMAGE_QUALITY_CFG = global_config.item('msg_send.low_quality_image.default_quality')
DEFAULT_LQ_IMAGE_SUBSAMPLING_CFG = global_config.item('msg_send.low_quality_image.default_subsampling')
DEFAULT_LQ_IMAGE_OPTIMIZE_CFG = global_config.item('msg_send.low_quality_image.default_optimize')


def get_all_bots() -> list[Bot]:
    """
    获取所有已连接的Bot对象列表
    """
    return list(nb_get_bots().values())

def get_bot_by_self_id(self_id: int | str) -> Bot | None:
    """
    通过self_id获取Bot对象
    """
    return nb_get_bots().get(str(self_id), None)

async def aget_group_bot(group_id: int, raise_exc: bool = False) -> Bot | None:
    """
    通过群号获取对应的Bot对象
    """
    group_id = int(group_id)
    bots = get_all_bots()
    valid_bots: list[Bot] = []
    for bot in bots:
        try:
            group_ids = await get_group_ids(bot)
            if group_id in group_ids:
                valid_bots.append(bot)
        except Exception as e:
            utils_logger.warning(f'通过群号获取对应的Bot对象时获取Bot {bot.self_id} 的群列表失败: {get_exc_desc(e)}')
    valid_bots.sort(key=lambda x: int(x.self_id))
    if len(valid_bots) == 0:
        if raise_exc:
            raise Exception(f'未找到在群 {group_id} 中的可用Bot')
        return None
    if len(valid_bots) > 1:
        utils_logger.warning(f'发现多个Bot在群 {group_id}，默认返回self_id最小的Bot {valid_bots[0].self_id}')
    return valid_bots[0]

async def aget_private_bot(user_id: int, raise_exc: bool = False) -> Bot | None:
    """
    通过用户号获取对应的Bot对象
    """
    user_id = int(user_id)
    bots = get_all_bots()
    valid_bots: list[Bot] = []
    for bot in bots:
        try:
            friend_ids = await get_friend_ids(bot)
            if user_id in friend_ids:
                valid_bots.append(bot)
        except Exception as e:
            utils_logger.warning(f'通过用户号获取对应的Bot对象时获取Bot {bot.self_id} 的好友列表失败: {get_exc_desc(e)}')
    valid_bots.sort(key=lambda x: int(x.self_id))
    if len(valid_bots) == 0:
        if raise_exc:
            raise Exception(f'未找到可与用户 {user_id} 私聊的可用Bot')
        return None
    if len(valid_bots) > 1:
        utils_logger.warning(f'发现多个Bot可与用户 {user_id} 私聊，默认返回self_id最小的Bot {valid_bots[0].self_id}')
    return valid_bots[0]


# ============================ MonkeyPatch ============================ #

# 修改adapters.onebot.v11.bot中的函数，使得event处理消息的时候获取to_me时不会把at和reply去掉

_at_me_msg_ids = set()
_reply_me_msg_ids = set()

async def _mp_check_reply(bot: "Bot", event: MessageEvent) -> None:
    """检查消息中存在的回复，去除并赋值 `event.reply`, `event.to_me`。

    参数:
        bot: Bot 对象
        event: MessageEvent 对象
    """
    try:
        index = [x.type == "reply" for x in event.message].index(True)
    except ValueError:
        return
    msg_seg = event.message[index]
    try:
        event.reply = type_validate_python(
            Reply, await bot.get_msg(message_id=int(msg_seg.data["id"]))
        )
    except Exception as e:
        # log("WARNING", f"Error when getting message reply info: {e!r}")
        return

    if event.reply.sender.user_id is not None:
        # ensure string comparation
        if str(event.reply.sender.user_id) == str(event.self_id):
            event.to_me = True
        del event.message[index]
        _reply_me_msg_ids.add(event.message_id)

        # if (
        #     len(event.message) > index
        #     and event.message[index].type == "at"
        #     and event.message[index].data.get("qq") == str(event.reply.sender.user_id)
        # ):
        #     del event.message[index]
        #     _at_me_msg_ids.add(event.message_id)

    # if len(event.message) > index and event.message[index].type == "text":
    #     event.message[index].data["text"] = event.message[index].data["text"].lstrip()
    #     if not event.message[index].data["text"]:
    #         del event.message[index]

    if not event.message:
        event.message.append(MessageSegment.text(""))

def _mp_check_at_me(bot: "Bot", event: MessageEvent) -> None:
    """检查消息开头或结尾是否存在 @机器人，去除并赋值 `event.to_me`。

    参数:
        bot: Bot 对象
        event: MessageEvent 对象
    """
    if not isinstance(event, MessageEvent):
        return

    # ensure message not empty
    if not event.message:
        event.message.append(MessageSegment.text(""))

    if event.message_type == "private":
        event.to_me = True
    else:

        def _is_at_me_seg(segment: MessageSegment):
            return segment.type == "at" and str(segment.data.get("qq", "")) == str(
                event.self_id
            )

        # check the first segment
        if event.message and _is_at_me_seg(event.message[0]):
            event.to_me = True
            event.message.pop(0)
            _at_me_msg_ids.add(event.message_id)
            if event.message and event.message[0].type == "text":
                event.message[0].data["text"] = event.message[0].data["text"].lstrip()
                if not event.message[0].data["text"]:
                    del event.message[0]
                    pass
            if event.message and _is_at_me_seg(event.message[0]):
                event.message.pop(0)
                if event.message and event.message[0].type == "text":
                    event.message[0].data["text"] = (
                        event.message[0].data["text"].lstrip()
                    )
                    if not event.message[0].data["text"]:
                        del event.message[0]

        if not event.to_me and event.message:
            # check the last segment
            i = -1
            last_msg_seg = event.message[i]
            if (
                last_msg_seg.type == "text"
                and not last_msg_seg.data["text"].strip()
                and len(event.message) >= 2
            ):
                i -= 1
                last_msg_seg = event.message[i]

            if _is_at_me_seg(last_msg_seg):
                event.to_me = True
                del event.message[i:]
                _at_me_msg_ids.add(event.message_id)

        if not event.message:
            event.message.append(MessageSegment.text(""))

bot_module._check_at_me = _mp_check_at_me
bot_module._check_reply = _mp_check_reply

# 修改adapters.onebot.v11.bot的Bot类的方法handle_event，将message_sent转为普通message事件

async def _mp_handle_event(self, event: Event) -> None:
    """处理收到的事件。"""
    if event.post_type == "message_sent":
        data = event.model_dump()
        data['post_type'] = 'message'
        event = GroupMessageEvent(**data) if hasattr(event, 'group_id') else PrivateMessageEvent(**data)

    if isinstance(event, MessageEvent):
        event.message.reduce()
        await bot_module._check_reply(self, event)
        bot_module._check_at_me(self, event)
        bot_module._check_nickname(self, event)

    await handle_event(self, event)

bot_module.Bot.handle_event = _mp_handle_event


# ============================ API调用 ============================ #

class ExpirableCache:
    def __init__(self, default_expire_seconds: int | ConfigItem):
        self.cache: dict[Any, tuple[Any, datetime]] = {}
        self.default_expire_seconds = default_expire_seconds
    
    def get(self, key: Any) -> Any | None:
        for k in list(self.cache.keys()):
            if self.cache[k][1] <= datetime.now():
                del self.cache[k]
        if key in self.cache:
            value, expire_time = self.cache[key]
            if expire_time > datetime.now():
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: Any, value: Any, expire_seconds: int | None = None):
        expire_seconds = expire_seconds or get_cfg_or_value(self.default_expire_seconds)
        self.cache[key] = (value, datetime.now() + timedelta(seconds=expire_seconds))

_group_member_name_cache = ExpirableCache(global_config.item('group_member_name_cache_expire_seconds'))
_stranger_name_cache = ExpirableCache(global_config.item('stranger_name_cache_expire_seconds'))
_group_name_cache = ExpirableCache(global_config.item('group_name_cache_expire_seconds'))
_bot_group_cache = ExpirableCache(global_config.item('bot_group_cache_expire_seconds'))
_bot_friend_cache = ExpirableCache(global_config.item('bot_friend_cache_expire_seconds'))

def get_user_name_by_event(event_or_reply: MessageEvent | Reply) -> str:
    """
    通过event或reply获取发送者用户名，如果有群名片则返回群名片 否则返回昵称
    """
    card = event_or_reply.sender.card
    nickname = event_or_reply.sender.nickname
    if card:
        return card
    return nickname or str(event_or_reply.user_id)
    
async def get_group_member_name(group_id: int, user_id: int) -> str:
    """
    调用API获取群聊中的用户名（带缓存） 如果有群名片则返回群名片 否则返回昵称
    """
    global _group_member_name_cache
    key = (group_id, user_id)
    if cache := _group_member_name_cache.get(key):
        return cache
    bot = await aget_group_bot(group_id, raise_exc=True)
    info = await bot.call_api('get_group_member_info', **{'group_id': int(group_id), 'user_id': int(user_id)})
    name = info.get('card') or info.get('nickname', str(user_id))
    _group_member_name_cache.set(key, name)
    return name

async def get_group_ids(bot: Bot, refresh: bool = False) -> set[int]:
    """
    获取加入的所有群id
    """
    global _bot_group_cache
    key = int(bot.self_id)
    if not refresh:
        if cache := _bot_group_cache.get(key):
            return cache
    group_list = await bot.call_api('get_group_list')
    group_ids = set(int(group['group_id']) for group in group_list)
    _bot_group_cache.set(key, group_ids)
    return group_ids

async def get_group_list(bot: Bot) -> List[dict]:
    """
    获取加入的所有群
    """
    return await bot.call_api('get_group_list')

async def get_all_bot_group_list(allow_exception: bool = True) -> list[dict]:
    groups = []
    for bot in get_all_bots():
        try:
            bot_groups = await get_group_list(bot)
            groups.extend(bot_groups)
        except Exception as e:
            if not allow_exception:
                raise e
            utils_logger.warning(f'获取Bot {bot.self_id} 的群列表失败: {get_exc_desc(e)}')
    # 去重
    unique_groups = {}
    for group in groups:
        unique_groups[int(group['group_id'])] = group
    return list(unique_groups.values())

async def get_stranger_info(bot: Bot, user_id: int) -> dict:
    """
    获取陌生人信息
    """
    global _stranger_name_cache
    if cache := _stranger_name_cache.get(int(user_id)):
        return {'user_id': int(user_id), 'nickname': cache}
    info = await bot.call_api('get_stranger_info', **{'user_id': int(user_id)})
    name = info.get('nickname', str(user_id))
    _stranger_name_cache.set(int(user_id), name)
    return info

async def get_friend_ids(bot: Bot) -> set[int]:
    """
    获取好友列表id
    """
    global _bot_friend_cache
    key = int(bot.self_id)
    if cache := _bot_friend_cache.get(key):
        return cache
    friend_list = await bot.call_api('get_friend_list')
    friend_ids = set(int(friend['user_id']) for friend in friend_list)
    _bot_friend_cache.set(key, friend_ids)
    return friend_ids

async def get_group_users(bot: Bot, group_id: int) -> List[dict]:
    """
    获取群聊中所有用户
    """
    return await bot.call_api('get_group_member_list', **{'group_id': int(group_id)})

async def get_group_name(bot: Bot, group_id: int) -> str:
    """
    获取群聊名
    """
    global _group_name_cache
    if cache := _group_name_cache.get(int(group_id)):
        return cache
    group_info = await bot.call_api('get_group_info', **{'group_id': int(group_id)})
    name = group_info.get('group_name', str(group_id))
    _group_name_cache.set(int(group_id), name)
    return name

async def get_group(bot: Bot, group_id: int) -> dict:
    """
    获取群聊信息
    """
    return await bot.call_api('get_group_info', **{'group_id': int(group_id)})

async def get_avatar_url(bot: Bot | None, user_id: int) -> str:
    """
    获取QQ头像的url
    """
    if bot and user_id >= 10 ** 10:
        return await bot.call_api('get_avatar_url', **{'user_id': int(user_id)})
    return f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"

async def get_avatar_url_large(bot: Bot | None, user_id: int) -> str:
    """
    获取QQ头像的高清url
    """
    if bot and user_id >= 10 ** 10:
        return await bot.call_api('get_avatar_url', **{'user_id': int(user_id)})
    return f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

async def download_avatar(bot: Bot, user_id: int, circle=False) -> Image.Image:
    """
    下载QQ头像并返回PIL Image对象
    """
    url = await get_avatar_url(bot, user_id)
    img = await download_image(url)
    if circle:
        r = img.width // 2
        circle_img = Image.new('L', (img.width, img.height), 0)
        draw = ImageDraw.Draw(circle_img)
        draw.ellipse((0, 0, r * 2, r * 2), fill=255)
        img.putalpha(circle_img)
    return img
      
async def get_image_cq(
    image: Union[str, Image.Image, bytes],
    allow_error: bool = False, 
    logger: Logger = None, 
    low_quality: bool = False, 
    quality: int | ConfigItem = DEFAULT_LQ_IMAGE_QUALITY_CFG,
    subsampling: int | ConfigItem = DEFAULT_LQ_IMAGE_SUBSAMPLING_CFG,
    optimize: bool | ConfigItem = DEFAULT_LQ_IMAGE_OPTIMIZE_CFG,
    send_url_as_is: bool = False,
):
    """
    获取图片的cq码用于发送
    """
    args = (allow_error, logger, low_quality, quality)
    try:
        # 如果是远程图片
        if isinstance(image, str) and image.startswith("http"):
            if send_url_as_is:
                return f'[CQ:image,file={image}]'
            image = await download_image(image)
            return await get_image_cq(image, *args)
        # 如果是bytes
        if isinstance(image, bytes):
            image = Image.open(io.BytesIO(image))
            return await get_image_cq(image, *args)
        # 如果是本地路径
        if isinstance(image, str):
            if not os.path.exists(image):
                raise Exception(f'图片文件不存在: {image}')
            if send_url_as_is:
                return f'[CQ:image,file=file://{os.path.abspath(image)}]'
            image = open_image(image)
            return await get_image_cq(image, *args)

        is_gif_img = is_animated(image) or image.mode == 'P'
        ext = 'gif' if is_gif_img else ('jpg' if low_quality else 'png')
        with TempFilePath(ext, remove_after=timedelta(minutes=global_config.get('msg_send.tmp_img_keep_minutes'))) as tmp_path:
            if ext == 'gif':
                save_transparent_gif(gif_to_frames(image), get_gif_duration(image), tmp_path)
            elif ext == 'jpg':
                image = image.convert('RGB')
                image.save(
                    tmp_path, format='JPEG', 
                    quality=get_cfg_or_value(quality), 
                    optimize=get_cfg_or_value(optimize),
                    subsampling=get_cfg_or_value(subsampling), 
                    progressive=False
                )
            else:
                image.save(tmp_path)
            return f'[CQ:image,file=file://{os.path.abspath(tmp_path)}]'

    except Exception as e:
        if allow_error: 
            (logger or utils_logger).print_exc(f'图片加载失败: {e}')
            return f"[图片加载失败:{truncate(str(e), 16)}]"
        raise e

async def download_bot_file(bot: Bot, ftype: str, file: str) -> str:
    """
    下载onebot端文件，返回本地路径
    """
    if ftype == 'image':
        ret = await bot.call_api('get_image', **{'file': file})
    elif ftype == 'record':
        ret = await bot.call_api('get_record', **{'file': file, 'out_format': 'wav'})
    else:
        ret = await bot.call_api('get_file', **{'file': file})
    return ret['file']

class TempBotOrInternetFilePath:
    """
    用于临时下载网络文件或bot文件的上下文管理器
    """
    def __init__(self, ftype: str, file: str, bot: Bot = None):
        self.ftype = ftype
        self.file = file
        self.bot = bot
        self.ext = file.split('.')[-1]

    async def __aenter__(self) -> str:
        if self.file.startswith('http'):
            self.ext = {
                'html': 'html',
                'text': 'txt',
                'image': 'png',
                'record': 'wav',
                'video': 'mp4',
            }.get(self.ftype, self.ext)
            path = pjoin('data/utils/tmp', rand_filename(self.ext))
            await download_file(self.file, path)
        else:
            if not self.bot:
                raise Exception('下载napcat文件但没有提供bot对象')
            path = await download_bot_file(self.bot, self.ftype, self.file)
        self.path = path
        return path
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        remove_file(self.path)

async def upload_group_file(bot: Bot, group_id: int, file_path: str, name: str, folder: str = '/') -> dict:
    """
    上传群文件
    """
    return await bot.call_api('upload_group_file', **{
        'group_id': int(group_id),
        'file': f'file://{os.path.abspath(file_path)}',
        'name': name,
        'folder': folder,
    })


# ============================ 消息获取 ============================ #

def process_msg_segs(msg: list[dict] | Message, event: MessageEvent | None = None) -> list[dict]:
    """
    对消息段列表进行一些处理以保证各个bot后端的兼容性
    """
    ret = []
    if event:
        if event.message_id in _reply_me_msg_ids:
            ret.append({ 'type': 'reply', 'data': { 'id': str(event.reply.message_id) } })
        if event.message_id in _at_me_msg_ids:
            ret.append({ 'type': 'at', 'data': { 'qq': str(event.self_id) } })
    for seg in msg:
        stype = seg['type'] if isinstance(seg, dict) else str(seg.type)
        sdata = seg['data'] if isinstance(seg, dict) else dict(seg.data)
        if stype == 'image':
            # 为图片消息添加file_unique字段
            if not 'file_unique' in sdata:
                url: str = sdata.get('url', '')
                start_idx = url.find('fileid=') + len('fileid=')
                if start_idx == -1: continue
                end_idx = url.find('&', start_idx)
                if end_idx == -1: end_idx = len(url)
                file_unique = url[start_idx:end_idx]
                sdata['file_unique'] = file_unique 
        elif stype == 'mface':
            # mface转换为图片
            if 'url' in sdata:
                stype = 'image'
                sdata = {
                    'file': os.path.basename(sdata['url']),
                    'url': sdata['url'],
                    'file_unique': os.path.basename(sdata['url']).split('.')[0],
                    'subType': 1,
                    'summary': sdata.get('summary', '[表情]'),
                }
        # 添加file_size
        if stype in ('image', 'record', 'video'):
            if 'file_size' not in sdata:
                sdata['file_size'] = 0
        ret.append({
            'type': stype,
            'data': sdata,
        })
    return ret

def get_msg(event: MessageEvent) -> List[dict]:
    """
    从event中获取消息段内容
    """
    return process_msg_segs(event.message, event)

async def get_msg_obj_by_bot(bot: Bot, msg_id: int) -> dict:
    """
    调用API由msg_id获取完整消息对象
    """
    msg_obj = await bot.call_api('get_msg', **{'message_id': int(msg_id)})
    msg_obj['message'] = process_msg_segs(msg_obj['message'])
    msg_obj['user_id'] = msg_obj['sender']['user_id']
    return msg_obj

def get_reply_msg(event: MessageEvent) -> Optional[List[dict]]:
    """
    从event获取回复的消息段，如果没有回复则返回None
    """
    if event.reply:
        return process_msg_segs(event.reply.message)
    return None

async def get_forward_msg(bot: Bot, forward_id: str) -> dict:
    """
    获取折叠消息
    """
    result = await bot.call_api('get_forward_msg', **{'id': str(forward_id)})
    if 'messages' in result:
        # napcat
        pass
    else:
        # lagrange
        ret = { 'messages': [] }
        for node in result['message']:
            msg = node['data']
            msg['time'] = 0
            msg['message'] = msg['content']
            ret['messages'].append(msg)
        result = ret
    for msg in result['messages']:
        msg['message'] = process_msg_segs(msg['message'])
    return result

def get_msg_sender_name(event: MessageEvent) -> str:
    return event.sender.card or event.sender.nickname


def extract_cq_code(msg: list[dict]) -> Dict[str, List[dict]]:
    """
    解析消息段中的所有CQ码 返回格式为 ret["类型"]=[{CQ码1的data}{CQ码2的data}...]
    """
    ret = {}
    for seg in msg:
        if seg['type'] not in ret: ret[seg['type']] = []
        ret[seg['type']].append(seg['data'])
    return ret

def has_image(msg: list[dict]) -> bool:
    """
    检查消息段中是否包含图片
    """
    cqs = extract_cq_code(msg)
    return "image" in cqs and len(cqs["image"]) > 0

def extract_image_data(msg: list[dict]) -> List[dict]:
    cqs = extract_cq_code(msg)
    if "image" not in cqs or len(cqs["image"]) == 0: return []
    return [cq for cq in cqs["image"]]

def extract_image_url(msg: list[dict]) -> List[str]:
    """
    从消息段中提取所有图片链接
    """
    cqs = extract_cq_code(msg)
    if "image" not in cqs or len(cqs["image"]) == 0: return []
    return [cq["url"] for cq in cqs["image"] if "url" in cq]

def extract_image_id(msg: list[dict]) -> List[str]:
    """
    从消息段中提取所有图片id
    """
    cqs = extract_cq_code(msg)
    if "image" not in cqs or len(cqs["image"]) == 0: return []
    return [cq["file"] for cq in cqs["image"] if "file" in cq]

def extract_at_qq(msg: list[dict]) -> List[int]:
    """
    从消息段提取所有at的qq号
    """
    cqs = extract_cq_code(msg)
    if "at" not in cqs or len(cqs["at"]) == 0: return []
    return [int(cq["qq"]) for cq in cqs["at"] if "qq" in cq]

def extract_text(msg: list[dict]) -> str:
    """
    从消息段中提取文本
    """
    cqs = extract_cq_code(msg)
    if "text" not in cqs or len(cqs["text"]) == 0: return ""
    return ' '.join([cq['text'] for cq in cqs["text"]])

async def extract_special_text(msg: list[dict], group_id=None) -> str:
    """
    从消息段提取带有特殊消息的文本
    """
    text = ""
    for seg in msg:
        if seg['type'] == 'text':
            text += seg['data']['text']
        elif seg['type'] == 'at':
            if group_id:
                name = await get_group_member_name(group_id, seg['data']['qq'])
            else:
                name = await get_stranger_info(seg['data']['qq'])['nickname']
            if text: text += " "
            text += f"@{name} "
        elif seg['type'] == 'image':
            text += f"[图片]"
        elif seg['type'] == 'face':
            text += f"[表情]"
        elif seg['type'] == 'video':
            text += f"[视频]"
        elif seg['type'] == 'file':
            text += f"[文件]"
        elif seg['type'] =='record':
            text += f"[语音]"
        elif seg['type'] =='mface':
            text += f"[表情]"
    return text
    
async def get_image_datas_from_msg(
    bot: Bot,
    msg_or_event: list[dict] | MessageEvent,
    parse_reply: bool = True,
    parse_forward: bool = True,
    return_first: bool = False,
    min_count: int = 1,
    max_count: int = None,
    sender_id: int = None,
) -> Union[List[dict], dict]:
    """
    从event中获取所有图片数据
    """
    if isinstance(msg_or_event, MessageEvent):
        msg = get_msg(msg_or_event)
        event = msg_or_event
        sender_id = event.user_id
    else:
        msg = msg_or_event
        event = None

    if event and int(bot.self_id) == sender_id:
        cqs = extract_cq_code(msg)
        if cqs.get('json'):
            raise ReplyException(f'暂时无法读取Bot发送的折叠消息中的图片，可以先手动转发该消息')

    ret = extract_image_data(msg)
    if parse_forward:
        cqs = extract_cq_code(msg)
        if 'forward' in cqs:
            forward_msg = await get_forward_msg(bot, cqs['forward'][0]['id'])
            for msg_obj in forward_msg['messages']:
                msg = msg_obj['message']
                ret.extend(extract_image_data(msg))
    if parse_reply and event:
        if reply_msg := get_reply_msg(event):
            ret.extend(await get_image_datas_from_msg(
                bot, 
                reply_msg,
                parse_reply=False, 
                parse_forward=parse_forward,
                return_first=False,
                min_count=None,
                max_count=None,
                sender_id=event.reply.sender.user_id,
            ))

    sources = "消息本身"
    if parse_forward:   sources += "/折叠消息"
    if parse_reply:     sources += "/回复消息"

    if return_first:
        assert_and_reply(ret, f'该指令需要输入一张图片，在{sources}中没有找到图片')
        return ret[0]
    
    if min_count:
        assert_and_reply(len(ret) >= min_count, f'该指令至少输入{min_count}张图片，在{sources}中仅找到{len(ret)}张图片')
    if max_count:
        assert_and_reply(len(ret) <= max_count, f'该指令最多输入{max_count}张图片，在{sources}中找到{len(ret)}张图片')
    return ret

async def get_image_urls_from_msg(
    bot: Bot,
    msg_or_event: list[dict] | MessageEvent,
    parse_reply: bool = True,
    parse_forward: bool = True,
    return_first: bool = False,
    min_count: int = 1,
    max_count: int = None,
) -> Union[List[str], str]:
    """
    从event中获取所有图片链接
    """
    ret = await get_image_datas_from_msg(
        bot, 
        msg_or_event,
        parse_reply=parse_reply, 
        parse_forward=parse_forward,
        return_first=return_first,
        min_count=min_count,
        max_count=max_count,
    )
    if return_first:
        return ret['url']
    return [item['url'] for item in ret]  


# ============================ 聊天检查 ============================ #
        
def is_group_msg(event: MessageEvent):
    """
    检查事件是否是群聊消息
    """
    return hasattr(event, 'group_id') and event.group_id is not None

async def check_in_group(bot: Bot, group_id: int):
    """
    检查bot是否加入了某个群
    """
    return int(group_id) in await get_group_ids(bot)

async def check_is_friend(bot: Bot, user_id: int):
    """
    检查bot是否与某个用户是好友关系
    """
    return int(user_id) in await get_friend_ids(bot)

def check_in_blacklist(user_id: int):
    """
    检查用户是否在黑名单中
    """
    blacklist = utils_file_db.get('blacklist', [])
    return int(user_id) in blacklist or int(user_id) in HARDCODED_BLACKLIST_USERS

def check_group_disabled(group_id: int):
    """
    检查群聊是否被全局禁用
    """
    enabled_groups = utils_file_db.get('enabled_groups', [])
    return int(group_id) not in enabled_groups

def check_group_disabled_by_event(event: MessageEvent):
    """
    通过event检查群聊是否被全局禁用
    """
    if is_group_msg(event) and check_group_disabled(event.group_id):
        utils_logger.warning(f'取消发送消息到被全局禁用的群 {event.group_id}')
        return True
    return False

def set_group_enable(group_id: int, enable: bool):
    """
    设置群聊全局启用状态
    """
    enabled_groups = utils_file_db.get('enabled_groups', [])
    if enable:
        if int(group_id) not in enabled_groups:
            enabled_groups.append(int(group_id))
    else:
        if int(group_id) in enabled_groups:
            enabled_groups.remove(int(group_id))
    utils_file_db.set('enabled_groups', enabled_groups)
    utils_logger.info(f'设置群聊 {group_id} 全局启用状态为 {enable}')

def check_self(event: MessageEvent):
    """
    检查事件是否是自身发送的消息
    """
    return event.user_id == event.self_id

def check_superuser(event: MessageEvent, superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG):
    """ 
    检查事件是否是超级用户发送
    """
    if not superuser: 
        return False
    return event.user_id in get_cfg_or_value(superuser)

def check_self_reply(event: MessageEvent):
    """
    检查事件是否是自身对指令的回复消息
    """
    return int(event.message_id) in _bot_reply_msg_ids

def on_safe_mode() -> bool:
    """
    检查当前是否处于安全模式
    """
    return utils_file_db.get('safe_mode')

# ============================ 消息发送 ============================ #

_bot_reply_msg_ids = set()
_current_msg_count = 0
_current_msg_second = -1
_send_msg_failed_last_mail_time = datetime.fromtimestamp(0)

def check_send_msg_daily_limit(bot_id: int) -> bool:
    """
    检查是否超过全局发送消息上限
    """
    date = datetime.now().strftime("%Y-%m-%d")
    send_msg_count = utils_file_db.get('send_msg_count', {})
    bot_count = send_msg_count.get(str(bot_id), {})
    count = bot_count.get('count', 0)
    if bot_count.get('date', '') != date:
        bot_count = {'date': date, 'count': 0}
        send_msg_count[str(bot_id)] = bot_count
        utils_file_db.set('send_msg_count', send_msg_count)
        count = 0
    return count < global_config.get('msg_send.rate_limit.day')

def record_daily_msg_send(bot_id: int):
    """
    记录消息发送
    """
    date = datetime.now().strftime("%Y-%m-%d")
    send_msg_count = utils_file_db.get('send_msg_count', {})
    bot_count = send_msg_count.get(str(bot_id), {})
    count = bot_count.get('count', 0)
    if bot_count.get('date', '') != date:
        bot_count = {'date': date, 'count': 0}
        send_msg_count[str(bot_id)] = bot_count
        utils_file_db.set('send_msg_count', send_msg_count)
        count = 0
    count += 1
    bot_count['count'] = count
    send_msg_count[str(bot_id)] = bot_count
    utils_file_db.set('send_msg_count', send_msg_count)
    daily_limit = global_config.get('msg_send.rate_limit.day')
    if count == daily_limit:
        utils_logger.warning(f'Bot账号 {bot_id} 达到每日发送消息上限 {daily_limit}')

def get_send_msg_daily_count(bot_id: int) -> int:
    """
    获取bot账号当日发送消息数量
    """
    date = datetime.now().strftime("%Y-%m-%d")
    send_msg_count = utils_file_db.get('send_msg_count', {})
    bot_count = send_msg_count.get(str(bot_id), {})
    count = bot_count.get('count', 0)
    if bot_count.get('date', '') != date:
        bot_count = {'date': date, 'count': 0}
        send_msg_count[str(bot_id)] = bot_count
        utils_file_db.set('send_msg_count', send_msg_count)
        count = 0
    return count

def check_is_bot_reply_msg(msg_id: int):
    # 检查是否是bot处理指令后的回复消息
    global _bot_reply_msg_ids
    return int(msg_id) in _bot_reply_msg_ids


def send_msg_func(func):
    """
    发送消息函数的装饰器
    """
    async def wrapper(*args, **kwargs):
        # 检查消息发送次数限制
        cur_ts = int(datetime.now().timestamp())
        global _current_msg_count, _current_msg_second
        if cur_ts != _current_msg_second:
            _current_msg_count = 0
            _current_msg_second = cur_ts
        if _current_msg_count >= global_config.get('msg_send.rate_limit.second'):
            utils_logger.warning(f'消息达到发送频率，取消消息发送')
            return
        _current_msg_count += 1
        
        try:
            ret = await func(*args, **kwargs)
        except Exception as e:
            # 失败发送邮件通知
            # global _send_msg_failed_last_mail_time
            # if datetime.now() - _send_msg_failed_last_mail_time > timedelta(seconds=global_config.get('msg_send.failed_mail_interval')):
            #     _send_msg_failed_last_mail_time = datetime.now()
            #     asyncio.create_task(asend_exception_mail("消息发送失败", traceback.format_exc(), utils_logger))
            raise e

        # 记录自身对指令的回复消息id集合
        try:
            if ret:
                global _bot_reply_msg_ids
                _bot_reply_msg_ids.add(int(ret["message_id"]))
        except Exception as e:
            utils_logger.print_exc(f'记录发送消息的id失败')

        # 记录消息发送次数
        bot_id = None
        for arg in list(args) + list(kwargs.values()):
            if isinstance(arg, (MessageEvent, Bot)):
                bot_id = int(arg.self_id)
                break
        record_daily_msg_send(bot_id)
            
        return ret
        
    return wrapper
    
# -------- event内发送 -------- #

@send_msg_func
async def send_msg(handler, event: MessageEvent, message: str):
    """
    发送普通消息
    """
    if check_group_disabled_by_event(event): return None
    return await handler.send(Message(message))

@send_msg_func
async def send_reply_msg(handler, event: MessageEvent, message: str):
    """
    发送回复消息
    """
    if check_group_disabled_by_event(event): return None
    return await handler.send(Message(f'[CQ:reply,id={event.message_id}]{message}'))

@send_msg_func
async def send_at_msg(handler, event: MessageEvent, message: str):
    """
    发送at消息
    """
    if check_group_disabled_by_event(event): return None
    return await handler.send(Message(f'[CQ:at,qq={event.user_id}]{message}'))

# -------- event外发送 -------- #

@send_msg_func
async def send_group_msg_by_bot(group_id: int, content: str, bot: Bot = None):
    """
    在event外发送群聊消息
    """
    if bot is None:
        bot = await aget_group_bot(group_id)
        if bot is None:
            utils_logger.warning(f'取消发送消息到群 {group_id}，没有可用的bot')
            return
    if check_group_disabled(group_id):
        utils_logger.warning(f'取消发送消息到被全局禁用的群 {group_id}')
        return
    if not await check_in_group(bot, group_id):
        utils_logger.warning(f'取消发送消息到未加入的群 {group_id}')
        return
    return await bot.send_group_msg(group_id=int(group_id), message=content)

@send_msg_func
async def send_private_msg_by_bot(user_id: int, content: str, bot: Bot = None):
    """
    在event外发送私聊消息
    """
    if bot is None:
        bot = await aget_private_bot(user_id)
        if bot is None:
            utils_logger.warning(f'取消发送私聊消息给用户 {user_id}，没有可用的bot')
            return
    if check_in_blacklist(user_id):
        utils_logger.warning(f'取消发送私聊消息给黑名单用户 {user_id}')
        return
    if not await check_is_friend(bot, user_id):
        utils_logger.warning(f'取消发送私聊消息给非好友用户 {user_id}')
        return
    return await bot.send_private_msg(user_id=int(user_id), message=content)

# -------- 折叠消息处理 -------- #

MAX_FOLD_MSG_SEGMENT_COUNT = global_config.item('msg_send.max_fold_msg_segment_count')
DEFAULT_FOLD_THRESHOLD_CFG = global_config.item('msg_send.default_fold_threshold')
MAX_FOLD_MSG_LEN_CFG = global_config.item('msg_send.max_fold_msg_len')
DEFAULT_FOLD_FALLBACK_METHOD_CFG = global_config.item('msg_send.default_fold_fallback_method')

@dataclass
class FoldMsgPart:
    type: str
    content: str

    def get_linecount(self) -> int:
        if self.type == 'text':
            ret = 0
            for part in self.content.split('\n'):
                ret += get_str_display_length(part) // 40 + 1  # 每40个字符算一行
            return ret
        else:
            return 4    # 其他类型消息长度估计为4行
        
    def get_text_length(self) -> int:
        if self.type == 'text':
            return len(self.content)
        else:
            return 0
        
def contents_to_parts(contents: Union[str, List[str]]) -> List[List[FoldMsgPart]]:
    """
    获取折叠消息文本和CQ码片段
    """
    if isinstance(contents, str):
        contents = [contents]
    ret = []
    for content in contents:
        parts = []
        cur = 0
        while cur < len(content):
            cq_start = content.find('[CQ:', cur)
            if cq_start == -1:
                if cur < len(content):
                    parts.append(FoldMsgPart('text', content[cur:]))
                break
            if cur < cq_start:
                parts.append(FoldMsgPart('text', content[cur:cq_start]))
            cq_end = content.find(']', cq_start)
            cq_type_end = content.find(',', cq_start)
            if cq_end == -1 or cq_type_end == -1:
                raise ValueError(f'折叠消息内容格式错误')
            parts.append(FoldMsgPart(
                content[cq_start + 4:cq_type_end],
                content[cq_start:cq_end + 1]
            ))
            cur = cq_end + 1
        ret.append(parts)
    return ret

def parts_to_contents(parts: List[List[FoldMsgPart]]) -> List[str]:
    """
    将折叠消息片段转换回折叠消息
    """
    ret = []
    for part_list in parts:
        ret.append(''.join(part.content for part in part_list))
    return ret

def apply_limit_to_parts(fold_parts: List[List[FoldMsgPart]], limit: int, seq: int = 1) -> List[List[List[FoldMsgPart]]]:
    """
    将折叠消息片段应用长度限制，返回分割后的片段列表
    """
    if seq > MAX_FOLD_MSG_SEGMENT_COUNT.get():
        raise ValueError(f'折叠消息分段超过最大限制 {MAX_FOLD_MSG_SEGMENT_COUNT.get()}')
    assert limit > 64
    if not fold_parts:
        return []
    if seq > 1:
        fold_parts = [[FoldMsgPart('limit_text', f'[分段折叠消息Part.{seq}]')]] + fold_parts
    cur_len = 0
    cur_fold: List[List[FoldMsgPart]] = []
    for i, msg in enumerate(fold_parts):
        msg_len = sum(part.get_text_length() for part in msg)
        cur_msg: List[FoldMsgPart] = []

        if msg_len > limit:
            # 单条消息超过长度限制，需要在消息内分割
            rest_limit, parts_len = limit - cur_len, 0
            for part in msg:
                part_len = part.get_text_length()
                if part.type == 'text' and parts_len + part_len > rest_limit:
                    # 找到分割点，分割当前part
                    part1 = FoldMsgPart(part.type, part.content[:rest_limit - parts_len])
                    part2 = FoldMsgPart(part.type, part.content[rest_limit - parts_len:])
                    # part1加入cur_msg，当前的cur_msg加入cur_fold，然后作为一个新的折叠消息
                    cur_msg.append(part1)
                    cur_msg = [p for p in cur_msg if p.content]
                    cur_fold.append(cur_msg)
                    cur_fold.append([FoldMsgPart('limit_text', f'[消息过长已自动分段]')])
                    cur_fold = [m for m in cur_fold if m]
                    # part2加入剩余部分
                    rest_fold_parts = [[part2]] + fold_parts[i + 1:]
                    # 递归处理剩余部分
                    return [cur_fold] + apply_limit_to_parts(rest_fold_parts, limit, seq + 1)
                else:
                    cur_msg.append(part)
                    parts_len += part_len
                
        elif cur_len + msg_len > limit:
            # 当前总长度超过限制，将之前的作为一个折叠消息
            cur_fold.append([FoldMsgPart('limit_text', f'[消息过长已自动分段]')])
            return [cur_fold] + apply_limit_to_parts(fold_parts[i:], limit, seq + 1)
        
        else:
            # 没有超过长度限制，直接添加到当前折叠消息
            cur_msg = msg

        # 当前消息添加到当前折叠消息，更新总长度
        cur_msg = [p for p in cur_msg if p.content]
        cur_fold.append(cur_msg)
        cur_len += sum(part.get_text_length() for part in cur_msg)
    cur_fold = [m for m in cur_fold if m]
    return [cur_fold] if cur_fold else []

@send_msg_func
async def send_fold_msg(
    bot: Bot,
    group_id: Optional[int], 
    user_id: Optional[int], 
    contents: Union[str, List[str]], 
    fallback_method=None, 
    first_is_user=False,
):
    """
    发送私聊或群聊折叠消息
    fallback_method in ['seperate', 'join_newline', 'join', 'none']
    """
    parts = contents_to_parts(contents)
    if not parts:
        raise ValueError('发送的折叠消息为空')
    all_parts = apply_limit_to_parts(parts, MAX_FOLD_MSG_LEN_CFG.get())
    ret = None
    for parts in all_parts:
        contents = parts_to_contents(parts)
        if group_id and check_group_disabled(group_id):
            utils_logger.warning(f'取消发送消息到被全局禁用的群 {group_id}')
            return
        selfname = await get_group_member_name(group_id, bot.self_id)
        msg_list = []
        for i in range(len(contents)):
            if i == 0 and first_is_user:
                uid = user_id
                nickname = await get_group_member_name(group_id, user_id)
            else:
                uid = int(bot.self_id)
                nickname = selfname
            msg_list.append({
                "type": "node",
                "data": {
                    "user_id": uid,
                    "nickname": nickname,
                    "content": contents[i],
                }
            })
        try:
            if group_id:
                ret = await bot.send_group_forward_msg(group_id=group_id, messages=msg_list)
            else:
                ret = await bot.send_private_forward_msg(user_id=user_id, messages=msg_list)
        except Exception as e:
            ret = await fold_msg_fallback(bot, group_id, user_id, contents, e, fallback_method)
    return ret

async def fold_msg_fallback(
    bot: Bot,
    group_id: Optional[int], 
    user_id: Optional[int], 
    contents: List[str], 
    e: Exception, 
    method: Optional[str],
):
    """
    发送折叠消息失败的fallback
    method: 为None使用默认fallback方法
    """
    if method is None:
        method = DEFAULT_FOLD_FALLBACK_METHOD_CFG.get()
    def send(msg: str):
        if user_id:
            return send_private_msg_by_bot(user_id, msg)
        return send_group_msg_by_bot(group_id, msg)
    utils_logger.warning(f'发送折叠消息失败，fallback为发送普通消息(method={method}): {get_exc_desc(e)}')
    if method == 'seperate':
        contents[0] = "（发送折叠消息失败）\n" + contents[0]
        for content in contents:
            ret = await send(content)
    elif method == 'join_newline':
        contents = ["（发送折叠消息失败）"] + contents
        msg = "\n".join(contents)
        ret = await send(msg)
    elif method == 'join':
        contents = ["（发送折叠消息失败）\n"] + contents
        msg = "".join(contents)
        ret = await send(msg)
    elif method == 'none':
        ret = await send("发送折叠消息失败")
    else:
        raise Exception(f'未知折叠消息fallback方法 {method}')
    return ret

async def send_fold_msg_adaptive(
    bot: Bot, 
    group_id: Optional[int], 
    user_id: Optional[int], 
    contents: Union[str, List[str]], 
    not_fold_contents: Optional[List[str]]=None,
    threshold: Union[int, ConfigItem]=DEFAULT_FOLD_THRESHOLD_CFG,
    need_reply: bool=True, 
    reply_message_id: int=None,
    fallback_method: str=None,
    first_is_user=False,
):
    """
    根据消息长度以及是否是群聊消息动态判断是否需要发送折叠消息
    not_fold_contents: 指定不折叠发送的内容，None则发送相同内容
    threshold: 折叠消息的行数阈值
    need_reply: 不折叠的情况是否需要回复消息
    """
    if isinstance(contents, str):
        contents = [contents]
    all_parts = contents_to_parts(contents)
    linecount = len(all_parts) - 1  # 每条消息之间间隔看作一行
    for parts in all_parts:
        for part in parts:
            linecount += part.get_linecount()
    utils_logger.debug(f'折叠消息行数: {linecount}')
    if linecount < get_cfg_or_value(threshold):
        # 不折叠消息
        if not_fold_contents:
            if isinstance(not_fold_contents, str):
                not_fold_contents = [not_fold_contents]
            contents = not_fold_contents
        reply_cq = ""
        if need_reply: 
            assert reply_message_id is not None, "需要回复消息时reply_message_id不能为空"
            reply_cq = f'[CQ:reply,id={reply_message_id}]'
        ret = None
        if group_id:
            for content in contents:
                ret = await send_group_msg_by_bot(group_id, f'{reply_cq}{content}')
        else:
            for content in contents:
                ret = await send_private_msg_by_bot(user_id, f'{reply_cq}{content}')
        return ret
    else:
        # 折叠消息
        return await send_fold_msg(bot, group_id, user_id, contents, fallback_method=fallback_method, first_is_user=first_is_user)
  
async def send_private_fold_msg_adaptive_by_bot(
    bot: Bot,
    user_id: int,
    contents: Union[str, List[str]], 
    threshold: Union[int, ConfigItem]=DEFAULT_FOLD_THRESHOLD_CFG,
    fallback_method: str=None,
):
    """
    根据消息长度以及是否是群聊消息动态判断是否需要发送私聊折叠消息（不通过事件）
    threshold: 折叠消息的行数阈值
    """
    return await send_fold_msg_adaptive(
        bot, 
        None, 
        user_id, 
        contents, 
        threshold=threshold,
        need_reply=False, 
        reply_message_id=None,
        fallback_method=fallback_method
    )

async def send_group_fold_msg_adaptive_by_bot(
    bot: Bot,
    group_id: int,
    contents: Union[str, List[str]], 
    threshold: Union[int, ConfigItem]=DEFAULT_FOLD_THRESHOLD_CFG,
    fallback_method: str=None,
):
    """
    根据消息长度以及是否是群聊消息动态判断是否需要发送群聊折叠消息（不通过事件）
    threshold: 折叠消息的行数阈值
    """
    return await send_fold_msg_adaptive(
        bot, 
        group_id, 
        None, 
        contents, 
        threshold=threshold,
        need_reply=False, 
        reply_message_id=None,
        fallback_method=fallback_method
    )


# ============================ 聊天控制 ============================ #

DEFAULT_CD_CFG = global_config.item('default_cd')

class ColdDown:
    """
    冷却时间
    """
    def __init__(
        self, 
        db: FileDB, 
        logger: Logger, 
        default_interval: Union[int, ConfigItem]=DEFAULT_CD_CFG, 
        superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG, 
        cold_down_name: str=None, 
        group_seperate: bool=False
    ):
        self.default_interval = default_interval
        self.superuser = superuser
        self.db = db
        self.logger = logger
        self.group_seperate = group_seperate
        self.cold_down_name = f'cold_down' if cold_down_name is None else f'cold_down_{cold_down_name}'
    
    async def check(self, event, interval: int=None, allow_super=True, verbose=True):
        if allow_super and check_superuser(event, self.superuser):
            # self.logger.debug(f'{self.cold_down_name}检查: 超级用户{event.user_id}')
            return True
        if interval is None: interval = get_cfg_or_value(self.default_interval)
        key = str(event.user_id)
        if isinstance(event, GroupMessageEvent) and self.group_seperate:
            key = f'{event.group_id}-{key}'
        last_use = self.db.get(self.cold_down_name, {})
        now = datetime.now().timestamp()
        if key not in last_use:
            last_use[key] = now
            self.db.set(self.cold_down_name, last_use)
            # self.logger.debug(f'{self.cold_down_name}检查: {key} 未使用过')
            return True
        if now - last_use[key] < interval:
            # self.logger.debug(f'{self.cold_down_name}检查: {key} CD中')
            if verbose:
                try:
                    verbose_key = f'verbose_{key}'
                    if verbose_key not in last_use:
                        last_use[verbose_key] = 0
                    if now - last_use[verbose_key] > global_config.get('cd_verbose_interval'):
                        last_use[verbose_key] = now
                        self.db.set(self.cold_down_name, last_use)
                        rest_time = timedelta(seconds=interval - (now - last_use[key]))
                        verbose_msg = f'冷却中, 剩余时间: {get_readable_timedelta(rest_time)}'
                        if hasattr(event, 'message_id'):
                            if hasattr(event, 'group_id'):
                                await send_group_msg_by_bot(event.group_id, f'[CQ:reply,id={event.message_id}] {verbose_msg}')
                            else:
                                await send_private_msg_by_bot(event.user_id, f'[CQ:reply,id={event.message_id}] {verbose_msg}')
                except Exception as e:
                    self.logger.print_exc(f'{self.cold_down_name}检查: {key} CD中, 发送冷却中消息失败')
            return False
        last_use[key] = now
        self.db.set(self.cold_down_name, last_use)
        # self.logger.debug(f'{self.cold_down_name}检查: {key} 通过')
        return True

    def get_last_use(self, user_id: int, group_id: int=None):
        key = f'{group_id}-{user_id}' if group_id else str(user_id)
        last_use = self.db.get(self.cold_down_name, {})
        if key not in last_use:
            return None
        return datetime.fromtimestamp(last_use[key])

class RateLimit:
    """
    频率限制
    """
    def __init__(
        self, 
        db: FileDB, 
        logger: Logger, 
        limit: Union[int, ConfigItem], 
        period_type: Union[str, ConfigItem], 
        superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG, 
        rate_limit_name: str=None,
        group_seperate: bool=False
    ):
        """
        period_type: "minute", "hour", "day" or "m", "h", "d"
        """
        self.limit = limit
        self.period_type = period_type
        self.superuser = superuser
        self.db = db
        self.logger = logger
        self.group_seperate = group_seperate
        self.rate_limit_name = f'default' if rate_limit_name is None else f'{rate_limit_name}'

    def get_period_time(self, t: datetime) -> datetime:
        period_type = get_cfg_or_value(self.period_type)[0].lower()
        if period_type == "m":
            return t.replace(second=0, microsecond=0)
        if period_type == "h":
            return t.replace(minute=0, second=0, microsecond=0)
        if period_type == "d":
            return t.replace(hour=0, minute=0, second=0, microsecond=0)
        raise Exception(f'未知的时间段类型 {self.period_type}')

    async def check(self, event, allow_super=True, verbose=True):
        if allow_super and check_superuser(event, self.superuser):
            # self.logger.debug(f'{self.rate_limit_name}检查: 超级用户{event.user_id}')
            return True
        key = str(event.user_id)
        if isinstance(event, GroupMessageEvent) and self.group_seperate:
            key = f'{event.group_id}-{key}'
        last_check_time_key = f'last_check_time_{self.rate_limit_name}'
        count_key = f"rate_limit_count_{self.rate_limit_name}"
        last_check_time = datetime.fromtimestamp(self.db.get(last_check_time_key, 0))
        count = self.db.get(count_key, {})
        if self.get_period_time(datetime.now()) > self.get_period_time(last_check_time):
            count = {}
            # self.logger.debug(f'{self.rate_limit_name}检查: 额度已重置')
        limit = get_cfg_or_value(self.limit)
        if count.get(key, 0) >= limit:
            # self.logger.debug(f'{self.rate_limit_name}检查: {key} 频率超限')
            if verbose:
                reply_msg = "达到{period}使用次数限制({limit})"
                if self.period_type == "m":
                    reply_msg = reply_msg.format(period="分钟", limit=limit)
                elif self.period_type == "h":
                    reply_msg = reply_msg.format(period="小时", limit=limit)
                elif self.period_type == "d":
                    reply_msg = reply_msg.format(period="天", limit=limit)
                try:
                    if hasattr(event, 'message_id'):
                        if hasattr(event, 'group_id'):
                            await send_group_msg_by_bot(event.group_id, f'[CQ:reply,id={event.message_id}] {reply_msg}')
                        else:
                            await send_private_msg_by_bot(event.user_id, f'[CQ:reply,id={event.message_id}] {reply_msg}')
                except Exception as e:
                    self.logger.print_exc(f'{self.rate_limit_name}检查: {key} 频率超限, 发送频率超限消息失败')
            ok = False
        else:
            count[key] = count.get(key, 0) + 1
            # self.logger.debug(f'{self.rate_limit_name}检查: {key} 通过 当前次数 {count[key]}/{limit}')
            ok = True
        self.db.set(count_key, count)
        self.db.set(last_check_time_key, datetime.now().timestamp())
        return ok
        
class GroupWhiteList:
    """
    群白名单：默认关闭
    """
    def __init__(
        self, 
        db: FileDB, 
        logger: Logger, 
        name: str, 
        superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG,
        on_func=None, 
        off_func=None
    ):
        self.superuser = superuser
        self.name = name
        self.logger = logger
        self.db = db
        self.white_list_name = f'group_white_list_{name}'
        self.on_func = on_func
        self.off_func = off_func

        async def get_group_id_desc(ctx: HandlerContext) -> tuple[int, str]:
            if args := ctx.get_args().strip():
                group = find_by(await get_group_list(ctx.bot), 'group_id', int(args))
                assert_and_reply(group, f'无效群聊 {args}')
                group_id = group['group_id']
                group_desc = f"\"{group['group_name']}\"({group_id})"
            else:
                group_id = ctx.group_id
                group_desc = f'本群'
            return int(group_id), group_desc

        # 开启命令
        switch_on = CmdHandler([f'/{name} on'], utils_logger, help_command='/{服务名} on')
        switch_on.check_superuser(superuser)
        @switch_on.handle()
        async def _(ctx: HandlerContext):
            group_id, group_desc = await get_group_id_desc(ctx)
            white_list = db.get(self.white_list_name, [])
            if group_id in white_list:
                return await ctx.asend_reply_msg(f'{group_desc}的{name}已经是开启状态')
            white_list.append(group_id)
            db.set(self.white_list_name, white_list)
            if self.on_func is not None: 
                await self.on_func(ctx.group_id)
            return await ctx.asend_reply_msg(f'成功开启{group_desc}的{name}')
        
        # 关闭命令
        switch_off = CmdHandler([f'/{name} off'], utils_logger, help_command='/{服务名} off')
        switch_off.check_superuser(superuser)
        @switch_off.handle()
        async def _(ctx: HandlerContext):
            group_id, group_desc = await get_group_id_desc(ctx)
            white_list = db.get(self.white_list_name, [])
            if group_id not in white_list:
                return await ctx.asend_reply_msg(f'{group_desc}的{name}已经是关闭状态')
            white_list.remove(group_id)
            db.set(self.white_list_name, white_list)
            if self.off_func is not None:  
                await self.off_func(ctx.group_id)
            return await ctx.asend_reply_msg(f'成功关闭{group_desc}的{name}')
            
        # 查询命令
        switch_query = CmdHandler([f'/{name} status'], utils_logger, help_command='/{服务名} status')
        @switch_query.handle()
        async def _(ctx: HandlerContext):
            group_id, group_desc = await get_group_id_desc(ctx)
            white_list = db.get(self.white_list_name, [])
            if group_id in white_list:
                return await ctx.asend_reply_msg(f'{group_desc}的{name}开启中')
            else:
                return await ctx.asend_reply_msg(f'{group_desc}的{name}关闭中')

        @on_collect_quited_group
        def _collect_quited_group(current_groups: CurrentGroupInfoDict):
            return QuitedGroupUserInfo(
                quited_group_ids=[gid for gid in self.get() if gid not in current_groups],
            )

        @on_clean_quited_group
        def _clean_quited_group(current_groups: CurrentGroupInfoDict):
            for gid in self.get().copy():
                if gid not in current_groups:
                    self.remove(gid)

            
    def get(self) -> List[int]:
        """
        获取白名单群id列表
        """
        return self.db.get(self.white_list_name, [])
    
    def add(self, group_id: int) -> bool:
        """
        添加群到白名单，返回是否成功添加
        """
        white_list = self.db.get(self.white_list_name, [])
        if group_id in white_list:
            return False
        white_list.append(group_id)
        self.db.set(self.white_list_name, white_list)
        self.logger.info(f'添加群 {group_id} 到 {self.white_list_name}')
        if self.on_func is not None: self.on_func(group_id)
        return True
    
    def remove(self, group_id: int) -> bool:
        """
        从白名单移除群，返回是否成功移除
        """
        white_list = self.db.get(self.white_list_name, [])
        if group_id not in white_list:
            return False
        white_list.remove(group_id)
        self.db.set(self.white_list_name, white_list)
        self.logger.info(f'从 {self.white_list_name} 删除群 {group_id}')
        if self.off_func is not None: self.off_func(group_id)
        return True
            
    def check_id(self, group_id: int) -> bool:
        """
        检查群id是否在白名单中
        """
        white_list = self.db.get(self.white_list_name, [])
        # self.logger.debug(f'白名单{self.white_list_name}检查{group_id}: {"允许通过" if group_id in white_list else "不允许通过"}')
        return group_id in white_list

    def check(self, event: MessageEvent, allow_private=False, allow_super=True) -> bool:
        """
        检查消息事件是否通过白名单
        """
        if is_group_msg(event):
            if allow_super and check_superuser(event, self.superuser): 
                # self.logger.debug(f'白名单{self.white_list_name}检查: 允许超级用户{event.user_id}')
                return True
            return self.check_id(event.group_id)
        # self.logger.debug(f'白名单{self.white_list_name}检查: {"允许私聊" if allow_private else "不允许私聊"}')
        return allow_private
    
class GroupBlackList:
    """
    群黑名单：默认开启
    """
    def __init__(
        self, 
        db: FileDB, 
        logger: Logger, 
        name: str, 
        superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG,
        on_func=None, 
        off_func=None
    ):
        self.superuser = superuser
        self.name = name
        self.logger = logger
        self.db = db
        self.black_list_name = f'group_black_list_{name}'
        self.on_func = on_func
        self.off_func = off_func

        async def get_group_id_desc(ctx: HandlerContext) -> tuple[int, str]:
            if args := ctx.get_args().strip():
                group = find_by(await get_group_list(ctx.bot), 'group_id', int(args))
                assert_and_reply(group, f'无效群聊 {args}')
                group_id = group['group_id']
                group_desc = f"\"{group['group_name']}\"({group_id})"
            else:
                group_id = ctx.group_id
                group_desc = f'本群'
            return int(group_id), group_desc

        # 关闭命令
        switch_off = CmdHandler([f'/{name} off'], utils_logger, help_command='/{服务名} off')
        switch_off.check_superuser(superuser)
        @switch_off.handle()
        async def _(ctx: HandlerContext):
            group_id, group_desc = await get_group_id_desc(ctx)
            black_list = db.get(self.black_list_name, [])
            if group_id in black_list:
                return await ctx.asend_reply_msg(f'成功关闭{group_desc}的{name}')
            black_list.append(group_id)
            db.set(self.black_list_name, black_list)
            if self.off_func is not None: 
                await self.off_func(ctx.group_id)
            return await ctx.asend_reply_msg(f'{group_desc}的{name}已关闭')
        
        # 开启命令
        switch_on = CmdHandler([f'/{name} on'], utils_logger, help_command='/{服务名} on')
        switch_on.check_superuser(superuser)
        @switch_on.handle()
        async def _(ctx: HandlerContext):
            group_id, group_desc = await get_group_id_desc(ctx)
            black_list = db.get(self.black_list_name, [])
            if group_id not in black_list:
                return await ctx.asend_reply_msg(f'成功开启{group_desc}的{name}')
            black_list.remove(group_id)
            db.set(self.black_list_name, black_list)
            if self.on_func is not None: 
                await self.on_func(ctx.group_id)
            return await ctx.asend_reply_msg(f'{group_desc}的{name}已开启')
            
        # 查询命令
        switch_query = CmdHandler([f'/{name} status'], utils_logger, help_command='/{服务名} status')
        @switch_query.handle()
        async def _(ctx: HandlerContext):
            group_id, group_desc = await get_group_id_desc(ctx)
            black_list = db.get(self.black_list_name, [])
            if group_id in black_list:
                return await ctx.asend_reply_msg(f'{group_desc}的{name}关闭中')
            else:
                return await ctx.asend_reply_msg(f'{group_desc}的{name}开启中')

        @on_collect_quited_group
        def _collect_quited_group(current_groups: CurrentGroupInfoDict):
            return QuitedGroupUserInfo(
                quited_group_ids=[gid for gid in self.get() if gid not in current_groups],
            )

        @on_clean_quited_group
        def _clean_quited_group(current_groups: CurrentGroupInfoDict):
            for gid in self.get().copy():
                if gid not in current_groups:
                    self.remove(gid)
        
    def get(self) -> List[int]:
        """
        获取黑名单群id列表
        """
        return self.db.get(self.black_list_name, [])
    
    def add(self, group_id: int) -> bool:
        """
        添加群到黑名单，返回是否成功添加
        """
        black_list = self.db.get(self.black_list_name, [])
        if group_id in black_list:
            return False
        black_list.append(group_id)
        self.db.set(self.black_list_name, black_list)
        self.logger.info(f'添加群 {group_id} 到 {self.black_list_name}')
        if self.off_func is not None: self.off_func(group_id)
        return True
    
    def remove(self, group_id: int) -> bool:
        """
        从黑名单移除群，返回是否成功移除
        """
        black_list = self.db.get(self.black_list_name, [])
        if group_id not in black_list:
            return False
        black_list.remove(group_id)
        self.db.set(self.black_list_name, black_list)
        self.logger.info(f'从 {self.black_list_name} 删除群 {group_id}')
        if self.on_func is not None: self.on_func(group_id)
        return True
    
    def check_id(self, group_id) -> bool:
        """
        检查群id是否不在黑名单中
        """
        black_list = self.db.get(self.black_list_name, [])
        # self.logger.debug(f'黑名单{self.black_list_name}检查{group_id}: {"允许通过" if group_id not in black_list else "不允许通过"}')
        return group_id not in black_list
    
    def check(self, event, allow_private=False, allow_super=True) -> bool:
        """
        检查消息事件是否通过黑名单
        """
        if is_group_msg(event):
            if allow_super and check_superuser(event, self.superuser): 
                self.logger.debug(f'黑名单{self.black_list_name}检查: 允许超级用户{event.user_id}')
                return True
            # self.logger.debug(f'黑名单{self.black_list_name}检查: {"允许通过" if self.check_id(event.group_id) else "不允许通过"}')
            return self.check_id(event.group_id)
        # self.logger.debug(f'黑名单{self.black_list_name}检查: {"允许私聊" if allow_private else "不允许私聊"}')
        return allow_private
    

_gwls: Dict[str, GroupWhiteList] = {}
def get_group_white_list(
    db: FileDB, 
    logger: Logger, 
    name: str, 
    superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG,
    on_func=None, 
    off_func=None, 
    is_service=True
) -> GroupWhiteList:
    if is_service:
        global _gwls
        if name not in _gwls:
            _gwls[name] = GroupWhiteList(db, logger, name, superuser, on_func, off_func)
        return _gwls[name]
    return GroupWhiteList(db, logger, name, superuser, on_func, off_func)

_gbls: Dict[str, GroupBlackList] = {}
def get_group_black_list(
    db: FileDB, 
    logger: Logger, 
    name: str, 
    superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG,
    on_func=None, 
    off_func=None, 
    is_service=True
) -> GroupBlackList:
    if is_service:
        global _gbls
        if name not in _gbls:
            _gbls[name] = GroupBlackList(db, logger, name, superuser, on_func, off_func)
        return _gbls[name]
    return GroupBlackList(db, logger, name, superuser, on_func, off_func)



# ============================ 聊天处理 ============================ #

class NoReplyException(Exception):
    """
    不会触发消息回复的Exception，用于退出当前消息处理
    """
    pass

class ReplyException(Exception):
    """
    触发特定消息回复并且不会折叠的Exception，用于退出当前消息处理
    """
    pass

def assert_and_reply(condition, msg: str):
    """
    检查条件，如果不满足则抛出ReplyException
    """
    if not condition:
        raise ReplyException(msg)


class MessageArgumentParser(ArgumentParser):
    """
    适用于HandlerContext的参数解析器
    """
    def __init__(self, ctx: 'HandlerContext', *args, **kwargs):
        super().__init__(*args, **kwargs, exit_on_error=False)
        self.ctx = ctx

    def error(self, message):
        raise Exception(message)

    async def parse_args(self, error_reply=None, *args, **kwargs):
        try:
            s = self.ctx.get_args().strip().split()
            return super().parse_args(s, *args, **kwargs)
        except Exception as e:
            self.ctx.logger.print_exc("参数解析失败")
            if error_reply is None:
                raise e
            else:
                await self.ctx.asend_msg(error_reply)
                raise NoReplyException()

@dataclass
class HandlerContext:
    time: datetime = None
    handler: "CmdHandler" = None
    nonebot_handler: Any = None
    bot: Bot = None
    event: MessageEvent = None
    trigger_cmd: str = None
    arg_text: str = None
    message_id: int = None
    user_id: int = None
    group_id: int = None
    logger: Logger = None
    block_ids: List[str] = field(default_factory=list)

    # --------------------------  数据获取 -------------------------- #

    def get_args(self) -> str:
        return self.arg_text

    def get_argparser(self) -> MessageArgumentParser:
        return MessageArgumentParser(self)

    def get_msg(self) -> list[dict]:
        return get_msg(self.event)
    
    def get_sender_name(self) -> str:
        return get_user_name_by_event(self.event)

    def get_reply_msg(self) -> list[dict]:
        return get_reply_msg(self.event)

    def get_reply_msg_id(self) -> int | None:
        if self.event.reply:
            return int(self.event.reply.message_id)
        return None
    
    def get_reply_sender(self) -> Sender | None:
        if self.event.reply:
            return self.event.reply.sender
        return None

    def get_at_qids(self) -> list[int]:
        return extract_at_qq(self.get_msg())
    
    def aget_image_datas(
        self,
        parse_reply: bool = True,
        parse_forward: bool = True,
        return_first: bool = False,
        min_count: int = 1,
        max_count: int = None,
    ):
        return get_image_datas_from_msg(
            self.bot, 
            self.event,
            parse_reply=parse_reply, 
            parse_forward=parse_forward, 
            return_first=return_first, 
            min_count=min_count, 
            max_count=max_count
        )

    def aget_image_urls(
        self,
        parse_reply: bool = True,
        parse_forward: bool = True,
        return_first: bool = False,
        min_count: int = 1,
        max_count: int = None,
    ):
        return get_image_urls_from_msg(
            self.bot, 
            self.event,
            parse_reply=parse_reply, 
            parse_forward=parse_forward, 
            return_first=return_first, 
            min_count=min_count, 
            max_count=max_count
        )

    
    # -------------------------- 消息发送 -------------------------- # 

    def asend_msg(self, msg: str):
        return send_msg(self.nonebot_handler, self.event, msg)

    def asend_reply_msg(self, msg: str):
        return send_reply_msg(self.nonebot_handler, self.event, msg)

    def asend_at_msg(self, msg: str):
        return send_at_msg(self.nonebot_handler, self.event, msg)

    def asend_fold_msg(
        self, 
        contents: Union[str, List[str]], 
        show_command: bool=True,
        fallback_method: Optional[str]=None,
    ):
        first_is_user = False
        if isinstance(contents, str):
            contents = [contents]
        if show_command:
            contents = [self.event.get_plaintext()] + contents
            first_is_user = True
        return send_fold_msg(
            bot=self.bot,
            group_id=self.group_id,
            user_id=self.user_id,
            contents=contents,
            fallback_method=fallback_method,
            first_is_user=first_is_user,
        )

    def asend_fold_msg_adaptive(
        self, 
        contents: Union[str, List[str]], 
        threshold: Union[int, ConfigItem]=DEFAULT_FOLD_THRESHOLD_CFG,
        need_reply: bool=True,
        fallback_method: Optional[str]=None,
    ):
        first_is_user = False
        if isinstance(contents, str):
            contents = [contents]
        fold_contents = contents
        if need_reply:
            fold_contents = [self.event.get_plaintext()] + contents
            first_is_user = True
        return send_fold_msg_adaptive(
            bot=self.bot,
            group_id=self.group_id,
            user_id=self.user_id,
            contents=fold_contents,
            not_fold_contents=contents,
            threshold=threshold,
            need_reply=need_reply,
            reply_message_id=self.message_id,
            fallback_method=fallback_method,
            first_is_user=first_is_user,
        )

    async def asend_video(self, path: str):
        video = await run_in_pool(read_file_as_base64, path)
        try:
            await self.asend_msg(f"[CQ:video,file=base64://{video}]")
        except ActionFailed as e:
            try:
                err_msg = str(e)
                start = err_msg.find('/root/.config/QQ/')
                if start == -1: raise
                err_msg = err_msg[start:]
                end = err_msg.find('.png')
                if end == -1: raise
                err_msg = err_msg[:end + 4]
                await run_in_pool(save_video_first_frame, path, err_msg)
                utils_logger.warning(f'发送视频 {path} 失败，尝试手动保存第一帧后重试')
            except:
                raise e
            await self.asend_msg(f"[CQ:video,file=base64://{video}]")


    # -------------------------- 其他 -------------------------- # 

    async def block(self, block_id: str = "", timeout: int = 3 * 60, err_msg: str = None):
        """
        遇到相同block_id调用时阻塞当前指令，超时timeout秒后抛出ReplyException
        """
        block_id = str(block_id)
        block_start_time = datetime.now()
        while True:
            if block_id not in self.handler.block_set:
                break
            if (datetime.now() - block_start_time).seconds > timeout:
                if err_msg is None:
                    err_msg = f'指令执行繁忙(block_id={block_id})，请稍后再试'
                raise ReplyException(err_msg)
            await asyncio.sleep(1)
        self.handler.block_set.add(block_id)
        self.block_ids.append(block_id)


@dataclass
class HelpDocCmdPart:
    """
    帮助文档的单个指令部分
    """
    doc_name: str
    cmds: Set[str] = None
    content: str = ""
    md5: str = "",

@dataclass
class HelpDoc:
    """
    帮助文档
    """
    mtime: int
    parts: List[HelpDocCmdPart] = field(default_factory=list)


SEG_COMMAND_SEPS = ['', ' ', '_']

class SegCmd:
    """
    由多段构成的指令，用于生成不同分隔符的指令
    """
    def __init__(self, *args, seps: List[str]=SEG_COMMAND_SEPS):
        self.commands = set()
        assert len(args) > 0, "至少需要一个参数"
        if len(args) == 1:
            args = args[0]
            for sep in SEG_COMMAND_SEPS:
                if sep:
                    args = args.replace(sep, ' ')
            args = args.split()
        for sep in seps:
            self.commands.add(''.join([sep.join(args)]))

    def get(self) -> List[str]:
        return list(self.commands)


_cmd_history: List[HandlerContext] = []
MAX_CMD_HISTORY = 100

class CmdHandler:
    """
    命令处理器，封装了指令的注册和处理逻辑
    """
    cmd_handlers: list["CmdHandler"] = []
    HELP_PART_IMG_CACHE_DIR = "data/utils/help_part_img_cache/"
    help_docs: Dict[str, HelpDoc] = {}

    def __init__(
            self, 
            commands: Union[str, SegCmd, List[Union[str, SegCmd]]], 
            logger: Logger, 
            error_reply=True, 
            priority=0, 
            block=True, 
            only_to_me=False, 
            disabled=False, 
            banned_cmds: List[str] = None, 
            check_group_enabled=True,
            allow_bot_reply_msg=False,
            help_command: str=None,
            disable_help=False,
            help_trigger_condition: Union[str, Callable] = 'exact',
            use_seg_cmd=True,
        ):
        if isinstance(commands, str) or isinstance(commands, SegCmd):
            commands = [commands]
        self.commands = []
        for cmd in commands:
            if isinstance(cmd, str):
                if use_seg_cmd:
                    self.commands.extend(SegCmd(cmd).get())
                else:
                    self.commands.append(cmd)
            elif isinstance(cmd, SegCmd):
                self.commands.extend(cmd.get())
            else:
                raise Exception(f'未知的指令类型 {type(cmd)}')
        self.commands = list(set(self.commands)) 
        self.commands.sort(key=lambda x: len(x), reverse=True)
            
        self.logger = logger
        self.error_reply = error_reply
        self.check_group_enabled = check_group_enabled
        handler_kwargs = {}
        if only_to_me: handler_kwargs["rule"] = rule_to_me()
        self.handler = on_command(self.commands[0], priority=priority, block=block, aliases=set(self.commands[1:]), **handler_kwargs)
        self.superuser_check = None
        self.private_group_check = None
        self.wblist_checks = []
        self.cdrate_checks = []
        self.disabled = disabled
        self.banned_cmds = banned_cmds or []
        if isinstance(self.banned_cmds, str):
            self.banned_cmds = [self.banned_cmds]
        self.block_set = set()
        self.allow_bot_reply_msg = allow_bot_reply_msg
        self.help_command = help_command
        self.disable_help = disable_help
        if isinstance(help_trigger_condition, str):
            assert help_trigger_condition in ['contain', 'exact']
        self.help_trigger_condition = help_trigger_condition

        self.priority = priority
        self.only_to_me = only_to_me
        self.handler_func = None

        CmdHandler.cmd_handlers.append(self)
        CmdHandler.cmd_handlers.sort(key=lambda x: x.priority, reverse=True)
        # utils_logger.info(f'注册指令 {commands[0]}')

    def check_group(self):
        self.private_group_check = "group"
        return self
    
    def check_private(self):
        self.private_group_check = "private"
        return self

    def check_wblist(self, wblist: GroupWhiteList | GroupBlackList, allow_private=True, allow_super=True):
        self.wblist_checks.append((wblist, { "allow_private": allow_private, "allow_super": allow_super }))
        return self

    def check_cdrate(self, cd_rate: ColdDown | RateLimit, allow_super=True, verbose=True):
        self.cdrate_checks.append((cd_rate, { "allow_super": allow_super, "verbose": verbose }))
        return self

    def check_superuser(self, superuser: Union[List[int], ConfigItem]=SUPERUSER_CFG):
        self.superuser_check = { "superuser": superuser }
        return self

    @classmethod
    def update_help_docs(cls):
        """
        更新帮助文档列表
        """
        HELP_DOC_PATH = "helps/*.md"
        paths = list(glob.glob(HELP_DOC_PATH))
        names = set()
        all_md5 = set()

        def parse_doc(path: str) -> List[HelpDocCmdPart]:
            help_doc = Path(path).read_text(encoding="utf-8")
            doc_name = Path(path).stem
            parts = help_doc.split("---")[2:-1] # 每个小标题
            ret: List[HelpDocCmdPart] = []   # 每个指令的部分
            for part in parts:
                start = part.find("### ")   
                if start == -1:  continue
                part = part[start:]
                for p in part.split("### "):
                    p = p.strip()
                    if p:
                        ret.append(HelpDocCmdPart(
                            cmds=None,
                            doc_name=doc_name, 
                            content="### " + p + f"\n\n>发送`/help {doc_name}`查看完整帮助",
                        ))
            for part in ret:
                lines = part.content.splitlines()
                if len(lines) < 2: continue
                start = lines[1].find("`")
                if start == -1: continue
                part.cmds = set(lines[1][start:].replace("` `", "%").replace("`", "").strip().split("%"))
                part.md5 = get_md5(part.content)
                # print(f"解析帮助: {part.doc_name} {part.cmds} {truncate(part.content, 50)}")
            return ret

        for path in paths:
            try:
                name = Path(path).stem
                mtime = int(os.path.getmtime(path))
                if name not in cls.help_docs or cls.help_docs[name].mtime < mtime:
                    cls.help_docs[name] = HelpDoc(mtime=mtime)
                    cls.help_docs[name].parts = parse_doc(path)
                names.add(name)
            except:
                utils_logger.print_exc(f"解析帮助文档 {path} 失败")

        for name in list(cls.help_docs.keys()):
            if name not in names:
                del cls.help_docs[name]

        # 删除不在帮助文档中的缓存图片
        for doc in cls.help_docs.values():
            for part in doc.parts:
                if part.md5:
                    all_md5.add(part.md5)
        for path in glob.glob(os.path.join(cls.HELP_PART_IMG_CACHE_DIR, "*.png")):
            name = Path(path).stem
            if name not in all_md5:
                try: 
                    os.remove(path)
                except Exception as e:
                    utils_logger.print_exc(f"删除帮助文档图片缓存 {path} 失败: {e}")
        
    @classmethod
    def find_cmd_help_doc(cls, cmd: str) -> Optional[HelpDocCmdPart]:
        cls.update_help_docs()
        for doc in cls.help_docs.values():
            for part in doc.parts:
                if part.cmds and cmd in part.cmds:
                    return part
        return None

    @classmethod
    async def get_cmd_help_doc_img(cls, part: HelpDocCmdPart, width=600) -> Image.Image:
        md5 = get_md5(part.content)
        cache_path = create_parent_folder(os.path.join(cls.HELP_PART_IMG_CACHE_DIR, f"{md5}.png"))
        if os.path.exists(cache_path):
            return open_image(cache_path)
        img = await markdown_to_image(part.content, width=width)
        img.save(cache_path)
        return img
 
    async def additional_context_process(self, context: HandlerContext):
        return context

    def handle(self):
        def decorator(handler_func):
            @self.handler.handle()
            async def func(bot: Bot, event: MessageEvent):
                # utils_logger.info(f'Handler {self.commands[0]} 收到指令: {event.message.extract_plain_text()}')

                if self.disabled:
                    return

                # 安全模式
                if on_safe_mode() and not check_superuser(event):
                    return
                
                with ProfileTimer("handler.check_privilege"):
                    # 禁止私聊自己的指令生效
                    if not is_group_msg(event) and event.user_id == event.self_id:
                        self.logger.warning(f'取消私聊自己的指令处理')
                        return
                    
                    # 禁止bot回复自己的消息重复触发
                    if not self.allow_bot_reply_msg and event.message_id in _bot_reply_msg_ids:
                        return
                    
                    # 检测群聊是否启用
                    if self.check_group_enabled and is_group_msg(event) and check_group_disabled(event.group_id):
                        # self.logger.warning(f'取消未启用群聊 {event.group_id} 的指令处理')
                        return

                    # 检测黑名单
                    if check_in_blacklist(event.user_id):
                        self.logger.warning(f'取消黑名单用户 {event.user_id} 的指令处理')
                        return

                    # 权限检查
                    if self.private_group_check == "group" and not is_group_msg(event):
                        return
                    if self.private_group_check == "private" and is_group_msg(event):
                        return
                    if self.superuser_check and not check_superuser(event, **self.superuser_check):
                        return
                    for wblist, kwargs in self.wblist_checks:
                        if not wblist.check(event, **kwargs):
                            return

                    # 每日上限检查
                    if not check_send_msg_daily_limit(int(bot.self_id)) and not check_superuser(event, **self.superuser_check):
                        return

                    # cd检查
                    for cdrate, kwargs in self.cdrate_checks:
                        if not (await cdrate.check(event, **kwargs)):
                            return

                with ProfileTimer("handler.construct_context"):
                    # 上下文构造
                    context = HandlerContext()
                    context.time = datetime.now()
                    context.handler = self
                    context.nonebot_handler = self.handler
                    context.bot = bot
                    context.event = event
                    context.logger = self.logger

                    plain_text = event.message.extract_plain_text()
                    cmd_starts = []
                    for cmd in sorted(self.commands, key=len, reverse=True):
                        start = plain_text.find(cmd)
                        cmd_starts.append((cmd, start if start != -1 else float('inf')))
                    cmd_starts.sort(key=lambda x: x[1])
                    context.trigger_cmd = cmd_starts[0][0]
                    context.arg_text = plain_text[cmd_starts[0][1] + len(context.trigger_cmd):]

                    if any([banned_cmd in context.trigger_cmd for banned_cmd in self.banned_cmds]):
                        return

                    context.message_id = event.message_id
                    context.user_id = event.user_id
                    if is_group_msg(event):
                        context.group_id = event.group_id

                    # 记录到历史
                    global _cmd_history, MAX_CMD_HISTORY
                    if context.trigger_cmd:
                        _cmd_history.append(context)
                        if len(_cmd_history) > MAX_CMD_HISTORY:
                            _cmd_history = _cmd_history[-MAX_CMD_HISTORY:]

                try:
                    with ProfileTimer("handler.additional_context_process"):
                        # 额外处理，用于子类自定义
                        context = await self.additional_context_process(context)
                        assert context, "额外处理返回值不能为空"

                    # 帮助文档
                    if not self.disable_help:
                        for help_keyword in ('help', '帮助'):
                            ok = False
                            if isinstance(self.help_trigger_condition, str):
                                match self.help_trigger_condition:
                                    case 'contain':
                                        ok = help_keyword in context.arg_text
                                    case 'exact':
                                        ok = context.arg_text.strip() == help_keyword
                            else:
                                ok = self.help_trigger_condition(context.arg_text)
                            if ok:
                                cmds = self.commands if not self.help_command else [self.help_command]
                                for cmd in cmds:
                                    part = self.find_cmd_help_doc(cmd)
                                    if part:
                                        img = await self.get_cmd_help_doc_img(part)
                                        return await context.asend_reply_msg(await get_image_cq(img, low_quality=True))
                                raise ReplyException(f"没有找到该指令的帮助\n发送\"/help\"查看完整帮助")

                    # 执行函数
                    return await handler_func(context)
                
                except NoReplyException:
                    return
                except ReplyException as e:
                    return await context.asend_reply_msg(str(e))
                except Exception as e:
                    exc_desc = get_exc_desc(e)
                    self.logger.print_exc(f'指令\"{context.trigger_cmd}\"处理失败')
                    if self.error_reply:
                        if not ('ActionFailed' in exc_desc and 'Timeout' in exc_desc):
                            await context.asend_reply_msg(truncate(f"指令处理失败: {exc_desc}", 256))
                finally:
                    for block_id in context.block_ids:
                        self.block_set.discard(block_id)

            self.handler_func = func 
            return func
        return decorator



@dataclass
class CurrentGroupInfo:
    group_id: int
    group_member_ids: set[int]

class CurrentGroupInfoDict(dict[int, CurrentGroupInfo]):
    pass

@dataclass
class QuitedGroupUserInfo:
    quited_group_ids: list[int] = field(default_factory=list)
    quited_group_users: list[tuple[int, int]] = field(default_factory=list)

_on_collect_quit_group_handlers: list[Callable[[CurrentGroupInfoDict], QuitedGroupUserInfo]] = []
def on_collect_quited_group(func: Callable[[CurrentGroupInfoDict], QuitedGroupUserInfo]):
    if asyncio.iscoroutinefunction(func):
        async def wrapper(current_groups: CurrentGroupInfoDict) -> QuitedGroupUserInfo:
            return await func(current_groups)
    else:
        def wrapper(current_groups: CurrentGroupInfoDict) -> list[int]:
            return func(current_groups)
    _on_collect_quit_group_handlers.append(wrapper)
    return wrapper

_on_clean_quit_group_handlers: list[Callable[[CurrentGroupInfoDict], Any]] = []
def on_clean_quited_group(func: Callable[[CurrentGroupInfoDict], Any]):
    if asyncio.iscoroutinefunction(func):
        async def wrapper(current_groups: CurrentGroupInfoDict):
            await func(current_groups)
    else:
        def wrapper(current_groups: CurrentGroupInfoDict):
            func(current_groups)
    _on_clean_quit_group_handlers.append(wrapper)
    return wrapper



DEFAULT_CONFIRM_ACTIONS_TIMEOUT_CFG = global_config.item('confirm_actions_timeout_seconds')
_need_confirm_actions: dict[tuple[int, int], tuple[datetime, Callable[[HandlerContext], Any]]] = {}

async def add_need_confirm_action(
    ctx: HandlerContext, 
    action: Callable[[HandlerContext], Any], 
    additional_msg: str = None, 
    timeout: timedelta = None,
    fold_msg: bool = True,
):
    key = (ctx.user_id, ctx.group_id or None)
    if timeout is None:
        timeout = timedelta(seconds=DEFAULT_CONFIRM_ACTIONS_TIMEOUT_CFG.get())
    _need_confirm_actions[key] = (datetime.now() + timeout, action)
    msg = f'请发送"/确认"或"/取消"以继续操作'
    if additional_msg:
        msg = f'{additional_msg}\n' + msg
    if fold_msg:
        await ctx.asend_fold_msg_adaptive(msg, need_reply=True)
    else:
        await ctx.asend_reply_msg(msg)

@repeat_with_interval(10, "清空过期确认操作", utils_logger)
async def _clean_expired_confirm_actions():
    now = datetime.now()
    for key, (expire_time, action) in list(_need_confirm_actions.items()):
        if now >= expire_time:
            del _need_confirm_actions[key]


# ============================ 订阅管理 ============================ #

class SubHelper:
    def __init__(self, name: str, db: FileDB, logger: Logger, key_fn=None, val_fn=None):
        self.name = name
        self.db = db
        self.logger = logger
        self.key_fn = key_fn or (lambda x: str(x))
        self.val_fn = val_fn or (lambda x: x)
        self.key = f'{self.name}_sub_list'

    def is_subbed(self, *args):
        uid = self.key_fn(*args)
        return uid in self.db.get(self.key, [])

    def sub(self, *args):
        uid = self.key_fn(*args)
        lst = self.db.get(self.key, [])
        if uid in lst:
            return False
        lst.append(uid)
        self.db.set(self.key, lst)
        self.logger.log(f'{uid}订阅{self.name}')
        return True

    def unsub(self, *args):
        uid = self.key_fn(*args)
        lst = self.db.get(self.key, [])
        if uid not in lst:
            return False
        lst.remove(uid)
        self.db.set(self.key, lst)
        self.logger.log(f'{uid}取消订阅{self.name}')
        return True

    def get_all(self):
        return [self.val_fn(item) for item in self.db.get(self.key, [])]

    def clear(self):
        self.db.delete(self.key)
        self.logger.log(f'{self.name}清空订阅')

class GroupSubHelper(SubHelper):
    def __init__(self, name: str, db: FileDB, logger: Logger):
        super().__init__(name, db, logger, key_fn=lambda x: str(x), val_fn=lambda x: int(x))

        @on_collect_quited_group
        def _(current_groups: CurrentGroupInfoDict) -> QuitedGroupUserInfo:
            all_subs = self.get_all().copy()
            return QuitedGroupUserInfo(
                quited_group_ids=list(set(gid for gid in all_subs if gid not in current_groups)),
            )
        
        @on_clean_quited_group
        def _(current_groups: CurrentGroupInfoDict):
            all_subs = self.get_all().copy()
            for gid in all_subs:
                if gid not in current_groups:
                    self.unsub(gid)

    def is_subbed(self, group_id: int):
        return super().is_subbed(group_id)
    
    def sub(self, group_id: int):
        return super().sub(group_id)
    
    def unsub(self, group_id: int):
        return super().unsub(group_id)
    
    def get_all(self) -> list[int]:
        return super().get_all()
    
    def clear(self):
        super().clear()

class GroupUserSubHelper(SubHelper):
    def __init__(self, name: str, db: FileDB, logger: Logger):
        super().__init__(
            name, 
            db, 
            logger, 
            key_fn=lambda user_id, group_id: f"{user_id}@{group_id}", 
            val_fn=lambda key: list(map(int, key.split('@'))),
        )

        @on_collect_quited_group
        def _(current_groups: CurrentGroupInfoDict) -> QuitedGroupUserInfo:
            all_subs = self.get_all().copy()
            quited_groups = []
            quited_group_users = []
            for uid, gid in all_subs:
                if gid not in current_groups:
                    quited_groups.append(gid)
                elif uid not in current_groups[gid].group_member_ids:
                    quited_group_users.append((gid, uid))
            return QuitedGroupUserInfo(
                quited_group_ids=quited_groups,
                quited_group_users=quited_group_users,
            )

        @on_clean_quited_group
        def _(current_groups: CurrentGroupInfoDict):
            all_subs = self.get_all().copy()
            for uid, gid in all_subs:
                if gid not in current_groups or uid not in current_groups[gid].group_member_ids:
                    self.unsub(uid, gid)

    def is_subbed(self, user_id: int, group_id: int):
        return super().is_subbed(user_id, group_id)
    
    def sub(self, user_id: int, group_id: int):
        return super().sub(user_id, group_id)
    
    def unsub(self, user_id: int, group_id: int):
        return super().unsub(user_id, group_id)
    
    def get_all(self) -> list[tuple[int, int]]:
        """注意返回是 list[(user_id, group_id)] """
        return super().get_all()
    
    def clear(self):
        super().clear()


# ============================ 指令处理 ============================ #

cd = ColdDown(utils_file_db, utils_logger, cold_down_name='global_cmd')

# 获取当前群聊开启和关闭的服务 或 获取某个服务在哪些群聊开启
_handler = CmdHandler(['/service', '/服务'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    name = ctx.get_args().strip()

    if name:
        assert_and_reply(name in _gwls or name in _gbls, f"未知服务 {name}")
        msg = ""
        if name in _gwls:
            msg += f"{name}使用的规则是白名单\n开启服务的群聊有:\n"
            for group_id in _gwls[name].get():
                msg += f'{await get_group_name(ctx.bot, group_id)}({group_id})\n'
        elif name in _gbls:
            msg += f"{name}使用的规则是黑名单\n关闭服务的群聊有:\n"
            for group_id in _gbls[name].get():
                msg += f'{await get_group_name(ctx.bot, group_id)}({group_id})\n'
        return await ctx.asend_reply_msg(msg.strip())

    msg_on = "本群开启的服务:\n"
    msg_off = "本群关闭的服务:\n"
    for name, gwl in _gwls.items():
        if gwl.check_id(ctx.group_id):
            msg_on += f'{name} '
        else:
            msg_off += f'{name} '
    for name, gbl in _gbls.items():
        if gbl.check_id(ctx.group_id):
            msg_on += f'{name} '
        else:
            msg_off += f'{name} '

    return await ctx.asend_reply_msg(msg_on + '\n' + msg_off)

# 设置群聊开启
_handler = CmdHandler(['/enable'], utils_logger, check_group_enabled=False, only_to_me=True)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    try: group_id = int(args)
    except: 
        assert_and_reply(ctx.group_id, "请指定群号")
        group_id = ctx.group_id
    group_name = await get_group_name(ctx.bot, group_id)
    set_group_enable(group_id, True)
    return await ctx.asend_reply_msg(f'已启用群聊 {group_name} ({group_id}) BOT服务')

# 设置群聊关闭
_handler = CmdHandler(['/disable'], utils_logger, check_group_enabled=False)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    try: group_id = int(args)
    except: 
        assert_and_reply(ctx.group_id, "请指定群号")
        group_id = ctx.group_id
    group_name = await get_group_name(ctx.bot, group_id)
    set_group_enable(group_id, False)
    return await ctx.asend_reply_msg(f'已禁用群聊 {group_name} ({group_id}) BOT服务')

# 查看群聊列表的开启状态
_handler = CmdHandler(['/group status'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    enabled_msg = "【已启用的群聊】"
    disabled_msg = "【已禁用的群聊】"
    enabled_groups = utils_file_db.get("enabled_groups", [])
    for group_id in await get_group_ids(ctx.bot):
        group_name = await get_group_name(ctx.bot, group_id)
        if group_id in enabled_groups:
            enabled_msg += f'\n{group_name} ({group_id})'
        else:
            disabled_msg += f'\n{group_name} ({group_id})'
    return await ctx.asend_reply_msg(enabled_msg + '\n\n' + disabled_msg)

# 添加qq号到黑名单
_handler = CmdHandler(['/blacklist add'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    try: 
        user_ids = [int(x) for x in args.split()]
        assert user_ids
    except: 
        raise ReplyException("请指定要添加到黑名单的QQ号")

    msg = ""
    blacklist = utils_file_db.get("blacklist", [])
    for user_id in user_ids:
        if user_id in blacklist:
            msg += f'QQ号 {user_id} 已在黑名单中\n'
        else:
            blacklist.append(user_id)
            msg += f'已将QQ号 {user_id} 添加到黑名单\n'
    utils_file_db.set("blacklist", blacklist)

    return await ctx.asend_reply_msg(msg.strip())

# 删除黑名单中的qq号
_handler = CmdHandler(['/blacklist del'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    try: 
        user_ids = [int(x) for x in args.split()]
        assert user_ids
    except: 
        raise ReplyException("请指定要删除黑名单的QQ号")

    msg = ""
    blacklist = utils_file_db.get("blacklist", [])
    for user_id in user_ids:
        if user_id not in blacklist:
            msg += f'QQ号 {user_id} 不在黑名单中\n'
        else:
            blacklist.remove(user_id)
            msg += f'已将QQ号 {user_id} 从黑名单中删除\n'
    utils_file_db.set("blacklist", blacklist)

    return await ctx.asend_reply_msg(msg.strip())

# 获取当日消息发送数量
_handler = CmdHandler(['/send count'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    count = get_send_msg_daily_count(int(ctx.bot.self_id))
    return await ctx.asend_reply_msg(f'该账号今日已发送消息数量: {count}')

# 删除Painter缓存
_handler = CmdHandler(['/clear pcache', '/pcache clear'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    key = ctx.get_args().strip()
    ok_count = Painter.clear_cache(key)
    return await ctx.asend_reply_msg(f'已清除Painter缓存: {ok_count}个文件被删除')

# 查看Painter缓存
_handler = CmdHandler(['/pcache'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    ret = Painter.get_cache_key_mtimes()
    msg = f"当前Painter缓存的keys:\n"
    for key, mtime in ret.items():
        msg += f"{key}: {mtime.strftime('%Y-%m-%d %H:%M:%S')}\n"
    return await ctx.asend_reply_msg(msg.strip())

# 安全模式
_handler = CmdHandler(['/safe'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    safe_mode = utils_file_db.get("safe_mode", False)
    safe_mode = not safe_mode
    utils_file_db.set("safe_mode", safe_mode)
    return await ctx.asend_reply_msg(f'已{"开启" if safe_mode else "关闭"}安全模式')

# 确认操作
_handler = CmdHandler(['/confirm', '/确认'], utils_logger)
_handler.check_cdrate(cd)
@_handler.handle()
async def _(ctx: HandlerContext):
    key = (ctx.user_id, ctx.group_id or None)
    assert_and_reply(_need_confirm_actions.get(key), "当前没有需要确认的操作")
    _, action = _need_confirm_actions[key]
    del _need_confirm_actions[key]
    await call_common_or_async(action, ctx)
    
# 取消操作
_handler = CmdHandler(['/cancel', '/取消'], utils_logger)
_handler.check_cdrate(cd)
@_handler.handle()
async def _(ctx: HandlerContext):
    key = (ctx.user_id, ctx.group_id or None)
    assert_and_reply(_need_confirm_actions.get(key), "当前没有需要取消的操作")
    del _need_confirm_actions[key]
    await ctx.asend_reply_msg("已取消最近的操作")

# 清除退出群聊的订阅数据
_handler = CmdHandler(['/clean group', '/清理退群'], utils_logger)
_handler.check_superuser()
@_handler.handle()
async def _(ctx: HandlerContext):
    await ctx.block()
    current_groups: CurrentGroupInfoDict = {}
    for bot in get_all_bots():
        for group_id in await get_group_ids(bot, refresh=True):
            group_member_ids = [m['user_id'] for m in await get_group_users(bot, group_id)]
            current_groups[group_id] = CurrentGroupInfo(
                group_id=group_id,
                group_member_ids=set(group_member_ids),
            )
            
    quited_groups: set[int] = set()
    quited_group_users: set[tuple[int, int]] = set()
    for func in _on_collect_quit_group_handlers:
        try:
            ret: QuitedGroupUserInfo = await call_common_or_async(func, current_groups)
            quited_groups.update(ret.quited_group_ids)
            quited_group_users.update(ret.quited_group_users)
        except:
            utils_logger.print_exc("收集已退出群聊数据失败")
            raise ReplyException("收集已退出群聊数据失败")

    if not quited_groups and not quited_group_users:
        return await ctx.asend_reply_msg("没有需要清理的已退出群聊数据")

    async def clean(ctx: HandlerContext):
        for func in _on_clean_quit_group_handlers:
            try:
                await call_common_or_async(func, current_groups)
            except:
                utils_logger.print_exc("清理已退出群聊数据失败")
        return await ctx.asend_reply_msg(f"成功清理已退出群聊数据")

    msg = "发现已退出群聊:"
    for gid in quited_groups:
        msg += f'\n{gid}'
    group_exited_users: dict[int, list[int]] = {}
    for (gid, uid) in quited_group_users:
        group_exited_users.setdefault(gid, []).append(uid)
    for gid, uids in group_exited_users.items():
        group_bot = await aget_group_bot(gid)
        group_name = await get_group_name(group_bot, gid)
        msg += f'\n已退出群聊 {group_name}({gid}) 的用户:\n' + ' | '.join([str(uid) for uid in uids])

    await add_need_confirm_action(ctx, clean, additional_msg=msg)
    

exec_code = CmdHandler(['/exec', '/执行'], utils_logger)
exec_code.check_superuser()
@exec_code.handle()
async def _(ctx: HandlerContext):
    if not global_config.get("allow_code_exec", False):
        return await ctx.asend_reply_msg("该功能未在配置中启用")
    code = ctx.get_args().strip()
    assert_and_reply(code, "请输入要运行的代码")
    try:
        # 将代码包装在异步函数中执行，最后一行作为返回值
        lines = code.rstrip().split('\n')
        if not lines:
            result = None
        else:
            # 检查最后一行是否是表达式
            # 表达式特征：不以语句关键字开头，且不是赋值语句
            last_line = lines[-1].strip()
            statement_keywords = ('def ', 'class ', 'if ', 'for ', 'while ', 'with ', 'try ', 'except ', 
                                 'finally:', 'else:', 'elif ', 'import ', 'from ', 'return ', 'break ', 
                                 'continue ', 'pass ', 'raise ', 'assert ', 'del ', 'global ', 'nonlocal ')
            
            # 检查是否是赋值语句（简单判断：包含 = 且不在字符串中）
            is_assignment = False
            if '=' in last_line:
                # 排除 ==, !=, <=, >=, +=, -= 等情况
                import re
                # 查找单独的 = 号（前后不是 =）
                if re.search(r'[^=<>!+\-*/%]=[^=]', last_line.split('#')[0]):
                    is_assignment = True
            
            is_expression = (
                last_line and 
                not any(last_line.startswith(kw) for kw in statement_keywords) and
                not is_assignment
            )
            
            if is_expression:
                # 最后一行是表达式，将其作为返回值
                body_lines = lines[:-1]
                last_expr_content = lines[-1].strip()
                # 为所有行添加一级缩进（函数体缩进）
                if body_lines:
                    indented_body = '\n'.join('    ' + line for line in body_lines)
                    wrapped_code = f'{indented_body}\n    return {last_expr_content}'
                else:
                    wrapped_code = f'    return {last_expr_content}'
            else:
                # 最后一行是语句，执行但不返回
                wrapped_code = '\n'.join('    ' + line for line in lines)
            
            # 创建异步函数并执行
            async_func_code = f'async def _exec_code_func():\n{wrapped_code}'
            utils_logger.info(f"执行代码:\n{async_func_code}")
            local_vars = {}
            exec(async_func_code, globals(), local_vars)
            result = await local_vars['_exec_code_func']()
        
        result = str(result)
        OUTPUT_LIMIT = 2048
        if len(result) > OUTPUT_LIMIT:
            omit_len = len(result) - OUTPUT_LIMIT
            result = result[:OUTPUT_LIMIT] + f"...(输出过长，已省略{omit_len}字符)"
        await ctx.asend_fold_msg_adaptive(f"执行代码成功:\n{result}")

    except Exception as e:
        await ctx.asend_fold_msg_adaptive(f"执行代码失败\n{traceback.format_exc()}")

