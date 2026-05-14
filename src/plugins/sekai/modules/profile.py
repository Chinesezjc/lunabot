from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..gameapi import get_gameapi_config, request_gameapi
from ..suite import Suite
from .honor import compose_full_honor_image
from .resbox import get_res_box_info, get_res_icon
from ...utils.safety import *
from ...imgtool import shrink_image


SEKAI_PROFILE_DIR = f"{SEKAI_DATA_DIR}/profile"
profile_db = get_file_db(f"{SEKAI_PROFILE_DIR}/db.json", logger)
bind_history_db = get_file_db(f"{SEKAI_PROFILE_DIR}/bind_history.json", logger)
player_frame_db = get_file_db(f"{SEKAI_PROFILE_DIR}/player_frame.json", logger)

DAILY_BIND_LIMITS = config.item('bind.daily_limits')
TOTAL_BIND_LIMITS = config.item('bind.total_limits')


@dataclass
class PlayerAvatarInfo:
    card_id: int
    cid: int
    unit: str
    img: Image.Image

DEFAULT_DATA_MODE = 'latest'
VALID_DATA_MODES = ['latest', 'default', 'local', 'haruki']

CN_SUITE_FILTER_ALIASES = {
    "userMusicResults": "compactUserMusicResults",
    "userMusicAchievements": "compactUserMusicAchievements",
    "userCharacterMissionV2Statuses": "compactUserCharacterMissionV2Statuses",
}


def expand_suite_filter_keys(
    region: str,
    mode: str | None,
    keys: list[str] | set[str] | None,
) -> list[str]:
    if not keys:
        return []
    expanded = list(dict.fromkeys([str(k) for k in keys]))
    if region != "cn":
        return expanded
    if mode == "haruki":
        return expanded
    for key in list(expanded):
        alias = CN_SUITE_FILTER_ALIASES.get(key)
        if alias and alias not in expanded:
            expanded.append(alias)
    return expanded


def get_suite_source_error(mode: str | None, payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("error"), str) and payload["error"].strip():
        return payload["error"].strip()
    if payload:
        return None

    source = "数据源"
    if mode == "local":
        source = "本地数据"
    elif mode == "haruki":
        source = "Haruki工具箱"
    return f"{source}中没有找到该账号的Suite抓包数据"


@dataclass
class VerifyCode:
    region: str
    qid: int
    uid: int
    expire_time: datetime
    verify_code: str

VERIFY_CODE_EXPIRE_TIME = timedelta(minutes=30)
_region_qid_verify_codes: Dict[str, Dict[str, VerifyCode]] = {}
verify_rate_limit = RateLimit(file_db, logger, 10, 'd', rate_limit_name='pjsk验证')


@dataclass
class ProfileBgSettings:
    image: Image.Image
    blur: int = None
    alpha: int = None
    vertical: bool = False

PROFILE_BG_IMAGE_PATH = f"{SEKAI_PROFILE_DIR}/profile_bg/" + "{region}/{uid}.jpg"
profile_bg_settings_db = get_file_db(f"{SEKAI_PROFILE_DIR}/profile_bg_settings.json", logger)
profile_bg_upload_rate_limit = RateLimit(file_db, logger, 10, 'd', rate_limit_name='个人信息背景上传')

PROFILE_HORIZONTAL_KEYWORDS = ('横屏', '横向', '横版',)
PROFILE_VERTICAL_KEYWORDS = ('竖屏', '竖向', '竖版', '纵向',)


# ======================= 卡牌逻辑（防止循环依赖） ======================= #

CARD_ICON_CACHE_RES = 128 * 128

# 判断卡牌是否有after_training模式
def has_after_training(card):
    return card['cardRarityType'] in ["rarity_3", "rarity_4"]

# 判断卡牌是否只有after_training模式
def only_has_after_training(card):
    return card.get('initialSpecialTrainingStatus') == 'done'

# 获取角色卡牌缩略图
async def get_card_thumbnail(ctx: SekaiHandlerContext, cid: int, after_training: bool, high_res: bool=False):
    image_type = "after_training" if after_training else "normal"
    card = await ctx.md.cards.find_by_id(cid)
    assert_and_reply(card, f"找不到ID为{cid}的卡牌")
    img_cache_kwargs = {}
    if not high_res:
        img_cache_kwargs = {'use_img_cache': True, 'img_cache_max_res': CARD_ICON_CACHE_RES }
    return await ctx.rip.img(f"thumbnail/chara_rip/{card['assetbundleName']}_{image_type}.png", **img_cache_kwargs)

# 获取角色卡牌完整缩略图（包括边框、星级等）
async def get_card_full_thumbnail(
    ctx: SekaiHandlerContext, 
    card_or_card_id: Dict, 
    after_training: bool=None, 
    pcard: Dict=None, 
    custom_text: str=None,
    level_label: str="lv",
    high_res: bool=False,
):
    if isinstance(card_or_card_id, int):
        card = await ctx.md.cards.find_by_id(card_or_card_id)
        assert_and_reply(card, f"找不到ID为{card_or_card_id}的卡牌")
    else:
        card = card_or_card_id
    cid = card['id']

    if not pcard:
        after_training = after_training and has_after_training(card)
        rare_image_type = "after_training" if after_training else "normal"
    else:
        after_training = pcard['defaultImage'] == "special_training"
        rare_image_type = "after_training" if pcard['specialTrainingStatus'] == "done" else "normal"

    # 如果没有指定pcard则尝试使用缓存
    if not pcard:
        image_type = "after_training" if after_training else "normal"
        cache_path = f"{SEKAI_ASSET_DIR}/card_full_thumbnail/{ctx.region}/{cid}_{image_type}.png"
        try: return open_image(cache_path)
        except: pass

    img = await get_card_thumbnail(ctx, cid, after_training, high_res=high_res)
    ok_to_cache = (img != UNKNOWN_IMG)
    img = img.resize((128, 128), Image.BICUBIC)

    def draw(img: Image.Image, card):
        attr = card['attr']
        rare = card['cardRarityType']
        frame_img = ctx.static_imgs.get(f"card/frame_{rare}.png")
        attr_img = ctx.static_imgs.get(f"card/attr_{attr}.png")
        if rare == "rarity_birthday":
            rare_img = ctx.static_imgs.get(f"card/rare_birthday.png")
            rare_num = 1
        else:
            rare_img = ctx.static_imgs.get(f"card/rare_star_{rare_image_type}.png") 
            rare_num = int(rare.split("_")[1])

        img_w, img_h = img.size

        # 如果是profile卡片则绘制等级/加成
        if pcard:
            if custom_text is not None:
                draw = ImageDraw.Draw(img)
                draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
                draw.text((6, img_h - 31), custom_text, font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)
            else:
                level_label_lower = str(level_label).lower()
                if level_label_lower == "slv":
                    level = pcard.get('skillLevel', 1)
                    text = f"SLv.{level}"
                else:
                    level = pcard['level']
                    text = f"Lv.{level}"
                draw = ImageDraw.Draw(img)
                draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
                draw.text((6, img_h - 25), text, font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)

        # 绘制边框
        frame_img = frame_img.resize((img_w, img_h))
        img.paste(frame_img, (0, 0), frame_img)
        # 绘制特训等级
        if pcard:
            rank = pcard['masterRank']
            if rank:
                rank_img = ctx.static_imgs.get(f"card/train_rank_{rank}.png")
                rank_img = rank_img.resize((int(img_w * 0.35), int(img_h * 0.35)))
                rank_img_w, rank_img_h = rank_img.size
                img.paste(rank_img, (img_w - rank_img_w, img_h - rank_img_h), rank_img)
        # 左上角绘制属性
        attr_img = attr_img.resize((int(img_w * 0.22), int(img_h * 0.25)))
        img.paste(attr_img, (1, 0), attr_img)
        # 左下角绘制稀有度
        hoffset, voffset = 6, 6 if not pcard else 24
        scale = 0.17 if not pcard else 0.15
        rare_img = rare_img.resize((int(img_w * scale), int(img_h * scale)))
        rare_w, rare_h = rare_img.size
        for i in range(rare_num):
            img.paste(rare_img, (hoffset + rare_w * i, img_h - rare_h - voffset), rare_img)
        mask = Image.new('L', (img_w, img_h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, img_w, img_h), radius=10, fill=255)
        img.putalpha(mask)
        return img
    
    img = await run_in_pool(draw, img, card)

    if not pcard and ok_to_cache:
        create_parent_folder(cache_path)
        img.save(cache_path)

    return img

# 获取卡牌所属团名（return_support控制VS是否返回对应的所属团）
async def get_unit_by_card_id(ctx: SekaiHandlerContext, card_id: int, return_support: bool = True) -> str:
    card = await ctx.md.cards.find_by_id(card_id)
    if not card: raise Exception(f"卡牌ID={card_id}不存在")
    chara_unit = get_unit_by_chara_id(card['characterId'])
    if not return_support or chara_unit != 'piapro':
        return chara_unit
    return card['supportUnit'] if card['supportUnit'] != "none" else "piapro"


# ======================= 帐号相关 ======================= #

# 为兼容原本数据格式，用户绑定数据可能是字符串或字符串列表
def to_list(s: list | Any) -> list:
    if isinstance(s, list):
        return s
    return [s]

# 验证uid
def validate_uid(ctx: SekaiHandlerContext, uid: str) -> bool:
    uid = str(uid)
    if not (13 <= len(uid) <= 20) or not uid.isdigit():
        return False
    reg_time = get_register_time(ctx.region, uid)
    if not reg_time or not (datetime.strptime("2020-09-01", "%Y-%m-%d") <= reg_time <= datetime.now()):
        return False
    return True

# 获取用户绑定的账号数量
def get_player_bind_count(ctx: SekaiHandlerContext, qid: int) -> int:
    bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {}).get(ctx.region, {})
    uids = to_list(bind_list.get(str(qid), []))
    return len(uids)

# 获取qq用户绑定的游戏id，如果qid=None则使用ctx.uid_arg获取用户id，index=None获取主绑定账号
def get_player_bind_id(ctx: SekaiHandlerContext, qid: int = None, check_bind=True, index: int | None=None) -> str:
    is_super = check_superuser(ctx.event) if ctx.event else False
    region_name = get_region_name(ctx.region)

    bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {}).get(ctx.region, {})
    main_bind_list: Dict[str, str] = profile_db.get("main_bind_list", {}).get(ctx.region, {})

    def get_uid_by_index(qid: str, index: int) -> str | None:
        uids = bind_list.get(qid, [])
        if not uids:
            return None
        uids = to_list(uids)
        assert_and_reply(0 <= index < len(uids), f"指定的账号序号大于已绑定的{region_name}账号数量({len(uids)})")
        return uids[index]

    # 指定qid/没有ctx.uid_arg的情况则直接获取qid绑定的账号
    if qid or not ctx.uid_arg:
        qid = str(qid) if qid is not None else str(ctx.user_id)
        if index is None:
            uid = main_bind_list.get(qid, None) or get_uid_by_index(qid, 0)
        else:
            uid = get_uid_by_index(qid, index)
    # 从ctx.uid_arg中获取
    else:
        if ctx.uid_arg.startswith('u'):
            index = int(ctx.uid_arg[1:]) - 1
            uid = get_uid_by_index(str(ctx.user_id), index)
        elif ctx.uid_arg.startswith('@'):
            assert_and_reply(is_super, "仅bot管理可直接@指定QQ号")
            at_qid = int(ctx.uid_arg[1:])
            uid = get_player_bind_id(ctx, at_qid, check_bind)
        else:
            assert_and_reply(is_super, "仅bot管理可直接指定游戏ID")
            uid = ctx.uid_arg
            if not validate_uid(ctx, uid):
                raise ReplyException(f"指定的游戏ID {uid} 不是有效的{region_name}游戏ID")

    if check_bind and uid is None:
        region = "" if ctx.region == "jp" else ctx.region
        raise ReplyException(f"请使用\"/{region}绑定 你的游戏ID\"绑定账号")
    if not is_super:
        assert_and_reply(not check_uid_in_blacklist(uid), f"该游戏ID({uid})已被拉入黑名单")
    return uid

# 获取某个id在用户绑定的账号中的索引，找不到返回None
def get_player_bind_id_index(ctx: SekaiHandlerContext, qid: str, uid: str) -> int | None:
    bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {}).get(ctx.region, {})
    uids = to_list(bind_list.get(str(qid), []))
    try:
        return uids.index(str(uid))
    except ValueError:
        return None

# 为用户绑定游戏id，该函数仅判断uid是否重复，绑定的uid需要已经验证合法，返回额外信息
def add_player_bind_id(ctx: SekaiHandlerContext, qid: str, uid: str, set_main: bool) -> str:
    all_bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {})
    all_main_bind_list: Dict[str, str] = profile_db.get("main_bind_list", {})
    qid = str(qid)
    region = ctx.region
    region_name = get_region_name(region)
    additional_info = ""

    if region not in all_bind_list:
        all_bind_list[region] = {}
    if region not in all_main_bind_list:
        all_main_bind_list[region] = {}

    uids = to_list(all_bind_list[region].get(qid, []))
    if uid not in uids:
        total_bind_limit = TOTAL_BIND_LIMITS.get().get(ctx.region, 1e9)
        if len(uids) >= total_bind_limit:
            while len(uids) >= total_bind_limit:
                uids.pop(0)
            additional_info += f"你绑定的{region_name}账号数量已达上限({total_bind_limit})，已自动解绑最早绑定的账号\n"
        uids.append(uid)
        
        all_bind_list[region][qid] = uids
        profile_db.set("bind_list", all_bind_list)
        logger.info(f"为 {qid} 绑定 {region_name}账号: {uid}")
    else:
        logger.info(f"为 {qid} 绑定 {region_name}账号: {uid} 已存在，跳过绑定")

    if set_main:
        all_main_bind_list[region][qid] = uid
        profile_db.set("main_bind_list", all_main_bind_list)
        uid_index = uids.index(uid) + 1
        additional_info += f"已将该账号u{uid_index}设为你的{region_name}主账号\n"
        logger.info(f"为 {qid} 设定 {region_name}主账号: {uid}")

    return additional_info.strip()

# 使用索引解除绑定，返回信息，index为None则解除主绑定账号
def remove_player_bind_id(ctx: SekaiHandlerContext, qid: str, index: int | None) -> str:
    all_bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {})
    all_main_bind_list: Dict[str, str] = profile_db.get("main_bind_list", {})
    qid = str(qid)
    region = ctx.region
    region_name = get_region_name(region)
    ret_info = ""

    if region not in all_bind_list:
        all_bind_list[region] = {}
    if region not in all_main_bind_list:
        all_main_bind_list[region] = {}

    uids = to_list(all_bind_list[region].get(qid, []))
    assert_and_reply(uids, f"你还没有绑定任何{region_name}账号")
    assert_and_reply(index < 1e9, f"需要指定账号序号（按绑定时间顺序）而不是账号ID")

    if index is not None:
        assert_and_reply(0 <= index < len(uids), f"指定的账号序号大于已绑定的{region_name}账号数量({len(uids)})")
        removed_uid = uids.pop(index)
    else:
        main_bind_uid = get_player_bind_id(ctx, qid)
        uids.remove(main_bind_uid)
        removed_uid = main_bind_uid

    all_bind_list[region][qid] = uids
    profile_db.set("bind_list", all_bind_list)
    logger.info(f"为 {qid} 解除绑定 {region_name}账号: {removed_uid}")

    ret_info += f"已解除绑定你的{region_name}账号{process_hide_uid(ctx, removed_uid, keep=6)}\n"

    if all_main_bind_list[region].get(qid, None) == removed_uid:
        if uids:
            all_main_bind_list[region][qid] = uids[0]
            ret_info += f"已将你的{region_name}主账号切换为当前第一个账号({process_hide_uid(ctx, uids[0], keep=6)})\n"
            logger.info(f"为 {qid} 切换 {region_name}主账号: {uids[0]}")
        else:
            all_main_bind_list[region].pop(qid, None)
            ret_info += f"你目前没有绑定任何{region_name}账号，主账号已清除\n"
            logger.info(f"为 {qid} 清除 {region_name}主账号")
        profile_db.set("main_bind_list", all_main_bind_list)

    return ret_info.strip()

# 使用索引修改主绑定账号，返回信息
def set_player_main_bind_id(ctx: SekaiHandlerContext, qid: str, index: int) -> str:
    all_bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {})
    all_main_bind_list: Dict[str, str] = profile_db.get("main_bind_list", {})
    qid = str(qid)
    region = ctx.region
    region_name = get_region_name(region)

    if region not in all_bind_list:
        all_bind_list[region] = {}
    if region not in all_main_bind_list:
        all_main_bind_list[region] = {}

    uids = to_list(all_bind_list[region].get(qid, []))
    assert_and_reply(uids, f"你还没有绑定任何{region_name}账号")
    assert_and_reply(index < 1e9, f"需要指定账号序号（按绑定时间顺序）而不是账号ID")
    assert_and_reply(0 <= index < len(uids), f"指定的账号序号大于已绑定的{region_name}账号数量({len(uids)})")

    new_main_uid = uids[index]
    all_main_bind_list[region][qid] = new_main_uid
    profile_db.set("main_bind_list", all_main_bind_list)

    return f"已将你的{region_name}主账号修改为{process_hide_uid(ctx, new_main_uid, keep=6)}"

# 使用索引交换账号顺序
def swap_player_bind_id(ctx: SekaiHandlerContext, qid: str, index1: int, index2: int) -> str:
    all_bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {})
    qid = str(qid)
    region = ctx.region
    region_name = get_region_name(region)

    if region not in all_bind_list:
        all_bind_list[region] = {}

    uids = to_list(all_bind_list[region].get(qid, []))
    assert_and_reply(uids, f"你还没有绑定任何{region_name}账号")
    assert_and_reply(index1 < 1e9, f"需要指定账号序号（按绑定时间顺序）而不是账号ID")
    assert_and_reply(index2 < 1e9, f"需要指定账号序号（按绑定时间顺序）而不是账号ID")
    assert_and_reply(0 <= index1 < len(uids), f"指定的账号序号1大于已绑定的{region_name}账号数量({len(uids)})")
    assert_and_reply(0 <= index2 < len(uids), f"指定的账号序号2大于已绑定的{region_name}账号数量({len(uids)})")

    uids[index1], uids[index2] = uids[index2], uids[index1]
    all_bind_list[region][qid] = uids
    profile_db.set("bind_list", all_bind_list)

    return f"""
已将你绑定的{region_name}第{index1 + 1}个账号序号和第{index2 + 1}个账号交换顺序
该指令仅影响索引查询(u{index1 + 1}、u{index2 + 1})，修改默认查询账号请使用"/主账号"
""".strip()


# 验证用户游戏帐号
async def verify_user_game_account(ctx: SekaiHandlerContext, triggered_by_not_verified: bool = False):
    verified_uids = get_user_verified_uids(ctx)
    uid = get_player_bind_id(ctx)
    assert_and_reply(uid not in verified_uids, f"你当前绑定的{get_region_name(ctx.region)}帐号已经验证过")

    def generate_verify_code() -> str:
        while True:
            code = str(random.randint(1000, 9999))
            code = '/'.join(code)
            hit = False
            for codes in _region_qid_verify_codes.values():
                if any(info.verify_code == code for info in codes.values()):
                    hit = True
                    break
            if hit:
                continue
            return code
    
    qid = ctx.user_id
    if ctx.region not in _region_qid_verify_codes:
        _region_qid_verify_codes[ctx.region] = {}

    info = None
    err_msg = ""
    if qid in _region_qid_verify_codes[ctx.region]:
        info = _region_qid_verify_codes[ctx.region][qid]
        if info.expire_time < datetime.now():
            err_msg = f"你的上次验证已过期\n"
        if info.uid != uid:
            err_msg = f"开始验证时绑定的帐号与当前绑定帐号不一致\n"

    if triggered_by_not_verified:
        err_msg = f"该功能需要验证你的游戏账号\n"

    if err_msg:
        _region_qid_verify_codes[ctx.region].pop(qid, None)
        info = None
    
    # 首次验证
    if not info:
        info = VerifyCode(
            region=ctx.region,
            qid=qid,
            uid=uid,
            verify_code=generate_verify_code(),
            expire_time=datetime.now() + VERIFY_CODE_EXPIRE_TIME,
        )
        _region_qid_verify_codes[ctx.region][qid] = info
        raise ReplyException(f"""
{err_msg}请在你当前绑定的{get_region_name(ctx.region)}帐号的名片简介末尾输入验证码(不要去掉斜杠):
{info.verify_code}
编辑后退出名片界面保存，然后在{get_readable_timedelta(VERIFY_CODE_EXPIRE_TIME)}内发送\"/{ctx.region}pjsk验证\"完成验证
""".strip())
    
    profile = await get_basic_profile(ctx, info.uid, use_cache=False, use_remote_cache=False)
    word: str = profile['userProfile'].get('word', '').strip()

    assert_and_reply(word.endswith(info.verify_code), f"""
验证失败，从你绑定的{get_region_name(ctx.region)}帐号留言末尾没有获取到验证码\"{info.verify_code}\"，请重试（验证码未改变）
""".strip())

    try:
        # 验证成功
        verify_accounts = profile_db.get(f"verify_accounts_{ctx.region}", {})
        verify_accounts.setdefault(str(qid), []).append(info.uid)
        profile_db.set(f"verify_accounts_{ctx.region}", verify_accounts)
        raise ReplyException(f"验证成功！使用\"/{ctx.region}pjsk验证列表\"可以查看你验证过的游戏ID")
    finally:
        _region_qid_verify_codes[ctx.region].pop(qid, None)

# 获取用户验证过的游戏ID列表
def get_user_verified_uids(ctx: SekaiHandlerContext) -> List[str]:
    return profile_db.get_copy(f"verify_accounts_{ctx.region}", {}).get(str(ctx.user_id), [])

# 获取游戏id并检查用户是否验证过当前的游戏id，失败抛出异常
async def get_uid_and_check_verified(ctx: SekaiHandlerContext, force: bool = False) -> str:
    uid = get_player_bind_id(ctx)
    if not force:
        verified_uids = get_user_verified_uids(ctx)
        if uid not in verified_uids:
            await verify_user_game_account(ctx, triggered_by_not_verified=True)
            # 正常情况下不会往下走
            assert_and_reply(uid in verified_uids, f"""
该功能需要验证你的游戏帐号
请使用"/{ctx.region}pjsk验证"进行验证，使用"/{ctx.region}pjsk验证列表"查看你验证过的游戏ID
""".strip())
    return uid


# 检测游戏id是否在黑名单中
def check_uid_in_blacklist(uid: str) -> bool:
    blacklist = profile_db.get("blacklist", [])
    return uid in blacklist


# ======================= 处理逻辑 ======================= #

# 处理敏感指令抓包数据来源
def process_sensitive_cmd_source(data):
    if data.get('source') == 'haruki':
        data['source'] = 'remote'
    if data.get('local_source') == 'haruki':
        data['local_source'] = 'sync'

# 根据游戏id获取玩家基本信息
async def get_basic_profile(ctx: SekaiHandlerContext, uid: int, use_cache=True, use_remote_cache=True, raise_when_no_found=True) -> dict:
    cache_path = f"{SEKAI_PROFILE_DIR}/profile_cache/{ctx.region}/{uid}.json"
    try:
        region_name = get_region_name(ctx.region)
        url = get_gameapi_config(ctx).profile_api_url
        assert_and_reply(url, f"暂不支持查询{region_name}的玩家信息")
        profile = await request_gameapi(url.format(uid=uid) + f"?use_cache={use_remote_cache}")
        if raise_when_no_found:
            assert_and_reply(profile, f"找不到ID为 {uid} 的{region_name}玩家")
        elif not profile:
            return {}
        dump_json(profile, cache_path)
        return profile
    except Exception as e:
        if use_cache and os.path.exists(cache_path):
            logger.print_exc(f"获取 {ctx.region} {uid} 基本信息失败，使用缓存数据")
            profile = load_json(cache_path)
            return profile
        raise e

# 根据游戏id获取玩家liveRecords
async def get_user_live_records(
    ctx: SekaiHandlerContext,
    qid: int | None = None,
    uid: int | str | None = None,
    limit: int = 5000,
    include_partial: bool = False,
    raise_when_empty: bool = True,
) -> list[dict]:
    if uid is None:
        uid = get_player_bind_id(ctx, qid)
    uid = str(uid)

    region_name = get_region_name(ctx.region)
    api_url = get_gameapi_config(ctx).live_records_api_url
    assert_and_reply(
        api_url,
        f"当前{region_name}未配置liveRecords接口，使用 /{ctx.region}pjsk b30 old 查询旧版B30",
    )

    try:
        limit = max(1, int(limit))
    except Exception:
        limit = 5000

    req_url = api_url.format(uid=uid)
    req_url += ('&' if '?' in req_url else '?') + f"limit={limit}&include_partial={'true' if include_partial else 'false'}"

    data = await request_gameapi(req_url)
    records = data.get('records', []) if isinstance(data, dict) else []
    assert_and_reply(isinstance(records, list), "liveRecords接口返回格式错误")
    if raise_when_empty:
        assert_and_reply(records, f"{region_name}的liveRecords暂无可用数据")
    return records

# 获取玩家基本信息的简单卡片控件，返回Frame
async def get_basic_profile_card(ctx: SekaiHandlerContext, profile: dict, update_time_ms: int | None = None) -> Frame:
    with Frame().set_bg(roundrect_bg()).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            avatar_info = await get_player_avatar_info_by_basic_profile(ctx, profile)

            frames = get_player_frames(ctx, profile['user']['userId'], None)
            await get_avatar_widget_with_frame(ctx, avatar_info.img, 80, frames)

            with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                game_data = profile['user']
                user_id = process_hide_uid(ctx, game_data['userId'], keep=6)
                colored_text_box(
                    truncate(game_data['name'], 64),
                    TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2, shadow_color=ADAPTIVE_SHADOW),
                )
                TextBox(f"{ctx.region.upper()}: {user_id}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                raw_update_time = update_time_ms if update_time_ms is not None else profile.get('update_time')
                if raw_update_time is not None:
                    try:
                        update_time = datetime.fromtimestamp(int(raw_update_time) / 1000)
                        update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    except Exception:
                        update_time_text = "?"
                else:
                    update_time_text = "?"
                TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
    return f

# 从玩家基本信息获取该玩家头像PlayerAvatarInfo
async def get_player_avatar_info_by_basic_profile(ctx: SekaiHandlerContext, basic_profile: dict) -> PlayerAvatarInfo:
    decks = basic_profile['userDeck']
    pcards = [find_by(basic_profile['userCards'], 'cardId', decks[f'member{i}']) for i in range(1, 6)]
    for pcard in pcards:
        pcard['after_training'] = pcard['defaultImage'] == "special_training" and pcard['specialTrainingStatus'] == "done"
    card_id = pcards[0]['cardId']
    avatar_img = await get_card_thumbnail(ctx, card_id, pcards[0]['after_training'], high_res=True)
    cid = (await ctx.md.cards.find_by_id(card_id))['characterId']
    unit = await get_unit_by_card_id(ctx, card_id)
    return PlayerAvatarInfo(card_id, cid, unit, avatar_img)

# 查询抓包数据获取模式
def get_user_data_mode(ctx: SekaiHandlerContext, qid: int) -> str:
    if ctx.data_mode_arg:
        assert_and_reply(ctx.data_mode_arg in VALID_DATA_MODES, f"错误的抓包数据获取模式: {ctx.data_mode_arg}")
        return ctx.data_mode_arg
    data_modes = profile_db.get("data_modes", {})
    return data_modes.get(ctx.region, {}).get(str(qid), DEFAULT_DATA_MODE)

# 用户是否隐藏抓包信息
def is_user_hide_suite(ctx: SekaiHandlerContext, qid: int) -> bool:
    hide_list = profile_db.get("hide_suite_list", {}).get(ctx.region, [])
    return qid in hide_list

# 用户是否隐藏id
def is_user_hide_id(region: str, qid: int) -> bool:
    hide_list = profile_db.get("hide_id_list", {}).get(region, [])
    return qid in hide_list

# 如果ctx的用户隐藏id则返回隐藏的uid，否则原样返回
def process_hide_uid(ctx: SekaiHandlerContext, uid: int, keep: int=0) -> str:
    if is_user_hide_id(ctx.region, ctx.user_id):
        if keep:
            return "*" * (16 - keep) + str(uid)[-keep:]
        return "*" * 16
    return uid

# 根据获取玩家详细信息，返回(profile, err_msg)
async def get_detailed_profile(
    ctx: SekaiHandlerContext, 
    qid: int, 
    raise_exc=False, 
    mode=None, 
    ignore_hide=False, 
    filter: list[str] | set[str] | None=None,
    strict: bool=True,
) -> Tuple[Suite | None, str]:
    cache_path = None
    uid = None
    try:
        # 获取绑定的游戏id
        try:
            uid = get_player_bind_id(ctx)
        except Exception as e:
            logger.info(f"获取 {qid} {ctx.region}抓包数据失败: 未绑定游戏账号")
            raise e
        
        # 检测是否隐藏抓包信息
        if not ignore_hide and is_user_hide_suite(ctx, qid):
            logger.info(f"获取 {qid} {ctx.region} {uid} 抓包数据失败: 用户已隐藏抓包信息")
            raise ReplyException(f"你已隐藏抓包信息，发送\"/{ctx.region}展示抓包\"可重新展示")
        
        # 服务器不支持
        url = get_gameapi_config(ctx).suite_api_url
        if not url:
            raise ReplyException(f"暂不支持查询{get_region_name(ctx.region)}的抓包数据")
        
        # 数据获取模式
        mode = mode or get_user_data_mode(ctx, qid)

        # 尝试下载
        try:   
            url = url.format(uid=uid) + f"?mode={mode}"
            if filter:
                req_filter = expand_suite_filter_keys(ctx.region, mode, filter)
                url += f"&filter={','.join(req_filter)}"
            raw_profile = await request_gameapi(url)
            if source_err := get_suite_source_error(mode, raw_profile):
                raise ReplyException(source_err)
            profile = Suite.from_region(ctx.region, raw_profile)
        except HttpError as e:
            logger.info(f"获取 {qid} {ctx.region} {uid} 抓包数据失败: {get_exc_desc(e)}")
            if e.status_code == 404:
                local_err = e.message.get('local_err', None)
                haruki_err = e.message.get('haruki_err', None)
                if mode == "local" and (local_err is not None or haruki_err is not None):
                    detail = local_err if local_err is not None else haruki_err
                    raise ReplyException(f"[本地数据] {detail}")
                if mode == "haruki" and (haruki_err is not None or local_err is not None):
                    detail = haruki_err if haruki_err is not None else local_err
                    raise ReplyException(f"[Haruki工具箱] {detail}")
                msg = f"获取你的{get_region_name(ctx.region)}Suite抓包数据失败，发送\"/抓包\"指令可获取帮助\n"
                if local_err is not None: msg += f"[本地数据] {local_err}\n"
                if haruki_err is not None: msg += f"[Haruki工具箱] {haruki_err}\n"
                raise ReplyException(msg.strip())
            else:
                raise e
        except Exception as e:
            logger.info(f"获取 {qid} {ctx.region} {uid} 抓包数据失败: {get_exc_desc(e)}")
            raise e
            
        if not profile:
            logger.info(f"获取 {qid} {ctx.region} {uid} 抓包数据失败: 找不到该玩家")
            raise ReplyException(f"找不到ID为 {uid} 的玩家")
        
        # 缓存数据（目前已不缓存）
        cache_path = f"{SEKAI_PROFILE_DIR}/suite_cache/{ctx.region}/{uid}.json"
        # if not upload_time_only:
        #     dump_json(profile, cache_path)
        logger.info(f"获取 {qid} {ctx.region} {uid} 抓包数据成功，数据已缓存")
        
    except Exception as e:
        # 获取失败的情况，尝试读取缓存
        if cache_path and os.path.exists(cache_path):
            profile = Suite.from_region(ctx.region, load_json(cache_path))
            logger.info(f"从缓存获取 {qid} {ctx.region} {uid} 抓包数据")
            return profile, get_exc_desc(e) + "(使用先前的缓存数据)"
        else:
            logger.info(f"未找到 {qid} {ctx.region} {uid} 的缓存抓包数据")

        if raise_exc:
            raise e
        else:
            return None, get_exc_desc(e)

    if strict and filter:
        missing_keys = profile.missing_fields(filter)
        if missing_keys:
            source = profile.source or '?'
            update_time = datetime.fromtimestamp(profile.upload_time / 1000).strftime('%m-%d %H:%M:%S')
            err_msg = (f"你的{get_region_name(ctx.region)}Suite抓包数据中缺少必要的字段: {', '.join(missing_keys)}"
                       f" (数据来源: {source} 更新时间: {update_time})")
            if raise_exc:
                raise ReplyException(err_msg)
            return None, err_msg
        
    return profile, ""

# 获取包含了玩家详细信息的简单卡片控件所需要的filter
def get_detailed_profile_card_filter(*s: str) -> set[str]:
    return {'userGamedata', 'userDecks', 'upload_time', 'userCards', *s}

# 从玩家详细信息获取该玩家头像的PlayerAvatarInfo
async def get_player_avatar_info_by_detailed_profile(ctx: SekaiHandlerContext, detail_profile: Suite) -> PlayerAvatarInfo:
    deck_id = detail_profile.userGamedata['deck']
    decks = find_by(detail_profile.userDecks, 'deckId', deck_id)
    pcards = [find_by(detail_profile.userCards, 'cardId', decks[f'member{i}']) for i in range(1, 6)]
    for pcard in pcards:
        pcard['after_training'] = pcard['defaultImage'] == "special_training" and pcard['specialTrainingStatus'] == "done"
    card_id = pcards[0]['cardId']
    avatar_img = await get_card_thumbnail(ctx, card_id, pcards[0]['after_training'], high_res=True)
    cid = (await ctx.md.cards.find_by_id(card_id))['characterId']
    unit = await get_unit_by_card_id(ctx, card_id)
    return PlayerAvatarInfo(card_id, cid, unit, avatar_img)

# 获取玩家详细信息的简单卡片控件，返回Frame
async def get_detailed_profile_card(ctx: SekaiHandlerContext, profile: Suite | None, err_msg: str, mode=None) -> Frame:
    with Frame().set_bg(roundrect_bg()).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if profile:
                avatar_info = await get_player_avatar_info_by_detailed_profile(ctx, profile)

                frames = get_player_frames(ctx, profile.userGamedata['userId'], profile)
                await get_avatar_widget_with_frame(ctx, avatar_info.img, 80, frames)

                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    game_data = profile.userGamedata
                    source = profile.source or '?'
                    if local_source := profile.local_source:
                        source += f"({local_source})"
                    mode = mode or get_user_data_mode(ctx, ctx.user_id)
                    update_time = datetime.fromtimestamp(profile.upload_time / 1000)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    user_id = process_hide_uid(ctx, game_data['userId'], keep=6)
                    colored_text_box(
                        truncate(game_data['name'], 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(f"{ctx.region.upper()}: {user_id} Suite数据", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"数据来源: {source}  获取模式: {mode}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
            if err_msg:
                TextBox(f"获取数据失败: {err_msg}", TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(300)
    return f

# 获取注册时间，无效uid返回None
def get_register_time(region: str, uid: str) -> datetime:
    try:
        if region in ['jp', 'en']:
            time = int(uid[:-3]) / 1024 / 4096
            return datetime.fromtimestamp(1600218000 + int(time))
        elif region in ['tw', 'cn', 'kr']:
            time = int(uid) / 1024 / 1024 / 4096
            return datetime.fromtimestamp(int(time))
    except ValueError:
        return None

# 合成个人信息图片
async def compose_profile_image(ctx: SekaiHandlerContext, basic_profile: dict, vertical: bool=None) -> Image.Image:
    bg_settings = get_profile_bg_settings(ctx)
    detail_profile, _ = await get_detailed_profile(
        ctx, ctx.user_id, raise_exc=False, ignore_hide=True, 
        filter=['upload_time', 'userPlayerFrames'],
        strict=False,
    )
    uid = str(basic_profile['user']['userId'])

    decks = basic_profile['userDeck']
    pcards = [find_by(basic_profile['userCards'], 'cardId', decks[f'member{i}']) for i in range(1, 6)]
    for pcard in pcards:
        pcard['after_training'] = pcard['defaultImage'] == "special_training" and pcard['specialTrainingStatus'] == "done"
    avatar_info = await get_player_avatar_info_by_basic_profile(ctx, basic_profile)

    bg = ImageBg(bg_settings.image, blur=False, fade=0) if bg_settings.image else random_unit_bg(avatar_info.unit)
    ui_bg = roundrect_bg(fill=(255, 255, 255, bg_settings.alpha), blurglass=True, blurglass_kwargs={'blur': bg_settings.blur})

    async def draw_honor():
        with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding((16, 0)):
            honors = basic_profile["userProfileHonors"]
            async def compose_honor_image_nothrow(*args):
                try: return await compose_full_honor_image(*args)
                except: 
                    logger.print_exc("合成头衔图片失败")
                    return None
            honor_imgs = await asyncio.gather(*[
                compose_honor_image_nothrow(ctx, find_by(honors, 'seq', 1), True, basic_profile),
                compose_honor_image_nothrow(ctx, find_by(honors, 'seq', 2), False, basic_profile),
                compose_honor_image_nothrow(ctx, find_by(honors, 'seq', 3), False, basic_profile)
            ])
            for img in honor_imgs:
                if img: 
                    ImageBox(img, size=(None, 48), shadow=True)

    async def draw_deck(vertical: bool):
        with HSplit().set_content_align('c').set_item_align('c').set_sep(6 if not vertical else 16).set_padding((16, 0)):
            card_ids = [pcard['cardId'] for pcard in pcards]
            cards = await ctx.md.cards.collect_by_ids(card_ids)
            card_imgs = [
                await get_card_full_thumbnail(ctx, card, pcard=pcard, high_res=True)
                for card, pcard in zip(cards, pcards)
            ]
            for i in range(len(card_imgs)):
                ImageBox(card_imgs[i], size=(90, 90), image_size_mode='fill', shadow=True)

    # 个人信息部分
    async def draw_info(vertical: bool): 
        with VSplit().set_bg(ui_bg).set_content_align('c').set_item_align('c').set_sep(32).set_padding((32, 35)) as ret:
            # 名片
            with HSplit().set_content_align('c').set_item_align('c').set_sep(32).set_padding((32, 0)):
                frames = get_player_frames(ctx, uid, detail_profile)
                await get_avatar_widget_with_frame(ctx, avatar_info.img, 128, frames)

                with VSplit().set_content_align('c').set_item_align('l').set_sep(16):
                    game_data = basic_profile['user']
                    colored_text_box(
                        truncate(game_data['name'], 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=ADAPTIVE_WB, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(f"{ctx.region.upper()}: {process_hide_uid(ctx, game_data['userId'], keep=6)}", TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB))
                    with Frame():
                        ImageBox(ctx.static_imgs.get("lv_rank_bg.png"), size=(180, None))
                        TextBox(f"{game_data['rank']}", TextStyle(font=DEFAULT_FONT, size=30, color=WHITE)).set_offset((110, 0))\
                        
            # 头衔（竖版）
            if vertical:
                await draw_honor()

            # 推特
            with Frame().set_content_align('l').set_w(450):
                tw_id = basic_profile['userProfile'].get('twitterId', '')
                tw_id_box = TextBox('        @ ' + tw_id, TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB), line_count=1)
                tw_id_box.set_wrap(False).set_bg(ui_bg).set_line_sep(2).set_padding(10).set_w(300).set_content_align('l')
                x_icon = ctx.static_imgs.get("x_icon.svg").resize((24, 24)).convert('RGBA')
                ImageBox(x_icon, image_size_mode='original').set_offset((16, 0))

            # 留言
            user_word = basic_profile['userProfile'].get('word', '')
            user_word = re.sub(r'<#.*?>', '', user_word)
            user_word_box = TextBox(user_word, TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB), line_count=3)
            user_word_box.set_wrap(True).set_bg(ui_bg).set_line_sep(2).set_padding((18, 16)).set_w(450)

            # 头衔（横版）
            if not vertical:
                await draw_honor()
            
            # 卡组（横版）
            if not vertical:
                await draw_deck(vertical)
            
        return ret

    # 打歌部分
    async def draw_play(vertical: bool): 
        with HSplit().set_content_align('c').set_item_align('t').set_sep(12).set_bg(ui_bg).set_padding(32) as ret:
            hs, vs, gw, gh = 8, 12, 90, 25
            with VSplit().set_sep(vs):
                Spacer(gh, gh)
                ImageBox(ctx.static_imgs.get(f"icon_clear.png"), size=(gh, gh))
                ImageBox(ctx.static_imgs.get(f"icon_fc.png"), size=(gh, gh))
                ImageBox(ctx.static_imgs.get(f"icon_ap.png"), size=(gh, gh))
            with Grid(col_count=6).set_sep(hsep=hs, vsep=vs):
                for diff, color in DIFF_COLORS.items():
                    t = TextBox(diff.upper(), TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE))
                    t.set_bg(RoundRectBg(fill=color, radius=3)).set_size((gw, gh)).set_content_align('c')
                diff_count = basic_profile['userMusicDifficultyClearCount']
                scores = ['liveClear', 'fullCombo', 'allPerfect']
                play_result = ['clear', 'fc', 'ap']
                for i, score in enumerate(scores):
                    for j, diff in enumerate(DIFF_COLORS.keys()):
                        bg_color = (255, 255, 255, 150) if j % 2 == 0 else (255, 255, 255, 100)
                        count = find_by(diff_count, 'musicDifficultyType', diff)[score]
                        TextBox(str(count), TextStyle(
                                DEFAULT_FONT, 20, PLAY_RESULT_COLORS['not_clear'], use_shadow=True,
                                shadow_color=PLAY_RESULT_COLORS[play_result[i]], shadow_offset=1,
                            )).set_bg(RoundRectBg(fill=bg_color, radius=3)).set_size((gw, gh)).set_content_align('c')
        return ret
    
    # 养成部分
    async def draw_chara(vertical: bool):
        with VSplit().set_sep(16).set_item_bg(ui_bg) as ret:
            with Frame().set_content_align('rb'):
                hs, vs, gw, gh = 8, 7, 96, 48

                # 左侧：角色等级
                with Grid(col_count=6).set_sep(hsep=hs, vsep=vs).set_padding(32):
                    chara_list = [
                        "miku", "rin", "len", "luka", "meiko", "kaito", 
                        "ick", "saki", "hnm", "shiho", None, None,
                        "mnr", "hrk", "airi", "szk", None, None,
                        "khn", "an", "akt", "toya", None, None,
                        "tks", "emu", "nene", "rui", None, None,
                        "knd", "mfy", "ena", "mzk", None, None,
                    ]
                    for chara in chara_list:
                        if chara is None:
                            Spacer(gw, gh)
                            continue
                        cid = int(get_cid_by_nickname(chara))
                        chara_data = find_by(basic_profile['userCharacters'], 'characterId', cid)
                        rank = chara_data['characterRank'] if chara_data else 1

                        with Frame().set_size((gw, gh)):
                            chara_img = ctx.static_imgs.get(f'chara_rank_icon/{chara}.png')
                            ImageBox(chara_img, size=(gw, gh), use_alphablend=True)
                            t = TextBox(str(rank), TextStyle(font=DEFAULT_FONT, size=20, color=(40, 40, 40, 255)))
                            t.set_size((60, 48)).set_content_align('c').set_offset((36, 4))

                # 右侧：Challenge Live + Multi Live
                with VSplit().set_content_align('c').set_item_align('c').set_padding((50, 36)).set_sep(9):
                    common_style = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50, 255))
                    box_bg = roundrect_bg(radius=6)
                    box_padding = (10, 7)

                    if 'userChallengeLiveSoloResult' in basic_profile:
                        solo_live_result = basic_profile['userChallengeLiveSoloResult']
                        if isinstance(solo_live_result, list):
                            solo_live_result = sorted(solo_live_result, key=lambda x: x['highScore'], reverse=True)[0]
                        cid, score = solo_live_result['characterId'], solo_live_result['highScore']
                        stages = find_by(basic_profile['userChallengeLiveSoloStages'], 'characterId', cid, mode='all')
                        stage_rank = max([stage['rank'] for stage in stages]) if stages else 0

                        TextBox("CHALLENGE LIVE", common_style).set_bg(box_bg).set_padding(box_padding)

                        with Frame():
                            chara_nickname = get_character_first_nickname(cid) if 'get_character_first_nickname' in globals() else str(cid)
                            chara_img = ctx.static_imgs.get(f'chara_rank_icon/{chara_nickname}.png')
                            ImageBox(chara_img, size=(100, 50), use_alphablend=True)

                            t = TextBox(str(stage_rank), TextStyle(font=DEFAULT_FONT, size=22, color=(40, 40, 40, 255)), overflow='clip')
                            t.set_size((50, 50)).set_content_align('c').set_offset((40, 5))

                        TextBox(f"SCORE  {score}", common_style).set_bg(box_bg).set_padding(box_padding)

                    if 'userMultiLiveTopScoreCount' in basic_profile:
                        multi_stats = basic_profile['userMultiLiveTopScoreCount']
                        mvp_count = multi_stats.get('mvp', 0)
                        ss_count = multi_stats.get('superStar', 0)

                        TextBox("MULTI LIVE", common_style).set_bg(box_bg).set_padding(box_padding)

                        TextBox(f"MVP  {mvp_count}次", common_style).set_bg(box_bg).set_padding(box_padding)

                        TextBox(f"SUPERSTAR  {ss_count}次", common_style).set_bg(box_bg).set_padding(box_padding)

            # 卡组（竖版）
            if vertical:
                with Frame().set_content_align('c').set_padding(32):
                    await draw_deck(vertical)
        return ret

    if vertical is None:
        vertical = bg_settings.vertical

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        if not vertical:
            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                await draw_info(vertical)
                with VSplit().set_content_align('c').set_item_align('c').set_sep(16):
                    await draw_play(vertical)
                    await draw_chara(vertical)
        else:
            with VSplit().set_content_align('c').set_item_align('c').set_sep(16).set_item_bg(ui_bg):
                (await draw_info(vertical)).set_bg(None)
                (await draw_play(vertical)).set_bg(None)
                (await draw_chara(vertical)).set_bg(None).set_omit_parent_bg(True)

    if 'update_time' in basic_profile:
        update_time = datetime.fromtimestamp(basic_profile['update_time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
    else:
        update_time = "?"
    text = f"DT: {update_time}  " + DEFAULT_WATERMARK_CFG.get()
    if bg_settings.image:
        text = text + f"  This background is user-uploaded."
    add_watermark(canvas, text)
    return await canvas.get_img(1.5)

# 个人信息背景设置
async def set_profile_bg_settings(
    ctx: SekaiHandlerContext,
    image: Optional[Image.Image] = None,
    remove_image: bool = False,
    blur: Optional[int] = None,
    alpha: Optional[int] = None,
    vertical: Optional[bool] = None,
    force: bool = False
):
    uid = await get_uid_and_check_verified(ctx, force)
    region = ctx.region
    image_path = PROFILE_BG_IMAGE_PATH.format(region=region, uid=uid)

    settings: Dict[str, Dict[str, Any]] = profile_bg_settings_db.get(region, {})
    
    if remove_image:
        if os.path.exists(image_path):
            os.remove(image_path)
    elif image:
        w, h = image.size
        w1, h1 = config.get('profile.bg_image_size.horizontal')
        w2, h2 = config.get('profile.bg_image_size.vertical')
        scale = -1
        if w > w1 and h > h1:
            scale = max(scale, w1 / w, h1 / h)
        if w > w2 and h > h2:
            scale = max(scale, w2 / w, h2 / h)
        if scale < 0:
            scale = 1
        target_w, target_h = int(w * scale), int(h * scale)
        assert_and_reply(min(target_w, target_h) < 10000, "上传图片的横纵比过大或过小")
        image = image.convert('RGB')
        if image.width > target_w:
            image = image.resize((target_w, target_h), Image.LANCZOS)
        save_kwargs = config.get('profile.bg_image_save_kwargs', {})
        create_parent_folder(image_path)
        image.save(image_path, **save_kwargs)
        settings.setdefault(uid, {})['vertical'] = target_w < target_h
        if 'blur' not in settings.get(uid, {}):
            settings.setdefault(uid, {})['blur'] = 1
        if 'alpha' not in settings.get(uid, {}):
            settings.setdefault(uid, {})['alpha'] = 50

    if blur is not None:
        blur = max(0, min(10, blur))
        settings.setdefault(uid, {})['blur'] = blur

    if alpha is not None:
        alpha = max(0, min(255, alpha))
        settings.setdefault(uid, {})['alpha'] = alpha

    if vertical is not None:
        settings.setdefault(uid, {})['vertical'] = vertical

    profile_bg_settings_db.set(region, settings)

# 个人信息背景设置获取
def get_profile_bg_settings(ctx: SekaiHandlerContext) -> ProfileBgSettings:
    uid = get_player_bind_id(ctx)
    region = ctx.region
    try:
        image = open_image(PROFILE_BG_IMAGE_PATH.format(region=region, uid=uid))
    except:
        image = None
    settings = profile_bg_settings_db.get(region, {}).get(uid, {})
    ret = ProfileBgSettings(image=image, **settings)
    if ret.alpha is None:
        ret.alpha = WIDGET_BG_COLOR_CFG.get()[3]
    if ret.blur is None:
        ret.blur = 4
    return ret

# 获取玩家框信息，提供detail_profile会直接取用并更新缓存，否则使用缓存数据
def get_player_frames(ctx: SekaiHandlerContext, uid: str, detail_profile: Optional[Suite] = None) -> List[dict]:
    uid = str(uid)
    all_cached_frames = player_frame_db.get(ctx.region, {})
    cached_frames = all_cached_frames.get(uid, {})
    if detail_profile:
        upload_time = detail_profile.upload_time
        frames = detail_profile.userPlayerFrames
        if upload_time > cached_frames.get('upload_time', 0):
            # 更新缓存
            cached_frames = {
                'upload_time': upload_time,
                'frames': frames
            }
            if frames:
                all_cached_frames[uid] = cached_frames
                player_frame_db.set(ctx.region, all_cached_frames)
    return cached_frames.get('frames', [])

# 获取头像框图片，失败返回None
async def get_player_frame_image(ctx: SekaiHandlerContext, frame_id: int, frame_w: int) -> Image.Image | None:
    try:
        frame = await ctx.md.player_frames.find_by_id(frame_id)
        frame_group = await ctx.md.player_frame_groups.find_by_id(frame['playerFrameGroupId'])
        asset_name = frame_group['assetbundleName']
        asset_path = f"player_frame/{asset_name}/{frame_id}/"

        cache_path = f"{SEKAI_ASSET_DIR}/player_frames/{ctx.region}/{asset_name}_{frame_id}.png"

        scale = 1.5
        corner = 20
        corner2 = 50
        w = 700
        border = 100
        border2 = 80
        inner_w = w - 2*border

        if os.path.exists(cache_path):
            img = open_image(cache_path)
        else:
            base = await ctx.rip.img(asset_path + "horizontal/frame_base.png", allow_error=False)
            ct = await ctx.rip.img(asset_path + "vertical/frame_centertop.png", allow_error=False)
            lb = await ctx.rip.img(asset_path + "vertical/frame_leftbottom.png", allow_error=False)
            lt = await ctx.rip.img(asset_path + "vertical/frame_lefttop.png", allow_error=False)
            rb = await ctx.rip.img(asset_path + "vertical/frame_rightbottom.png", allow_error=False)
            rt = await ctx.rip.img(asset_path + "vertical/frame_righttop.png", allow_error=False)

            try:
                ct = (await run_in_pool(shrink_image, ct, 10, 0)).image
            except Exception as e:
                logger.warning(f"合成playerFrame_{frame_id}时为ct执行shrink失败（可能导致错位）: {get_exc_desc(e)}")
            
            ct = resize_keep_ratio(ct, scale, mode='scale')
            lt = resize_keep_ratio(lt, scale, mode='scale')
            lb = resize_keep_ratio(lb, scale, mode='scale')
            rt = resize_keep_ratio(rt, scale, mode='scale')
            rb = resize_keep_ratio(rb, scale, mode='scale')

            bw = base.width
            base_lt = base.crop((0, 0, corner, corner))
            base_rt = base.crop((bw-corner, 0, bw, corner))
            base_lb = base.crop((0, bw-corner, corner, bw))
            base_rb = base.crop((bw-corner, bw-corner, bw, bw))
            base_l = base.crop((0, corner, corner, bw-corner))
            base_r = base.crop((bw-corner, corner, bw, bw-corner))
            base_t = base.crop((corner, 0, bw-corner, corner))
            base_b = base.crop((corner, bw-corner, bw-corner, bw))

            p = Painter(size=(w, w))

            p.move_region((border, border), (inner_w, inner_w))
            p.paste(base_lt, (0, 0), (corner2, corner2))
            p.paste(base_rt, (inner_w-corner2, 0), (corner2, corner2))
            p.paste(base_lb, (0, inner_w-corner2), (corner2, corner2))
            p.paste(base_rb, (inner_w-corner2, inner_w-corner2), (corner2, corner2))
            p.paste(base_l.resize((corner2, inner_w-2*corner2)), (0, corner2))
            p.paste(base_r.resize((corner2, inner_w-2*corner2)), (inner_w-corner2, corner2))
            p.paste(base_t.resize((inner_w-2*corner2, corner2)), (corner2, 0))
            p.paste(base_b.resize((inner_w-2*corner2, corner2)), (corner2, inner_w-corner2))
            p.restore_region()

            p.paste(lb, (border2, w-border2-lb.height))
            p.paste(rb, (w-border2-rb.width, w-border2-rb.height))
            p.paste(lt, (border2, border2))
            p.paste(rt, (w-border2-rt.width, border2))
            p.paste(ct, ((w-ct.width)//2, border2-ct.height//2))

            img = await p.get()
            create_parent_folder(cache_path)
            img.save(cache_path)

        img = resize_keep_ratio(img, frame_w / inner_w, mode='scale')
        return img

    except:
        logger.print_exc(f"获取playerFrame {frame_id} 失败")
        return None
    
# 获取带框头像控件
async def get_avatar_widget_with_frame(ctx: SekaiHandlerContext, avatar_img: Image.Image, avatar_w: int, frame_data: list[dict]) -> Frame:
    frame_img = None
    try:
        if frame := find_by(frame_data, 'playerFrameAttachStatus', "first"):
            frame_img = await get_player_frame_image(ctx, frame['playerFrameId'], avatar_w + 5)
    except:
        pass

    # 期间限定框
    term_limit_frame_img: Image.Image = None
    try:
        for limited_time_frame in config.get('profile.limited_time_custom_frames', []):
            now = datetime.now()
            for period in limited_time_frame.get('periods', []):
                start = datetime.strptime(period[0], '%m-%d %H:%M').replace(year=now.year)
                end = datetime.strptime(period[1], '%m-%d %H:%M').replace(year=now.year)
                if start <= now <= end:
                    term_limit_frame_img = ctx.static_imgs.get(limited_time_frame['path'])
                    term_limit_frame_img = resize_keep_ratio(term_limit_frame_img, avatar_w, scale=limited_time_frame.get('scale', 1.0))
                    break
            if term_limit_frame_img:
                break
    except Exception as e:
        logger.warning(f"获取期间限定头像框失败: {get_exc_desc(e)}")
        term_limit_frame_img = None

    with Frame().set_size((avatar_w, avatar_w)).set_content_align('c').set_allow_draw_outside(True) as ret:
        ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alphablend=False, shadow=True)
        if frame_img:
            ImageBox(frame_img, use_alphablend=True, shadow=True)
        if term_limit_frame_img:
            ImageBox(term_limit_frame_img, use_alphablend=True, shadow=True)
    return ret


# ======================= 角色等级任务总览 ======================= #

CHAR_MISSION_SHORT_NAMES = {
    "play_live": "队长次数",
    "play_live_ex": "队长次数(EX)",
    "waiting_room": "休息室次数",
    "waiting_room_ex": "休息室次数(EX)",
    "collect_costume_3d": "服装",
    "collect_stamp": "表情",
    "read_area_talk": "区域对话",
    "read_card_episode_first": "卡面剧情前篇",
    "read_card_episode_second": "卡面剧情后篇",
    "collect_another_vocal": "Another Vocal",
    "area_item_level_up_character": "单人家具升级次数",
    "area_item_level_up_unit": "团家具升级次数",
    "area_item_level_up_reality_world": "属性道具（树&花）升级次数",
    "collect_member": "卡面",
    "skill_level_up_rare": "技能等级升级次数（★4&生日卡）",
    "skill_level_up_standard": "技能等级升级次数（★1~★3）",
    "master_rank_up_rare": "专精等级升级次数（★4&生日卡）",
    "master_rank_up_standard": "专精等级升级次数（★1~★3）",
    "collect_character_archive_voice": "台词",
    "collect_mysekai_fixture": "MySekai家具数量",
    "collect_mysekai_canvas": "MySekai画布数量",
    "read_mysekai_fixture_unique_character_talk": "MySekai对话",
}

CHAR_MISSION_EX_TYPES = {"play_live_ex", "waiting_room_ex"}
CHAR_MISSION_EX_BASE_TYPES = {"play_live", "waiting_room"}


def _char_mission_short_name(mission_type: str) -> str:
    return CHAR_MISSION_SHORT_NAMES.get(mission_type, mission_type)


def _get_pg_requirement_by_seq(
    pg_seq_requirements: dict[int, list[tuple[int, int]]],
    parameter_group_id: int,
    seq: int,
) -> int:
    if seq <= 0:
        return 0
    req = 0
    for item_seq, item_req in pg_seq_requirements.get(parameter_group_id, []):
        if item_seq > seq:
            break
        req = item_req
    return req


def _calc_mission_percent(current: int, upper: int | None) -> str:
    if upper is None or upper <= 0:
        return "-"
    return f"{min(current / upper * 100, 100.0):.1f}%"


def _draw_single_progress(
    line_title: str,
    current: int,
    upper: int | None,
    ratio: float,
    bar_width: int,
    bar_color: Color,
    title_size: int = 16,
    title_align: str = "l",
    title_badge: str | None = None,
    next_need: int | None = None,
    next_exp: int | None = None,
):
    style_title = TextStyle(font=DEFAULT_BOLD_FONT, size=title_size, color=(35, 35, 35, 255))
    style_text = TextStyle(font=DEFAULT_FONT, size=15, color=(55, 55, 55, 255))

    if line_title:
        # 标题使用固定宽度容器，确保“相对于各自文本框”居中
        if title_badge:
            with Frame().set_w(bar_width).set_content_align(title_align):
                with HSplit().set_content_align(title_align).set_item_align('c').set_sep(8):
                    TextBox(line_title, style_title)
                    TextBox(title_badge, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(55, 55, 55, 255))) \
                        .set_bg(roundrect_bg(fill=(255, 255, 255, 180), radius=8)).set_padding((8, 2))
        else:
            TextBox(line_title, style_title).set_w(bar_width).set_content_align(title_align)

        # 让进度条整体离标题更远一点
        Spacer(w=bar_width, h=4)

    # 与“/队长次数”同色阶，但按比例分档（适用于所有任务）
    raw_ratio = ratio
    if upper is not None and upper > 0:
        raw_ratio = current / upper
    final_bar_color = (255, 50, 50, 255)
    if raw_ratio >= 1.0:
        final_bar_color = (100, 255, 100, 255)
    elif raw_ratio > 0.8:
        final_bar_color = (255, 255, 100, 255)
    elif raw_ratio > 0.6:
        final_bar_color = (255, 200, 100, 255)
    elif raw_ratio > 0.4:
        final_bar_color = (255, 150, 100, 255)
    elif raw_ratio > 0.2:
        final_bar_color = (255, 100, 100, 255)

    # 复用“/队长次数”进度条风格
    with Frame().set_w(bar_width).set_h(18).set_content_align('lt'):
        progress = max(0.0, min(ratio, 1.0))
        total_w, total_h, border = bar_width, 14, 2
        progress_w = int((total_w - border * 2) * progress)
        progress_h = total_h - border * 2

        if progress > 0:
            Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 255), radius=total_h // 2))
            Spacer(w=progress_w, h=progress_h).set_bg(
                RoundRectBg(fill=final_bar_color, radius=(total_h - border) // 2)
            ).set_offset((border, border))

            # 刻度线风格与“/队长次数”一致
            for i in range(1, 5):
                lx = int((total_w - border * 2) * (i / 5.0))
                line_color = (100, 100, 100, 255) if i / 5.0 < progress else (150, 150, 150, 255)
                Spacer(w=1, h=total_h // 2 - 1).set_bg(FillBg(line_color)).set_offset((border + lx - 1, total_h // 2))
        else:
            Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 100), radius=total_h // 2))

    upper_text = "∞" if upper is None else f"{upper:,}"
    pct_text = _calc_mission_percent(current, upper)
    with HSplit().set_content_align('c').set_item_align('c').set_sep(8):
        TextBox(f"{current:,}/{upper_text} ({pct_text})", style_text).set_content_align('l')
        if next_need is not None:
            exp_text = "?" if next_exp is None else str(next_exp)
            TextBox(
                f"下一档{current:,}/{next_need:,} EXP+{exp_text}",
                TextStyle(font=DEFAULT_FONT, size=14, color=(80, 80, 80, 255)),
            ).set_content_align('r')
        else:
            TextBox(
                "下一档已满",
                TextStyle(font=DEFAULT_FONT, size=14, color=(80, 80, 80, 255)),
            ).set_content_align('r')


def _build_single_mission_card(
    title: str,
    current: int,
    upper: int | None,
    ratio: float,
    card_w: int,
    bar_color: Color = (82, 165, 255, 255),
    next_need: int | None = None,
    next_exp: int | None = None,
) -> Frame:
    with Frame().set_w(card_w).set_bg(roundrect_bg(fill=(255, 255, 255, 140))).set_padding((12, 10)) as card:
        with VSplit().set_content_align('l').set_item_align('l').set_sep(10):
            _draw_single_progress(
                title,
                current,
                upper,
                ratio,
                bar_width=card_w - 24,
                bar_color=bar_color,
                title_size=20,
                title_align='c',
                next_need=next_need,
                next_exp=next_exp,
            )
    return card


def _build_dual_mission_card(
    title: str,
    normal_current: int,
    normal_upper: int | None,
    normal_ratio: float,
    normal_next_need: int | None,
    normal_next_exp: int | None,
    ex_current: int,
    ex_upper: int | None,
    ex_ratio: float,
    ex_next_need: int | None,
    ex_next_exp: int | None,
    card_w: int,
    ex_round_text: str,
) -> Frame:
    with Frame().set_w(card_w).set_bg(roundrect_bg(fill=(255, 255, 255, 155))).set_padding((12, 10)) as card:
        with VSplit().set_content_align('l').set_item_align('l').set_sep(10):
            with Frame().set_w(card_w - 24).set_content_align('c'):
                with HSplit().set_content_align('c').set_item_align('c').set_sep(8):
                    TextBox(title, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(20, 20, 20, 255))).set_content_align('c')
            _draw_single_progress(
                "普通任务",
                normal_current,
                normal_upper,
                normal_ratio,
                bar_width=card_w - 24,
                bar_color=(84, 170, 255, 255),
                title_align='c',
                next_need=normal_next_need,
                next_exp=normal_next_exp,
            )
            _draw_single_progress(
                "EX任务",
                ex_current,
                ex_upper,
                ex_ratio,
                bar_width=card_w - 24,
                bar_color=(255, 145, 84, 255),
                title_align='c',
                title_badge=ex_round_text,
                next_need=ex_next_need,
                next_exp=ex_next_exp,
            )
    return card


def _get_current_cid_from_profile(profile: dict, cards_by_id: dict[int, dict]) -> int | None:
    try:
        deck_id = profile["userGamedata"]["deck"]
        deck = find_by(profile["userDecks"], "deckId", deck_id)
        if not deck:
            return None
        card_id = deck.get("member1")
        card = cards_by_id.get(card_id)
        if not card:
            return None
        return card.get("characterId")
    except Exception:
        return None


async def compose_character_rank_mission_overview_image(
    ctx: SekaiHandlerContext,
    profile: dict,
    err_msg: str,
    cid: int,
) -> Image.Image:
    async def get_masterdata_with_local_fallback(name: str):
        try:
            return await ctx.md.get(name)
        except Exception as e:
            local_path = pjoin(MASTER_DB_CACHE_DIR, ctx.region, f"{name}.json")
            if os.path.exists(local_path):
                logger.warning(
                    f"获取 MasterData [{ctx.region}.{name}] 失败，回退到本地文件: {get_exc_desc(e)}"
                )
                return load_json(local_path)
            raise e

    master_missions = await get_masterdata_with_local_fallback("characterMissionV2s")
    parameter_groups = await get_masterdata_with_local_fallback("characterMissionV2ParameterGroups")

    pg_seq_requirements: dict[int, list[tuple[int, int]]] = {}
    pg_seq_req_exp: dict[int, list[tuple[int, int, int]]] = {}
    pg_max_requirement: dict[int, int] = {}
    pg_seq_exp: dict[tuple[int, int], int] = {}
    for item in parameter_groups:
        pgid = item["id"]
        pg_seq_requirements.setdefault(pgid, []).append((item["seq"], item["requirement"]))
        pg_seq_req_exp.setdefault(pgid, []).append((item["seq"], item["requirement"], int(item.get("exp", 0))))
        pg_max_requirement[pgid] = max(pg_max_requirement.get(pgid, 0), item["requirement"])
        pg_seq_exp[(pgid, item["seq"])] = int(item.get("exp", 0))
    for items in pg_seq_requirements.values():
        items.sort(key=lambda x: x[0])
    for items in pg_seq_req_exp.values():
        items.sort(key=lambda x: x[0])

    def get_ex_round_requirement(pgid: int, round_no: int) -> int:
        req = 0
        for seq, requirement in pg_seq_requirements.get(pgid, []):
            if seq > round_no:
                break
            req = requirement
        return req

    def get_ex_round_exp(pgid: int, round_no: int) -> int:
        exp = 0
        for seq, _, seq_exp in pg_seq_req_exp.get(pgid, []):
            if seq > round_no:
                break
            exp = seq_exp
        return exp

    def calc_ex_round_and_progress(total: int, pgid: int) -> tuple[int, int, int]:
        # 返回: (当前回目, 当前回目进度, 当前回目需求)
        total = max(0, int(total))
        round_no = 1
        while True:
            req = get_ex_round_requirement(pgid, round_no)
            if req <= 0 or total < req:
                return round_no, total, req
            total -= req
            round_no += 1

    def calc_ex_exp_limit_30_rounds(pgid: int) -> int:
        return sum(get_ex_round_requirement(pgid, i) for i in range(1, 31))

    char_missions = [m for m in master_missions if m.get("characterId") == cid]
    char_missions.sort(key=lambda x: x["id"])
    assert_and_reply(char_missions, f"找不到角色ID={cid}的任务数据")

    chara = await ctx.md.game_characters.find_by_id(cid)
    chara_name = (
        f"{chara.get('firstName', '')}{chara.get('givenName', '')}"
        if chara else (get_character_first_nickname(cid) or str(cid))
    )

    # 兼容两种Suite数据结构:
    # 1) userCharacterMissionV2s: 实时进度(progress)
    # 2) userCharacterMissionV2Statuses: 已领取节点(seq)
    user_v2s = [item for item in (profile.get("userCharacterMissionV2s", []) or []) if item.get("characterId") == cid]
    user_statuses = [item for item in (profile.get("userCharacterMissionV2Statuses", []) or []) if item.get("characterId") == cid]
    user_items = [*user_v2s, *user_statuses]

    # character等级曲线（用于经验换算）
    char_levels = await ctx.md.levels.find_by("levelType", "character", mode="all")
    char_levels = sorted(char_levels, key=lambda x: x["level"])
    char_level_total_exp = {int(x["level"]): int(x["totalExp"]) for x in char_levels}

    # 角色当前等级/经验（来自suite）
    user_char = find_by(profile.get("userCharacters", []), "characterId", cid)
    assert_and_reply(user_char, "你的Suite数据来源没有提供userCharacters数据")
    cur_lv = int(user_char.get("characterRank", 1))
    cur_total_exp = int(user_char.get("totalExp", 0))
    # 部分区服没有exp字段，需要由totalExp反推当前等级内经验
    if user_char.get("exp") is not None:
        cur_exp = int(user_char.get("exp", 0))
    else:
        cur_exp = max(0, cur_total_exp - char_level_total_exp.get(cur_lv, 0))

    # 已达成未领取经验（missionStatus=achieved）
    pending_exp = 0
    for s in user_statuses:
        if s.get("missionStatus") != "achieved":
            continue
        pgid = int(s.get("parameterGroupId", 0))
        seq = int(s.get("seq", 0))
        pending_exp += pg_seq_exp.get((pgid, seq), 0)

    # 依据character等级曲线，计算“领取后”的最终等级与经验
    char_levels = await ctx.md.levels.find_by("levelType", "character", mode="all")
    char_levels = sorted(char_levels, key=lambda x: x["level"])
    final_total_exp = cur_total_exp + pending_exp
    final_lv = 1
    final_lv_total = 0
    for lv_item in char_levels:
        if lv_item["totalExp"] <= final_total_exp:
            final_lv = int(lv_item["level"])
            final_lv_total = int(lv_item["totalExp"])
        else:
            break
    final_exp = final_total_exp - final_lv_total

    user_by_mission: dict[int, dict[str, int]] = {}
    user_by_type_progress: dict[str, int] = {}
    for item in user_items:
        mission_id = item.get("missionId")
        if mission_id is not None:
            cur = user_by_mission.setdefault(int(mission_id), {"progress": 0, "seq": 0})
            if item.get("progress") is not None:
                cur["progress"] = max(cur["progress"], int(item["progress"]))
            if item.get("seq") is not None:
                cur["seq"] = max(cur["seq"], int(item["seq"]))

        mission_type = item.get("characterMissionType")
        progress = item.get("progress")
        if mission_type and progress is not None:
            user_by_type_progress[mission_type] = max(user_by_type_progress.get(mission_type, 0), int(progress))

    # EX任务已领取到的最高seq（用于将“当前回目进度”换算为“累计进度”）
    ex_received_max_seq: dict[int, int] = {}
    for item in user_statuses:
        mission_id = item.get("missionId")
        seq = item.get("seq")
        if mission_id is None or seq is None:
            continue
        ex_received_max_seq[int(mission_id)] = max(ex_received_max_seq.get(int(mission_id), 0), int(seq))

    def get_ex_cleared_total(pgid: int, max_seq: int) -> int:
        if max_seq <= 0:
            return 0
        return sum(get_ex_round_requirement(pgid, i) for i in range(1, max_seq + 1))

    mission_rows = []
    for mission in char_missions:
        mission_id = int(mission["id"])
        mission_type = mission["characterMissionType"]
        pgid = int(mission["parameterGroupId"])
        is_ex = mission_type in CHAR_MISSION_EX_TYPES

        current = 0
        if is_ex:
            # EX在不同来源下可能是：
            # - 累计值（如28528）
            # - 当前回目内值（如28）
            # 结合statuses的已领奖seq统一换算为累计值
            progress_raw = user_by_type_progress.get(mission_type, 0)
            received_seq = ex_received_max_seq.get(mission_id, 0)
            cleared_total = get_ex_cleared_total(pgid, received_seq)
            if progress_raw > 0:
                # 若progress明显小于已清空累计值，判定为“当前回目内值”
                if progress_raw < cleared_total:
                    current = cleared_total + progress_raw
                else:
                    current = progress_raw
            else:
                current = cleared_total
        else:
            # 优先使用 userCharacterMissionV2s 中的实时 progress，避免被已领奖seq退化到档位左端点
            if mission_type in user_by_type_progress:
                current = user_by_type_progress[mission_type]
            elif mission_id in user_by_mission and user_by_mission[mission_id]["progress"] > 0:
                current = user_by_mission[mission_id]["progress"]
            elif mission_id in user_by_mission and user_by_mission[mission_id]["seq"] > 0:
                current = _get_pg_requirement_by_seq(pg_seq_requirements, pgid, user_by_mission[mission_id]["seq"])

        finite_upper = pg_max_requirement.get(pgid, 0)
        upper = None if is_ex else finite_upper
        ratio_upper = finite_upper if finite_upper > 0 else max(current, 1)
        ratio = 0.0 if ratio_upper <= 0 else min(current / ratio_upper, 1.0)
        next_need = None
        next_exp = None
        if is_ex:
            round_no, in_round_progress, round_need = calc_ex_round_and_progress(current, pgid)
            if round_need > 0:
                next_need = current + max(round_need - in_round_progress, 0)
                next_exp = get_ex_round_exp(pgid, round_no)
        else:
            for _, req, seq_exp in pg_seq_req_exp.get(pgid, []):
                if req > current:
                    next_need = req
                    next_exp = seq_exp
                    break

        mission_rows.append({
            "mission_id": mission_id,
            "mission_type": mission_type,
            "title": _char_mission_short_name(mission_type),
            "is_achievement": bool(mission.get("isAchievementMission", False)),
            "is_ex": is_ex,
            "current": current,
            "upper": upper,
            "ratio": ratio,
            "next_need": next_need,
            "next_exp": next_exp,
        })

    by_type = {item["mission_type"]: item for item in mission_rows}

    basic_rows = [item for item in mission_rows if not item["is_achievement"]]
    basic_order = [
        "collect_member",                               # 卡面
        "collect_stamp",                                # 表情
        "collect_costume_3d",                           # 服装
        "collect_character_archive_voice",              # 台词
        "collect_another_vocal",                        # Another Vocal
        "read_mysekai_fixture_unique_character_talk",   # MySekai对话
        "read_area_talk",                               # 区域对话
    ]
    basic_order_idx = {name: i for i, name in enumerate(basic_order)}
    basic_rows.sort(key=lambda x: (basic_order_idx.get(x["mission_type"], 10**9), x["mission_id"]))
    ach_rows = [
        item for item in mission_rows
        if item["is_achievement"]
        and item["mission_type"] not in CHAR_MISSION_EX_TYPES
        and item["mission_type"] not in CHAR_MISSION_EX_BASE_TYPES
    ]

    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    sub_header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(35, 35, 35, 255))
    card_w = 520
    card_sep = 16

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, err_msg)

            with VSplit().set_content_align('l').set_item_align('l').set_sep(8).set_item_bg(roundrect_bg()):
                TextBox(
                    "各任务上限为MasterData中所规定的上限，并不一定是当前已实装资源总数",
                    TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(0, 0, 0)),
                    use_real_line_count=True,
                ).set_padding(12)

            with VSplit().set_bg(roundrect_bg()).set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                with HSplit().set_content_align('c').set_item_align('c').set_sep(12):
                    ImageBox(get_chara_icon_by_chara_id(cid), size=(48, 48))
                    TextBox(
                        f"{chara_name} 当前Lv.{cur_lv} EXP×{cur_exp} + 未领取EXP×{pending_exp} = 总计Lv.{final_lv} EXP×{final_exp}",
                        header_style,
                        use_real_line_count=True,
                    )

            with VSplit().set_bg(roundrect_bg()).set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                TextBox("基本任务", sub_header_style)
                for i in range(0, len(basic_rows), 2):
                    left = basic_rows[i]
                    right = basic_rows[i + 1] if i + 1 < len(basic_rows) else None
                    with HSplit().set_content_align('lt').set_item_align('lt').set_sep(card_sep):
                        _build_single_mission_card(
                            left["title"], left["current"], left["upper"], left["ratio"], card_w,
                            next_need=left.get("next_need"), next_exp=left.get("next_exp"),
                        )
                        if right:
                            _build_single_mission_card(
                                right["title"], right["current"], right["upper"], right["ratio"], card_w,
                                next_need=right.get("next_need"), next_exp=right.get("next_exp"),
                            )
                        else:
                            Spacer(w=card_w, h=1)

            with VSplit().set_bg(roundrect_bg()).set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                TextBox("成就", sub_header_style)

                play_live = by_type.get("play_live", {"current": 0, "upper": 0, "ratio": 0})
                play_live_ex = by_type.get("play_live_ex", {"current": 0, "upper": None, "ratio": 0})
                waiting_room = by_type.get("waiting_room", {"current": 0, "upper": 0, "ratio": 0})
                waiting_room_ex = by_type.get("waiting_room_ex", {"current": 0, "upper": None, "ratio": 0})

                play_live_ex_total = play_live_ex["current"]
                waiting_room_ex_total = waiting_room_ex["current"]
                play_live_ex_limit = calc_ex_exp_limit_30_rounds(101)
                waiting_room_ex_limit = calc_ex_exp_limit_30_rounds(102)
                play_live_ex_ratio = min(play_live_ex_total / max(play_live_ex_limit, 1), 1.0)
                waiting_room_ex_ratio = min(waiting_room_ex_total / max(waiting_room_ex_limit, 1), 1.0)

                play_live_round, _, _ = calc_ex_round_and_progress(play_live_ex_total, 101)
                waiting_room_round, _, _ = calc_ex_round_and_progress(waiting_room_ex_total, 102)

                with HSplit().set_content_align('lt').set_item_align('lt').set_sep(card_sep):
                    _build_dual_mission_card(
                        "队长次数",
                        play_live["current"], play_live["upper"], play_live["ratio"],
                        play_live.get("next_need"), play_live.get("next_exp"),
                        play_live_ex_total, play_live_ex_limit, play_live_ex_ratio,
                        play_live_ex.get("next_need"), play_live_ex.get("next_exp"),
                        card_w,
                        f"EX {play_live_round} 回目",
                    )
                    _build_dual_mission_card(
                        "休息室次数",
                        waiting_room["current"], waiting_room["upper"], waiting_room["ratio"],
                        waiting_room.get("next_need"), waiting_room.get("next_exp"),
                        waiting_room_ex_total, waiting_room_ex_limit, waiting_room_ex_ratio,
                        waiting_room_ex.get("next_need"), waiting_room_ex.get("next_exp"),
                        card_w,
                        f"EX {waiting_room_round} 回目",
                    )

                for i in range(0, len(ach_rows), 2):
                    left = ach_rows[i]
                    right = ach_rows[i + 1] if i + 1 < len(ach_rows) else None
                    with HSplit().set_content_align('lt').set_item_align('lt').set_sep(card_sep):
                        _build_single_mission_card(
                            left["title"], left["current"], left["upper"], left["ratio"], card_w,
                            next_need=left.get("next_need"), next_exp=left.get("next_exp"),
                        )
                        if right:
                            _build_single_mission_card(
                                right["title"], right["current"], right["upper"], right["ratio"], card_w,
                                next_need=right.get("next_need"), next_exp=right.get("next_exp"),
                            )
                        else:
                            Spacer(w=card_w, h=1)

    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令处理 ======================= #

# 绑定id或查询绑定id
pjsk_bind = SekaiCmdHandler([
    "/pjsk bind", "/pjsk id",
    "/绑定", "/pjsk 绑定"
], parse_uid_arg=False)
pjsk_bind.check_cdrate(cd).check_wblist(gbl)
@pjsk_bind.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    args = ''.join([c for c in args if c.isdigit()])
    
    # -------------- 查询 -------------- #

    if not args:
        has_any = False
        msg = ""
        for region in ALL_SERVER_REGIONS:
            region_ctx = SekaiHandlerContext.from_region(region)
            main_uid = get_player_bind_id(region_ctx, ctx.user_id, check_bind=False)

            lines = []
            for i in range(get_player_bind_count(region_ctx, ctx.user_id)):
                uid = get_player_bind_id(region_ctx, ctx.user_id, index=i)
                is_main = (uid == main_uid)
                uid = process_hide_uid(ctx, uid, keep=6)
                line = f"[{i+1}] {uid}"
                if is_main:
                    line = "*" + line
                lines.append(line)

            if lines:
                has_any = True
                msg += f"【{get_region_name(region)}】\n" + '\n'.join(lines) + '\n'

        if not has_any:
            return await ctx.asend_reply_msg("你还没有绑定过游戏ID，请使用\"/绑定 游戏ID\"进行绑定")
        
        msg += """
标注星号的是查询时默认的主账号，其他账号需要手动指定，例如"/个人信息 u2"查询第二个账号的个人信息
""".strip()
        return await ctx.asend_fold_msg_adaptive(msg.strip())

    # -------------- 绑定 -------------- #

    # 检查是否在黑名单中
    assert_and_reply(not check_uid_in_blacklist(args), f"该游戏ID({args})已被拉入黑名单，无法绑定")
    
    # 检查有效的服务器
    checked_regions = []
    async def check_bind(region: str) -> Optional[Tuple[str, str, str]]:
        try:
            region_ctx = SekaiHandlerContext.from_region(region)
            if not get_gameapi_config(region_ctx).profile_api_url:
                return None
            # 检查格式
            if not validate_uid(region_ctx, args):
                return region, None, f"ID格式错误"
            checked_regions.append(get_region_name(region))
            profile = await get_basic_profile(region_ctx, args, use_cache=False, use_remote_cache=False, raise_when_no_found=False)
            if not profile:
                return region, None, "找不到该ID的玩家"
            user_name = profile['user']['name']
            return region, user_name, None
        except Exception as e:
            logger.warning(f"在 {region} 服务器尝试绑定失败: {get_exc_desc(e)}")
            return region, None, "内部错误，请稍后重试"
        
    check_results = await asyncio.gather(*[check_bind(region) for region in ALL_SERVER_REGIONS])
    check_results = [res for res in check_results if res]
    ok_check_results = [res for res in check_results if res[2] is None]

    if not ok_check_results:
        reply_text = f"所有支持的服务器尝试绑定失败，请检查ID是否正确"
        for region, _, err_msg in check_results:
            if err_msg:
                reply_text += f"\n{get_region_name(region)}: {err_msg}"
        return await ctx.asend_reply_msg(reply_text)
    
    if len(ok_check_results) > 1:
        await ctx.asend_reply_msg(f"该ID在多个服务器都存在！默认绑定找到的第一个服务器")
    region, user_name, _ = ok_check_results[0]
    qid = str(ctx.user_id)
    uid = args

    region_ctx = SekaiHandlerContext.from_region(region)
    last_bind_main_id = get_player_bind_id(region_ctx, ctx.user_id, check_bind=False)

    # 检查绑定次数限制
    if not check_superuser(ctx.event):
        date = get_date_str()
        all_daily_info = bind_history_db.get(f"{region}_daily", {})
        daily_info = all_daily_info.get(qid, { 'date': date, 'ids': [] })
        if daily_info['date'] != date:
            daily_info = { 'date': date, 'ids': [] }

        today_ids = set(daily_info.get('ids', []))
        today_ids.add(uid)
        if last_bind_main_id:
            today_ids.add(last_bind_main_id) # 当前绑定的id也算在内

        daily_info['ids'] = list(today_ids)
        if len(daily_info['ids']) > DAILY_BIND_LIMITS.get().get(region, 1e9):
            return await ctx.asend_reply_msg(f"你今日绑定{get_region_name(region)}帐号的数量已达上限")
        all_daily_info[qid] = daily_info
        bind_history_db.set(f"{region}_daily", all_daily_info)

    msg = f"{get_region_name(region)}绑定成功: {user_name}"

    # 如果以前没有绑定过其他区服，设置默认服务器
    other_bind = None
    for r in ALL_SERVER_REGIONS:
        if r == region: continue
        other_bind = other_bind or get_player_bind_id(SekaiHandlerContext.from_region(r), ctx.user_id, check_bind=False)
    default_region = get_user_default_region(ctx.user_id, None)
    if not other_bind and not default_region:
        msg += f"\n已设置你的默认服务器为{get_region_name(region)}，如需修改可使用\"/pjsk服务器\""
        set_user_default_region(ctx.user_id, region)
    if default_region and default_region != region:
        msg += f"\n你的默认服务器为{get_region_name(default_region)}，查询{get_region_name(region)}需加前缀{region}，或使用\"/pjsk服务器\"修改默认服务器"

    # 如果该区服以前没有绑定过，设置默认隐藏id
    if not last_bind_main_id:
        lst = profile_db.get("hide_id_list", {})
        if region not in lst:
            lst[region] = []
        if ctx.user_id not in lst[ctx.region]:
            lst[region].append(ctx.user_id)
        profile_db.set("hide_id_list", lst)

    # 进行绑定
    bind_msg = add_player_bind_id(region_ctx, ctx.user_id, uid, set_main=True)
    msg += "\n" + bind_msg

    # 保存绑定历史
    bind_history = bind_history_db.get("history", {})
    if qid not in bind_history:
        bind_history[qid] = []
    bind_history[qid].append({
        "time": int(time.time() * 1000),
        "region": region,
        "uid": uid,
    })
    bind_history_db.set("history", bind_history)
    
    return await ctx.asend_reply_msg(msg.strip())


# 解绑id
pjsk_unbind = SekaiCmdHandler([
    "/pjsk unbind", "/pjsk解绑", "/解绑",
], parse_uid_arg=False)
pjsk_unbind.check_cdrate(cd).check_wblist(gbl)
@pjsk_unbind.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip().lower()
    qid = ctx.user_id
    try:
        args = args.replace('u', '')
        index = int(args) - 1
    except:
        raise ReplyException(f"""
解除第x个账号绑定:"{ctx.original_trigger_cmd} x"
发送"/绑定"查询已绑定的账号
""".strip())
    
    msg = remove_player_bind_id(ctx, qid, index=index)
    return await ctx.asend_reply_msg(msg)


# 设置主账号
pjsk_set_main = SekaiCmdHandler([
    "/pjsk set main", "/pjsk主账号", "/设置主账号", "/主账号",
], parse_uid_arg=False)
pjsk_set_main.check_cdrate(cd).check_wblist(gbl)
@pjsk_set_main.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    qid = ctx.user_id
    try:
        args = args.replace('u', '')
        index = int(args) - 1
    except:
        raise ReplyException(f"""
使用方式: 
设置主账号为你第x个绑定的账号: {ctx.original_trigger_cmd} x
""".strip())
    
    msg = set_player_main_bind_id(ctx, qid, index=index)
    return await ctx.asend_reply_msg(msg)


# 交换绑定账号顺序
pjsk_swap_bind = SekaiCmdHandler([
    "/pjsk swap bind", "/pjsk交换绑定", 
    "/交换绑定", "/绑定交换", "/交换账号", "/交换账号顺序",
], parse_uid_arg=False)
pjsk_swap_bind.check_cdrate(cd).check_wblist(gbl)
@pjsk_swap_bind.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip().split()
    qid = ctx.user_id
    try:
        index1 = int(args[0].replace('u', '')) - 1
        index2 = int(args[1].replace('u', '')) - 1
    except:
        raise ReplyException(f"""
使用方式:
交换你绑定的第x个和第y个账号的位置: {ctx.original_trigger_cmd} x y
""".strip())
    
    msg = swap_player_bind_id(ctx, qid, index1=index1, index2=index2)
    return await ctx.asend_reply_msg(msg)


# 隐藏抓包信息
pjsk_hide_suite = SekaiCmdHandler([
    "/pjsk hide suite",
    "/pjsk隐藏抓包", "/隐藏抓包",
])
pjsk_hide_suite.check_cdrate(cd).check_wblist(gbl)
@pjsk_hide_suite.handle()
async def _(ctx: SekaiHandlerContext):
    lst = profile_db.get("hide_suite_list", {})
    if ctx.region not in lst:
        lst[ctx.region] = []
    if ctx.user_id not in lst[ctx.region]:
        lst[ctx.region].append(ctx.user_id)
    profile_db.set("hide_suite_list", lst)
    return await ctx.asend_reply_msg(f"已隐藏{get_region_name(ctx.region)}抓包信息")
    

# 展示抓包信息
pjsk_show_suite = SekaiCmdHandler([
    "/pjsk show suite",
    "/pjsk显示抓包", "/pjsk展示抓包", "/展示抓包",
])
pjsk_show_suite.check_cdrate(cd).check_wblist(gbl)
@pjsk_show_suite.handle()
async def _(ctx: SekaiHandlerContext):
    lst = profile_db.get("hide_suite_list", {})
    if ctx.region not in lst:
        lst[ctx.region] = []
    if ctx.user_id in lst[ctx.region]:
        lst[ctx.region].remove(ctx.user_id)
    profile_db.set("hide_suite_list", lst)
    return await ctx.asend_reply_msg(f"已展示{get_region_name(ctx.region)}抓包信息")


# 隐藏id信息
pjsk_hide_id = SekaiCmdHandler([
    "/pjsk hide id",
    "/pjsk隐藏id", "/pjsk隐藏ID", "/隐藏id", "/隐藏ID",
])
pjsk_hide_id.check_cdrate(cd).check_wblist(gbl)
@pjsk_hide_id.handle()
async def _(ctx: SekaiHandlerContext):
    lst = profile_db.get("hide_id_list", {})
    if ctx.region not in lst:
        lst[ctx.region] = []
    if ctx.user_id not in lst[ctx.region]:
        lst[ctx.region].append(ctx.user_id)
    profile_db.set("hide_id_list", lst)
    return await ctx.asend_reply_msg(f"已隐藏{get_region_name(ctx.region)}ID信息")


# 展示id信息
pjsk_show_id = SekaiCmdHandler([
    "/pjsk show id",
    "/pjsk显示id", "/pjsk显示ID", "/pjsk展示id", "/pjsk展示ID",
    "/展示id", "/展示ID", "/显示id", "/显示ID",
])
pjsk_show_id.check_cdrate(cd).check_wblist(gbl)
@pjsk_show_id.handle()
async def _(ctx: SekaiHandlerContext):
    lst = profile_db.get("hide_id_list", {})
    if ctx.region not in lst:
        lst[ctx.region] = []
    if ctx.user_id in lst[ctx.region]:
        lst[ctx.region].remove(ctx.user_id)
    profile_db.set("hide_id_list", lst)
    return await ctx.asend_reply_msg(f"已展示{get_region_name(ctx.region)}ID信息")


# 查询单角色角色等级任务总览
pjsk_character_rank_mission = SekaiCmdHandler([
    "/cr任务", "/角色等级任务",
])
pjsk_character_rank_mission.check_cdrate(cd).check_wblist(gbl)
@pjsk_character_rank_mission.handle()
async def _(ctx: SekaiHandlerContext):
    help_msg = f"""
使用方式:
1. {ctx.original_trigger_cmd} 角色名
2. {ctx.original_trigger_cmd} 角色名 all 任务名
示例:
{ctx.original_trigger_cmd} miku
{ctx.original_trigger_cmd} miku all 队长次数
发送“/cr任务 help”获取详细帮助
""".strip()
    raw_args = ctx.get_args().strip()
    assert_and_reply(raw_args, help_msg)
    if raw_args.lower() in ("help", "帮助"):
        help_text = f"""
# CR任务

查询指定角色的CR任务进度，或查看某个任务的全量档位表。
需要📡抓包数据。
支持服务器: `所有`

## 基础用法

- `{ctx.original_trigger_cmd} miku`
- `{ctx.original_trigger_cmd} miku all 队长次数`

## 查询模式

- `角色名`
  查询该角色的角色任务总览
- `角色名 all 任务名`
  查询该任务的全量档位、累计需求和累计EXP

## 说明

- `队长次数` 和 `休息室次数` 在 `all` 视图下会同时显示普通任务和EX任务
- 其他任务在 `all` 视图下只显示对应单个任务表

## 可用任务名示例

- `队长次数` `队长`
- `休息室次数` `休息室` `控制室`
- `服装` `衣装`
- `表情` `贴纸`
- `区域对话`
- `前篇` `前编`
- `后篇` `后编`
- `anvo`
- `单人家具` `单人道具`
- `团家具`
- `树花` `属性家具` `属性道具` `植物`
- `卡面` `图鉴` `成员`
- `4星技能` `四星技能` `四星slv` `4星slv`
- `低星技能` `低星slv`
- `4星专精` `四星专精` `四星突破` `4星突破` `4星mr` `四星mr`
- `低星专精` `低星突破` `低星mr`
- `台词` `语音`
- `ms家具` `烤森家具`
- `ms画布` `烤森画布`
- `ms对话` `烤森对话`
""".strip()
        return await ctx.asend_reply_msg(await get_image_cq(
            await markdown_to_image(help_text, width=760),
            low_quality=True,
        ))
    nickname, rest = extract_nickname_from_args(raw_args)
    assert_and_reply(nickname, f"未识别到角色名称\n{help_msg}")
    cid = get_cid_by_nickname(nickname)
    assert_and_reply(cid is not None, f"角色名无效: {nickname}")

    rest = rest.strip()
    if rest:
        from .education import (
            extract_character_rank_all_flag,
            extract_character_rank_mission_type,
            compose_character_rank_mission_all_image,
        )
        show_all, rest = extract_character_rank_all_flag(rest)
        if show_all:
            mission_type, rest = extract_character_rank_mission_type(rest)
            assert_and_reply(mission_type is not None and not rest.strip(), f"未识别到角色等级任务名\n{help_msg}")
            return await ctx.asend_reply_msg(await get_image_cq(
                await compose_character_rank_mission_all_image(ctx, ctx.user_id, cid, mission_type),
                low_quality=True,
            ))
        assert_and_reply(False, f"参数无法解析: {rest}\n{help_msg}")

    profile, err_msg = await get_detailed_profile(
        ctx,
        ctx.user_id,
        filter=get_detailed_profile_card_filter("userCharacterMissionV2s", "userCharacterMissionV2Statuses", "userCharacters"),
        raise_exc=True,
    )

    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_character_rank_mission_overview_image(ctx, profile, err_msg, cid),
        low_quality=True,
    ))


# 查询个人名片
pjsk_info = SekaiCmdHandler([
    "/pjsk profile",
    "/个人信息", "/名片", "/pjsk 个人信息", "/pjsk 名片",
])
pjsk_info.check_cdrate(cd).check_wblist(gbl)
@pjsk_info.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    vertical = None

    for keyword in PROFILE_HORIZONTAL_KEYWORDS:
        if keyword in args:
            vertical = False
            args = args.replace(keyword, '', 1).strip()
            break
    for keyword in PROFILE_VERTICAL_KEYWORDS:
        if keyword in args:
            vertical = True
            args = args.replace(keyword, '', 1).strip()
            break

    uid = get_player_bind_id(ctx)
    profile = await get_basic_profile(ctx, uid, use_cache=True, use_remote_cache=False)
    logger.info(f"绘制名片 region={ctx.region} uid={uid}")
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_profile_image(ctx, profile, vertical=vertical),
        low_quality=True, quality=95,
    ))


# 查询注册时间
pjsk_reg_time = SekaiCmdHandler([
    "/pjsk reg time",
    "/注册时间", "/pjsk 注册时间", "/查时间",
])
pjsk_reg_time.check_cdrate(cd).check_wblist(gbl)
@pjsk_reg_time.handle()
async def _(ctx: SekaiHandlerContext):
    uid = get_player_bind_id(ctx)
    reg_time = get_register_time(ctx.region, uid)
    elapsed = datetime.now() - reg_time
    region_name = get_region_name(ctx.region)
    return await ctx.asend_reply_msg(f"{region_name}注册时间: {reg_time.strftime('%Y-%m-%d %H:%M:%S')} ({elapsed.days}天前)")


# 检查profile服务器状态
pjsk_check_service = SekaiCmdHandler([
    "/pjsk check service", "/pcs", "/pjsk检查服务状态",
])
pjsk_check_service.check_cdrate(cd).check_wblist(gbl)
@pjsk_check_service.handle()
async def _(ctx: SekaiHandlerContext):
    url = get_gameapi_config(ctx).api_status_url
    assert_and_reply(url, f"暂无 {ctx.region} 的查询服务器")
    try:
        data = await request_gameapi(url)
        assert data['status'] == 'ok'
    except Exception as e:
        logger.print_exc(f"profile查询服务状态异常")
        return await ctx.asend_reply_msg(f"profile查询服务异常: {str(e)}")
    return await ctx.asend_reply_msg("profile查询服务正常")


# 设置抓包数据获取模式
pjsk_data_mode = SekaiCmdHandler([
    "/pjsk data mode", 
    "/pjsk抓包模式", "/pjsk抓包获取模式", "/抓包模式",
])
pjsk_data_mode.check_cdrate(cd).check_wblist(gbl)
@pjsk_data_mode.handle()
async def _(ctx: SekaiHandlerContext):
    data_modes = profile_db.get("data_modes", {})
    cur_mode = data_modes.get(ctx.region, {}).get(str(ctx.user_id), DEFAULT_DATA_MODE)
    help_text = f"""
你的{get_region_name(ctx.region)}抓包数据获取模式: {cur_mode} 
---
使用\"{ctx.original_trigger_cmd} 模式名\"来切换模式，可用模式名如下:
【latest】
同时从所有数据源获取，使用最新的一个（推荐）
【default】
从本地数据获取失败才尝试从Haruki工具箱获取
【local】
仅从本地数据获取
【haruki】
仅从Haruki工具箱获取
""".strip()
    
    ats = ctx.get_at_qids()
    if ats and ats[0] != int(ctx.bot.self_id):
        # 如果有at则使用at的qid
        qid = ats[0]
        assert_and_reply(check_superuser(ctx.event), "只有超级管理能修改别人的模式")
    else:
        qid = ctx.user_id
    
    args = ctx.get_args().strip().lower()
    assert_and_reply(args in VALID_DATA_MODES, help_text)

    if ctx.region not in data_modes:
        data_modes[ctx.region] = {}
    data_modes[ctx.region][str(qid)] = args
    profile_db.set("data_modes", data_modes)

    if qid == ctx.user_id:
        return await ctx.asend_reply_msg(f"切换{get_region_name(ctx.region)}抓包数据获取模式:\n{cur_mode} -> {args}")
    else:
        return await ctx.asend_reply_msg(f"切换 {qid} 的{get_region_name(ctx.region)}抓包数据获取模式:\n{cur_mode} -> {args}")


# 查询抓包数据
pjsk_check_data = SekaiCmdHandler([
    "/pjsk check data",
    "/pjsk抓包", "/pjsk抓包状态", "/pjsk抓包数据", "/pjsk抓包查询", "/抓包数据", "/抓包状态", "/抓包信息",
])
pjsk_check_data.check_cdrate(cd).check_wblist(gbl)
@pjsk_check_data.handle()
async def _(ctx: SekaiHandlerContext):
    cqs = extract_cq_code(ctx.get_msg())
    qid = int(cqs['at'][0]['qq']) if 'at' in cqs else ctx.user_id
    uid = get_player_bind_id(ctx)

    task1 = get_detailed_profile(ctx, qid, raise_exc=False, mode="local", filter=['upload_time'])
    task2 = get_detailed_profile(ctx, qid, raise_exc=False, mode="haruki", filter=['upload_time'])
    (local_profile, local_err), (haruki_profile, haruki_err) = await asyncio.gather(task1, task2)

    msg = f"{process_hide_uid(ctx, uid, keep=6)}({ctx.region.upper()}) Suite数据\n"

    if local_err:
        local_err = local_err[local_err.find(']')+1:].strip()
        msg += f"[本地数据]\n获取失败: {local_err}\n"
    else:
        msg += "[本地数据]\n"
        upload_time = datetime.fromtimestamp(local_profile.upload_time / 1000)
        upload_time_text = upload_time.strftime('%m-%d %H:%M:%S') + f"({get_readable_datetime(upload_time, show_original_time=False)})"
        if local_source := local_profile.local_source:
            upload_time_text = local_source + " " + upload_time_text
        msg += f"{upload_time_text}\n"

    if haruki_err:
        haruki_err = haruki_err[haruki_err.find(']')+1:].strip()
        msg += f"[Haruki工具箱]\n获取失败: {haruki_err}\n"
    else:
        msg += "[Haruki工具箱]\n"
        upload_time = datetime.fromtimestamp(haruki_profile.upload_time / 1000)
        upload_time_text = upload_time.strftime('%m-%d %H:%M:%S') + f"({get_readable_datetime(upload_time, show_original_time=False)})"
        msg += f"{upload_time_text}\n"

    mode = get_user_data_mode(ctx, ctx.user_id)
    msg += f"---\n"
    msg += f"该指令查询Suite数据，查询Mysekai数据请使用\"/{ctx.region}msd\"\n"
    # msg += f"数据获取模式: {mode}，使用\"/{ctx.region}抓包模式\"来切换模式\n"
    msg += f"发送\"/抓包\"获取抓包教程"

    return await ctx.asend_reply_msg(msg)


# 添加游戏id到黑名单
pjsk_blacklist = CmdHandler([
    "/pjsk blacklist add", "/pjsk add blacklist",
    "/pjsk黑名单添加", "/pjsk添加黑名单",
], logger)
pjsk_blacklist.check_cdrate(cd).check_wblist(gbl).check_superuser()
@pjsk_blacklist.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    assert_and_reply(args, "请提供要添加的游戏ID")
    blacklist = profile_db.get("blacklist", [])
    if args in blacklist:
        return await ctx.asend_reply_msg(f"ID {args} 已在黑名单中")
    blacklist.append(args)
    profile_db.set("blacklist", blacklist)
    return await ctx.asend_reply_msg(f"ID {args} 已添加到黑名单中")


# 移除游戏id到黑名单
pjsk_blacklist_remove = CmdHandler([
    "/pjsk blacklist remove", "/pjsk blacklist del", "/pjsk remove blacklist", "/pjsk del blacklist",
    "/pjsk黑名单移除", "/pjsk移除黑名单", "/pjsk删除黑名单",
], logger)
pjsk_blacklist_remove.check_cdrate(cd).check_wblist(gbl).check_superuser()
@pjsk_blacklist_remove.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    assert_and_reply(args, "请提供要移除的游戏ID")
    blacklist = profile_db.get("blacklist", [])
    if args not in blacklist:
        return await ctx.asend_reply_msg(f"ID {args} 不在黑名单中")
    blacklist.remove(args)
    profile_db.set("blacklist", blacklist)
    return await ctx.asend_reply_msg(f"ID {args} 已从黑名单中移除")


# 验证用户游戏帐号
verify_game_account = SekaiCmdHandler([
    "/pjsk verify", "/pjsk验证",
])
verify_game_account.check_cdrate(cd).check_wblist(gbl).check_cdrate(verify_rate_limit)
@verify_game_account.handle()
async def _(ctx: SekaiHandlerContext):
    await ctx.block_region(key=str(ctx.user_id))
    await verify_user_game_account(ctx)


# 查询用户验证过的游戏ID列表
get_verified_uids = SekaiCmdHandler([
    "/pjsk verify list", "/pjsk验证列表", "/pjsk验证状态", 
])
get_verified_uids.check_cdrate(cd).check_wblist(gbl)
@get_verified_uids.handle()
async def _(ctx: SekaiHandlerContext):
    uids = get_user_verified_uids(ctx)
    msg = ""
    region_name = get_region_name(ctx.region)
    if not uids:
        msg += f"你还没有验证过任何{region_name}游戏ID\n"
    else:
        msg += f"你验证过的{region_name}游戏ID:\n"
        for uid in uids:
            msg += process_hide_uid(ctx, uid, keep=6) + "\n"
    msg += f"---\n"
    msg += f"使用\"/{ctx.region}pjsk验证\"进行验证"
    return await ctx.asend_reply_msg(msg)


# 上传个人信息背景图片
upload_profile_bg = SekaiCmdHandler([
    "/pjsk upload profile bg", "/pjsk upload profile background",
    "/上传个人信息背景", "/上传个人信息图片", "/上传个人背景", "/上传个人信息",
])
upload_profile_bg.check_cdrate(cd).check_wblist(gbl).check_cdrate(profile_bg_upload_rate_limit)
@upload_profile_bg.handle()
async def _(ctx: SekaiHandlerContext):
    await ctx.block_region(key=str(ctx.user_id))

    args = ctx.get_args().strip()
    force = False
    if 'force' in args and check_superuser(ctx.event):
        force = True
        args = args.replace('force', '').strip()

    uid = await get_uid_and_check_verified(ctx, force)
    img_url = await ctx.aget_image_urls(return_first=True)
    res = await image_safety_check(img_url)
    if res.suggest_block():
        raise ReplyException(f"图片审核结果: {res.message}")
    img = await download_image(img_url)
    await set_profile_bg_settings(ctx, image=img, force=force)

    msg = f"背景设置成功，使用\"/{ctx.region}调整个人信息\"可以调整界面方向、模糊、透明度\n"
    if res.suggest_review():
        msg += f"图片审核结果: {res.message}"
        logger.warning(f"用户 {ctx.user_id} 上传的个人信息背景图片需要人工审核: {res.message}")
        review_log_path = f"{SEKAI_PROFILE_DIR}/profile_bg_review.log"
        with open(review_log_path, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} {ctx.user_id} set {ctx.region} {uid}\n")

    try:
        img_cq = await get_image_cq(
            await compose_profile_image(ctx, await get_basic_profile(ctx, uid)),
            low_quality=True,
        )
        msg = img_cq + msg.strip()
    except Exception as e:
        logger.print_exc(f"绘制个人信息背景图片失败: {get_exc_desc(e)}")
        msg += f"绘制个人信息背景图片失败: {get_exc_desc(e)}"

    return await ctx.asend_reply_msg(msg)


# 清空个人信息背景图片
clear_profile_bg = SekaiCmdHandler([
    "/pjsk clear profile bg", "/pjsk clear profile background",
    "/清空个人信息背景", "/清除个人信息背景",  "/清空个人信息图片", "/清除个人信息图片", 
])
clear_profile_bg.check_cdrate(cd).check_wblist(gbl)
@clear_profile_bg.handle()
async def _(ctx: SekaiHandlerContext):
    await ctx.block_region(key=str(ctx.user_id))

    args = ctx.get_args().strip()
    force = False
    if 'force' in args and check_superuser(ctx.event):
        force = True
        args = args.replace('force', '').strip()

    await set_profile_bg_settings(ctx, remove_image=True, force=force)
    return await ctx.asend_reply_msg(f"已清空{get_region_name(ctx.region)}个人信息背景图片")


# 调整个人信息背景设置
adjust_profile_bg = SekaiCmdHandler([
    "/pjsk adjust profile", "/pjsk adjust profile bg", "/pjsk adjust profile background",
    "/调整个人信息背景", "/调整个人信息", "/设置个人信息", "/设置个人信息背景",
])
adjust_profile_bg.check_cdrate(cd).check_wblist(gbl)
@adjust_profile_bg.handle()
async def _(ctx: SekaiHandlerContext):
    await ctx.block_region(key=str(ctx.user_id))

    args = ctx.get_args().strip()
    force = False
    if 'force' in args and check_superuser(ctx.event):
        force = True
        args = args.replace('force', '').strip()

    uid = await get_uid_and_check_verified(ctx, force)
    HELP = f"""
调整横屏/竖屏:
{ctx.original_trigger_cmd} 竖屏
调整界面模糊度(0为无模糊):
{ctx.original_trigger_cmd} 模糊 0~10
调整界面透明度(0为不透明):
{ctx.original_trigger_cmd} 透明 0~100
""".strip()
    
    args = ctx.get_args().strip()
    if not args:
        settings = get_profile_bg_settings(ctx)
        msg = f"当前{get_region_name(ctx.region)}个人信息背景设置:\n"
        msg += f"ID: {process_hide_uid(ctx, uid, keep=6)}\n"
        msg += f"方向: {'竖屏' if settings.vertical else '横屏'}\n"
        msg += f"模糊度: {settings.blur}\n"
        msg += f"透明度: {100 - int(settings.alpha * 100 // 255)}\n"
        msg += f"---\n"
        msg += HELP
        return await ctx.asend_reply_msg(msg.strip())

    vertical, blur, alpha = None, None, None
    try:
        args = args.replace('度', '').replace('%', '')

        for keyword in PROFILE_HORIZONTAL_KEYWORDS:
            if keyword in args:
                vertical = False
                args = args.replace(keyword, '', 1).strip()
                break
        for keyword in PROFILE_VERTICAL_KEYWORDS:
            if keyword in args:
                vertical = True
                args = args.replace(keyword, '', 1).strip()
                break

        if '全模糊' in args:
            blur = 10
        elif '无模糊' in args or '不模糊' in args:
            blur = 0
        elif '模糊' in args:
            numarg = args.split('模糊')[1].strip()
            num = ''
            for c in numarg:
                if c.isdigit():
                    num += c
                elif num:
                    break
            blur = int(num)

        if '不透明' in args:
            alpha = 255
        elif '全透明' in args:
            alpha = 0
        elif '透明' in args:
            numarg = args.split('透明')[1].strip()
            num = ''
            for c in numarg:
                if c.isdigit():
                    num += c
                elif num:
                    break
            alpha = (100 - int(num)) * 255 // 100
    except:
        raise ReplyException(HELP)
    
    if blur is not None:
        assert_and_reply(0 <= blur <= 10, "模糊度必须在0到10之间")
    if alpha is not None:
        assert_and_reply(0 <= alpha <= 255, "透明度必须在0到100之间")
    
    await set_profile_bg_settings(ctx, vertical=vertical, blur=blur, alpha=alpha, force=force)
    settings = get_profile_bg_settings(ctx)

    msg = f"当前设置: {'竖屏' if settings.vertical else '横屏'} 透明度{100 - int(settings.alpha * 100 / 255)} 模糊度{settings.blur}\n"

    try:
        img_cq = await get_image_cq(
            await compose_profile_image(ctx, await get_basic_profile(ctx, uid)),
            low_quality=True,
        )
        msg = img_cq + msg.strip()
    except Exception as e:
        logger.print_exc(f"绘制个人信息背景图片失败: {get_exc_desc(e)}")
        msg += f"绘制个人信息背景图片失败: {get_exc_desc(e)}"
    return await ctx.asend_reply_msg(msg.strip())


# 查询用户统计
pjsk_user_sta = CmdHandler([
    "/pjsk user sta", "/用户统计",
], logger)
pjsk_user_sta.check_cdrate(cd).check_wblist(gbl).check_superuser()
@pjsk_user_sta.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    group_mode = False
    detail_mode = False
    if '群' in args or 'group' in args:
        group_mode = True
    if '详细' in args or 'detail' in args:
        detail_mode = True
    bind_list: Dict[str, Dict[str, str]] = profile_db.get("bind_list", {})
    suite_total, mysekai_total, qid_set = 0, 0, set()
    suite_source_total: dict[str, int] = {}
    mysekai_source_total: dict[str, int] = {}

    msg = "所有群聊统计:\n" if not group_mode else "当前群聊统计:\n"
    group_qids = set([str(m['user_id']) for m in await get_group_users(ctx.bot, ctx.group_id)])

    for region in ALL_SERVER_REGIONS:
        qids = set(bind_list.get(region, {}).keys())
        uids = set()
        if group_mode:
            qids = qids.intersection(group_qids)
            for qid in qids:
                for uid in to_list(bind_list.get(region, {}).get(qid, [])):
                    uids.add(uid)
        qid_set.update(qids)

        suites = glob.glob(config.get("suite_path").format(region=region))
        if group_mode:
            suites = [s for s in suites if s.split('/')[-1].split('.')[0] in uids]
        suite_total += len(suites)

        mysekais = glob.glob(config.get("mysekai_path").format(region=region))
        if group_mode:
            mysekais = [m for m in mysekais if m.split('/')[-1].split('.')[0] in uids]
        mysekai_total += len(mysekais)

        msg += f"【{get_region_name(region)}】\n绑定 {len(qids)} | Suite {len(suites)} | MySekai {len(mysekais)}\n"

        if detail_mode:
            suite_source_num: dict[str, int] = {}
            mysekai_source_num: dict[str, int] = {}
            def get_detail():
                for p in suites:
                    local_source = load_json_zstd(p).get('local_source', '未知')
                    suite_source_num[local_source] = suite_source_num.get(local_source, 0) + 1
                for k, v in suite_source_num.items():
                    suite_source_total[k] = suite_source_total.get(k, 0) + v
                for p in mysekais:
                    local_source = load_json_zstd(p).get('local_source', '未知')
                    mysekai_source_num[local_source] = mysekai_source_num.get(local_source, 0) + 1
                for k, v in mysekai_source_num.items():
                    mysekai_source_total[k] = mysekai_source_total.get(k, 0) + v
            await run_in_pool(get_detail)
            msg += "Suite来源: " + " | ".join([f"{k} {v}" for k, v in suite_source_num.items()]) + "\n"
            msg += "MySekai来源: " + " | ".join([f"{k} {v}" for k, v in mysekai_source_num.items()]) + "\n"


    msg += f"---\n【总计】\n绑定 {len(qid_set)} | Suite {suite_total} | MySekai {mysekai_total}"
    if detail_mode:
        msg += "\nSuite来源: " + " | ".join([f"{k} {v}" for k, v in suite_source_total.items()])
        msg += "\nMySekai来源: " + " | ".join([f"{k} {v}" for k, v in mysekai_source_total.items()])

    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 查询绑定历史
pjsk_bind_history = CmdHandler([
    "/pjsk bind history", "/pjsk bind his", "/绑定历史", "/绑定记录",
], logger, priority=1)
pjsk_bind_history.check_cdrate(cd).check_wblist(gbl).check_superuser()
@pjsk_bind_history.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    uid = None
    for region in ALL_SERVER_REGIONS:
        if validate_uid(SekaiHandlerContext.from_region(region), args):
            uid = args
            break

    if not uid:
        if ats := ctx.get_at_qids():
            qid = str(ats[0])
        else:
            qid = args

    bind_history = bind_history_db.get("history", {})
    if uid:
        # 游戏ID查QQ号
        has_any = False
        msg = f"当前绑定游戏ID{uid}的QQ用户:\n"
        for region in ALL_SERVER_REGIONS:
            bind_list: Dict[str, str | list[str]] = profile_db.get("bind_list", {}).get(region, {})
            for qid, items in bind_list.items():
                if uid in to_list(items):
                    msg += f"{qid}\n"
                    has_any = True
        if not has_any:
            msg += "无\n"

        has_any = False
        msg += f"曾经绑定过{uid}的QQ用户:\n"
        for qid, items in bind_history.items():
            for item in items:
                if item['uid'] == uid:
                    time = datetime.fromtimestamp(item['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    msg += f"[{time}] {qid}"
                    has_any = True
        if not has_any:
            msg += "无\n"
            
    else:
        # QQ号查游戏ID
        has_any = False
        msg = f"用户{qid}当前绑定:\n"
        for region in ALL_SERVER_REGIONS:
            region_ctx = SekaiHandlerContext.from_region(region)
            main_uid = get_player_bind_id(region_ctx, qid, check_bind=False)
            lines = []
            for i in range(get_player_bind_count(region_ctx, qid)):
                uid = get_player_bind_id(region_ctx, qid, index=i)
                is_main = (uid == main_uid)
                line = f"[{i+1}] {uid}"
                if is_main:
                    line = "*" + line
                lines.append(line)
            if lines:
                has_any = True
                msg += f"【{get_region_name(region)}】\n" + '\n'.join(lines) + '\n'
        if not has_any:
            msg += "无\n"

        has_any = False
        msg += f"用户{qid}的绑定历史:\n"
        items = bind_history.get(qid, [])
        for item in items:
            time = datetime.fromtimestamp(item['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            msg += f"[{time}]\n{item['region']} {item['uid']}\n"
            has_any = True
        if not has_any:
            msg += "无\n"

    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 创建游客账号
pjsk_create_guest_account = SekaiCmdHandler([
    "/pjsk create guest", "/pjsk register", "/pjsk注册",
], regions=['jp', 'en'])
guest_account_create_rate_limit = RateLimit(file_db, logger, 2, 'd', rate_limit_name='注册游客账号')
pjsk_create_guest_account.check_cdrate(cd).check_wblist(gbl).check_cdrate(guest_account_create_rate_limit)
@pjsk_create_guest_account.handle()
async def _(ctx: SekaiHandlerContext):
    region_name = get_region_name(ctx.region)
    url = get_gameapi_config(ctx).create_account_api_url
    assert_and_reply(url, f"不支持注册{region_name}帐号")
    data = await request_gameapi(url, method="POST")
    return await ctx.asend_fold_msg([
        f"注册{region_name}帐号成功，引继码和引继密码如下，登陆后请及时重新生成引继码",
        data['inherit_id'],
        data['inherit_pw'],
    ])
