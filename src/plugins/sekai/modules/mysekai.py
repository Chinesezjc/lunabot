from ...utils import *
from ...llm import translate_text
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..sub import SekaiUserSubHelper, SekaiGroupSubHelper
from ..gameapi import get_gameapi_config, request_gameapi
from .profile import (
    get_player_bind_id, 
    get_player_bind_count,
    get_player_bind_id_index,
    SEKAI_PROFILE_DIR,
    get_basic_profile,
    get_player_avatar_info_by_basic_profile,
    get_player_avatar_info_by_detailed_profile,
    get_user_data_mode,
    get_detailed_profile,
    get_detailed_profile_card,
    get_detailed_profile_card_filter,
    process_hide_uid,
    get_player_frames,
    get_avatar_widget_with_frame,
    process_sensitive_cmd_source,
)
from .music import get_music_cover_thumb, is_valid_music
from .card import get_character_sd_image
from ...imgtool import shrink_image

# 已丢弃的区服 ['en', 'tw', 'kr']
# MYSEKAI_REGIONS = ['jp', 'tw', 'cn', 'kr']
# BD_MYSEKAI_REGIONS = ['cn', 'tw', 'kr']

MYSEKAI_REGIONS = ['jp', 'cn']
BD_MYSEKAI_REGIONS = ['cn']

bd_msr_sub = SekaiGroupSubHelper("msr", "msr指令权限", BD_MYSEKAI_REGIONS)
msr_sub = SekaiUserSubHelper("msr", "烤森资源查询自动推送", MYSEKAI_REGIONS, only_one_group=True)

class MsrIdNotMatchException(ReplyException):
    pass

MYSEKAI_HARVEST_MAP_IMAGE_SCALE_CFG = config.item('mysekai.map_image_scale')
MYSEKAI_HARVEST_MAP_SITE_BG_IMAGE_DOWNSAMPLE = 0.5

MYSEKAI_RARE_RES_KEYS_CFG = config.item('mysekai.rare_res_keys')
MYSEKAI_HARVEST_FIXTURE_IMAGE_NAME = {
    1001: "oak.png",
    1002: "pine.png",
    1003: "palm.png",
    1004: "luxury.png",
    2001: "stone.png", 
    2002: "copper.png", 
    2003: "glass.png", 
    2004: "iron.png", 
    2005: "crystal.png", 
    2006: "diamond.png",
    3001: "toolbox.png",
    6001: "barrel.png",
    5001: "junk.png",
    5002: "junk.png",
    5003: "junk.png",
    5004: "junk.png",
    5101: "junk.png",
    5102: "junk.png",
    5103: "junk.png",
    5104: "junk.png",
}

mysekairun_friendcode_data = {}
mysekairun_friendcode_mtime = None
sekai8823_friendcode_data = WebJsonRes(
    "sekai.8823家具好友码信息", 
    "https://pjsk-static.8823.eu.org/api/fixtures/", 
    update_interval=timedelta(hours=3),
)

UNIT_GATEID_MAP = {
    "light_sound": 1,
    "idol": 2,
    "street": 3,
    "theme_park": 4,
    "school_refusal": 5,
}

SITE_ID_ORDER = (
    5, 7, 6, 8,
)

MSR_PUSH_CONCURRENCY_CFG = config.item('mysekai.msr_push_concurrency')

bd_msr_bind_db = get_file_db(f"{SEKAI_PROFILE_DIR}/bd_msr_bind.json", logger)

harvest_point_image_offsets_cache: dict[int, Tuple[Image.Image, tuple[int, int]]] = {}

MYSEKAI_ICON_CACHE_RES = 64 * 64

# ======================= 处理逻辑 ======================= #

# 获取ms自然刷新小时
def get_mysekai_refresh_hours(ctx: SekaiHandlerContext) -> Tuple[int, int]:
    return (
        region_hour_to_local(ctx.region, 5), 
        region_hour_to_local(ctx.region, 17),
    )

# 判断ms资源稀有等级（0, 1, 2)
def get_mysekai_res_rarity(key: str) -> int:
    t1, id1 = key.rsplit("_", 1)
    for rare, keys in MYSEKAI_RARE_RES_KEYS_CFG.get().items():
        for k in keys:
            if '~' in k:
                t2, id_range = k.rsplit('_', 1)
                min_id, max_id = map(int, id_range.split('~'))
                if t1 == t2 and min_id <= int(id1) <= max_id:
                    return rare
            elif k == key:
                return rare
    return 0

# 从角色UnitId获取角色图标
async def get_chara_icon_by_chara_unit_id(ctx: SekaiHandlerContext, cuid: int) -> Image.Image:
    cu = await ctx.md.game_character_units.find_by_id(cuid)
    return get_chara_icon_by_chara_id(cid=cu['gameCharacterId'], unit=cu['unit'])

# 获取玩家mysekai抓包数据 返回 (mysekai_info, err_msg)
async def get_mysekai_info(
    ctx: SekaiHandlerContext, 
    qid: int, 
    raise_exc=False, 
    mode=None, 
    filter: list[str]=None, 
) -> Tuple[dict, str]:
    cache_path = None
    uid = None
    try:
        # 获取绑定的玩家id
        try:
            uid = get_player_bind_id(ctx)
        except Exception as e:
            logger.info(f"获取 {qid} {ctx.region} mysekai抓包数据失败: 未绑定游戏账号")
            raise e
        
        # 服务器不支持
        url = get_gameapi_config(ctx).mysekai_api_url
        if not url:
            raise ReplyException(f"暂不支持{get_region_name(ctx.region)}的Mysekai抓包数据查询")

        # 获取模式
        mode = mode or get_user_data_mode(ctx, qid)

        # 尝试下载
        try:
            url = url.format(uid=uid) + f"?mode={mode}"
            if filter:
                url += f"&filter={','.join(filter)}"
            mysekai_info = await request_gameapi(url)
        except HttpError as e:
            logger.info(f"获取 {qid} {ctx.region} {uid} mysekai抓包数据失败: {get_exc_desc(e)}")
            if e.status_code == 404:
                local_err = e.message.get('local_err', None)
                haruki_err = e.message.get('haruki_err', None)
                msg = f"获取你的{get_region_name(ctx.region)}Mysekai抓包数据失败，发送\"/抓包\"指令可获取帮助\n"
                if local_err is not None: msg += f"[本地数据] {local_err}\n"
                if haruki_err is not None: msg += f"[Haruki工具箱] {haruki_err}\n"
                raise ReplyException(msg.strip())
            else:
                raise e
        except Exception as e:
            logger.info(f"获取 {qid} {ctx.region} {uid} mysekai抓包数据失败: {get_exc_desc(e)}")
            raise e
        
        if not mysekai_info:
            logger.info(f"获取 {qid} {ctx.region} {uid} mysekai抓包数据失败: 找不到该玩家")
            raise ReplyException(f"找不到ID为 {uid} 的玩家")
        
        # 缓存数据（目前已不缓存）
        cache_path = f"{SEKAI_PROFILE_DIR}/mysekai_cache/{ctx.region}/{uid}.json"
        # if not upload_time_only:
        #     dump_json(mysekai_info, cache_path)
        logger.info(f"获取 {qid} {ctx.region} {uid} mysekai抓包数据成功，数据已缓存")

    except Exception as e:
        # 获取失败的情况，尝试读取缓存
        if cache_path and os.path.exists(cache_path):
            mysekai_info = load_json(cache_path)
            logger.info(f"从缓存获取 {qid} {ctx.region} {uid} mysekai抓包数据")
            return mysekai_info, get_exc_desc(e) + "(使用先前的缓存数据)"
        else:
            logger.info(f"未找到 {qid} {ctx.region} {uid} 的缓存mysekai抓包数据")

        if raise_exc:
            raise e
        else:
            return None, get_exc_desc(e)
    return mysekai_info, ""

# 获取玩家mysekai抓包数据的简单卡片 返回 Frame
async def get_mysekai_info_card(ctx: SekaiHandlerContext, mysekai_info: dict, basic_profile: dict, err_msg: str) -> Frame:
    with Frame().set_bg(roundrect_bg()).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if mysekai_info:
                avatar_info = await get_player_avatar_info_by_basic_profile(ctx, basic_profile)

                frames = get_player_frames(ctx, basic_profile['user']['userId'], None)
                await get_avatar_widget_with_frame(ctx, avatar_info.img, 80, frames)

                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    game_data = basic_profile['user']
                    mysekai_game_data = mysekai_info['updatedResources']['userMysekaiGamedata']
                    if ctx.region in BD_MYSEKAI_REGIONS:
                        process_sensitive_cmd_source(mysekai_info)
                    source = mysekai_info.get('source', '?')
                    if local_source := mysekai_info.get('local_source'):
                        source += f"({local_source})"
                    mode = get_user_data_mode(ctx, ctx.user_id)
                    update_time = datetime.fromtimestamp(mysekai_info['upload_time'] / 1000)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    with HSplit().set_content_align('lb').set_item_align('lb').set_sep(5):
                        hs = colored_text_box(
                            truncate(game_data['name'], 64),
                            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2, shadow_color=ADAPTIVE_SHADOW),
                        )
                        name_length = 0
                        for item in hs.items:
                            if isinstance(item, TextBox):
                                name_length += get_str_display_length(item.text)
                        ms_lv = mysekai_game_data['mysekaiRank']
                        ms_lv_text = f"MySekai Lv.{ms_lv}" if name_length <= 12 else f"MSLv.{ms_lv}"
                        TextBox(ms_lv_text, TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))

                    TextBox(f"{ctx.region.upper()}: {process_hide_uid(ctx, game_data['userId'], keep=6)} Mysekai数据", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"数据来源: {source}  获取模式: {mode}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
            if err_msg:
                TextBox(f"获取数据失败:{err_msg}", TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(240)
    return f

# 获取玩家mysekai抓包数据+suite数据的简单卡片 返回 Frame
async def get_mysekai_and_detail_profile_card(ctx: SekaiHandlerContext, mysekai_info: dict, profile: dict, err_msg: str, mode: str = None) -> Frame:
    with Frame().set_bg(roundrect_bg()).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if profile and mysekai_info:
                avatar_info = await get_player_avatar_info_by_detailed_profile(ctx, profile)

                frames = get_player_frames(ctx, profile['userGamedata']['userId'], profile)
                await get_avatar_widget_with_frame(ctx, avatar_info.img, 80, frames)

                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    game_data = profile['userGamedata']
                    source = profile.get('source', '?')
                    if local_source := profile.get('local_source'):
                        source += f"({local_source})"
                    mode = mode or get_user_data_mode(ctx, ctx.user_id)
                    update_time = datetime.fromtimestamp(profile['upload_time'] / 1000)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    user_id = process_hide_uid(ctx, game_data['userId'], keep=6)

                    mysekai_game_data = mysekai_info['updatedResources']['userMysekaiGamedata']
                    if ctx.region in BD_MYSEKAI_REGIONS:
                        process_sensitive_cmd_source(mysekai_info)
                    ms_source = mysekai_info.get('source', '?')
                    if ms_local_source := mysekai_info.get('local_source'):
                        ms_source += f"({ms_local_source})"
                    ms_update_time = datetime.fromtimestamp(mysekai_info['upload_time'] / 1000)
                    ms_update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(ms_update_time, show_original_time=False)})"

                    with HSplit().set_content_align('lb').set_item_align('lb').set_sep(5):
                        hs = colored_text_box(
                            truncate(game_data['name'], 64),
                            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2, shadow_color=ADAPTIVE_SHADOW),
                        )
                        name_length = 0
                        for item in hs.items:
                            if isinstance(item, TextBox):
                                name_length += get_str_display_length(item.text)
                        ms_lv = mysekai_game_data['mysekaiRank']
                        ms_lv_text = f"MySekai Lv.{ms_lv}" if name_length <= 12 else f"MSLv.{ms_lv}"
                        TextBox(ms_lv_text, TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))

                    style = TextStyle(font=DEFAULT_FONT, size=16, color=BLACK)
                    TextBox(f"{ctx.region.upper()}: {user_id}", style)
                    TextBox(f"Suite更新时间: {update_time_text}", style)
                    TextBox(f"数据来源: {source} 获取模式: {mode}", style)
                    TextBox(f"Mysekai更新时间: {ms_update_time_text}", style)
                    TextBox(f"数据来源: {ms_source} 获取模式: {mode}", style)
            if err_msg:
                TextBox(f"获取数据失败: {err_msg}", TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(300)
    return f

# 获取mysekai上次资源刷新时间和刷新原因(natural/bdstart_{cid}/bdend_{cid})
def get_mysekai_last_refresh_time_and_reason(ctx: SekaiHandlerContext, dt: datetime=None) -> Tuple[datetime, str]:
    # 自然刷新
    h1, h2 = get_mysekai_refresh_hours(ctx)
    if h1 > h2:
        h1, h2 = h2, h1
    now = dt or datetime.now()
    last_refresh_time = None
    if now.hour < h1:
        last_refresh_time = now.replace(hour=h2, minute=0, second=0, microsecond=0)
        last_refresh_time -= timedelta(days=1)
    elif now.hour < h2:
        last_refresh_time = now.replace(hour=h1, minute=0, second=0, microsecond=0)
    else:
        last_refresh_time = now.replace(hour=h2, minute=0, second=0, microsecond=0)
    # 五周年后的生日掉落更新产生的刷新
    if is_fifth_anniversary(ctx.region):
        for cid in range(1, 27):
            dt = get_character_next_birthday_dt(ctx.region, cid, now - timedelta(days=1))
            start = dt - timedelta(days=3)
            end = dt
            if last_refresh_time < start <= now:
                return start, f'bdstart_{cid}'
            if last_refresh_time < end <= now:
                return end, f'bdend_{cid}'
    return last_refresh_time, 'natural'

# 从蓝图ID获取家具，不存在返回None
async def get_fixture_by_blueprint_id(ctx: SekaiHandlerContext, bid: int) -> Optional[dict]:
    blueprint = await ctx.md.mysekai_blueprints.find_by_id(bid)
    if blueprint and blueprint['mysekaiCraftType'] == 'mysekai_fixture':
        return await ctx.md.mysekai_fixtures.find_by_id(blueprint['craftTargetId'])
    return None

# 获取mysekai家具图标
async def get_mysekai_fixture_icon(ctx: SekaiHandlerContext, fixture: dict, color_idx: int = 0) -> Image.Image:
    img_cache_kwargs = {'use_img_cache': True, 'img_cache_max_res': MYSEKAI_ICON_CACHE_RES }
    ftype = fixture['mysekaiFixtureType']
    asset_name = fixture['assetbundleName']
    suface_type = fixture.get('mysekaiSettableLayoutType', None)
    color_count = 1
    if fixture.get('mysekaiFixtureAnotherColors'):
        color_count += len(fixture['mysekaiFixtureAnotherColors'])

    if ftype == "surface_appearance":
        suffix = "_1" if color_count == 1 else f"_{color_idx+1}"
        return await ctx.rip.img(f"mysekai/thumbnail/surface_appearance/{asset_name}/tex_{asset_name}_{suface_type}{suffix}.png", **img_cache_kwargs)
    else:
        suffix = f"_{color_idx+1}"
        return await ctx.rip.img(f"mysekai/thumbnail/fixture/{asset_name}{suffix}.png", **img_cache_kwargs)

# 获取mysekai资源图标
async def get_mysekai_res_icon(ctx: SekaiHandlerContext, key: str) -> Image.Image:
    img = UNKNOWN_IMG
    img_cache_kwargs = {'use_img_cache': True, 'img_cache_max_res': MYSEKAI_ICON_CACHE_RES }
    try:
        res_id = int(key.split("_")[-1])
        # mysekai材料
        if key.startswith("mysekai_material"):
            name = (await ctx.md.mysekai_materials.find_by_id(res_id))['iconAssetbundleName']
            img = await ctx.rip.img(f"mysekai/thumbnail/material/{name}.png", **img_cache_kwargs)
        # 普通材料
        elif key.startswith("material"):
            img = await ctx.rip.img(f"thumbnail/material_rip/material{res_id}.png", **img_cache_kwargs)
        # 道具
        elif key.startswith("mysekai_item"):
            name = (await ctx.md.mysekai_items.find_by_id(res_id))['iconAssetbundleName']
            img = await ctx.rip.img(f"mysekai/thumbnail/item/{name}.png", **img_cache_kwargs)
        # 家具（植物种子）
        elif key.startswith("mysekai_fixture"):
            name = (await ctx.md.mysekai_fixtures.find_by_id(res_id))['assetbundleName']
            try:
                img = await ctx.rip.img(f"mysekai/thumbnail/fixture/{name}_{res_id}_1.png", **img_cache_kwargs)
            except:
                img = await ctx.rip.img(f"mysekai/thumbnail/fixture/{name}_1.png", **img_cache_kwargs)
        # 唱片
        elif key.startswith("mysekai_music_record"):
            mid = (await ctx.md.mysekai_musicrecords.find_by_id(res_id))['externalId']
            name = (await ctx.md.musics.find_by_id(mid))['assetbundleName']
            img = await ctx.rip.img(f"music/jacket/{name}_rip/{name}.png", **img_cache_kwargs)
        # 蓝图
        elif key.startswith("mysekai_blueprint"):
            fixture = await get_fixture_by_blueprint_id(ctx, res_id)
            if not fixture: 
                logger.warning(f"{key}对应的不是家具")
                return UNKNOWN_IMG
            img = await get_mysekai_fixture_icon(ctx, fixture)

        else:
            raise Exception(f"未知的资源类型: {key}")

    except:
        logger.print_exc(f"获取{key}资源的图标失败")
        return UNKNOWN_IMG
    return img

# 获取mysekai天气颜色数据
def get_mysekai_phenomena_color_info(phenomena_id: int) -> dict:
    try:
        phenomena_colors = Config('sekai.mysekai_phenomena_colors').get(phenomena_id)
        ground = color_code_to_rgb(phenomena_colors['ground'])
        sky1 = color_code_to_rgb(phenomena_colors['sky1'])
        sky2 = color_code_to_rgb(phenomena_colors['sky2'])

        brightness = config.get('mysekai.map_brightness')
        if brightness > 1.0:
            ground = lerp_color(ground, WHITE, brightness - 1.0)
        else:
            ground = lerp_color(ground, BLACK, 1.0 - brightness)

        return {
            'ground': ground,
            'sky1': sky1,
            'sky2': sky2,
        }
    except Exception as e:
        return {
            'ground': (255, 255, 255, 255),
            'sky1': (200, 200, 255, 255),
            'sky2': (200, 200, 255, 255),
        }

# 合成mysekai资源位置地图图片
async def compose_mysekai_harvest_map_image(ctx: SekaiHandlerContext, harvest_map: dict, show_harvested: bool, phenomena_color_info: dict, mysekai_info: dict = None) -> Image.Image:
    site_id = harvest_map['mysekaiSiteId']
    site_image_info = Config('sekai.mysekai_site_map_image_info').get(site_id)
    site_image = ctx.static_imgs.get(site_image_info['image'])
    scale = MYSEKAI_HARVEST_MAP_IMAGE_SCALE_CFG.get()
    draw_w, draw_h = int(site_image.width * scale), int(site_image.height * scale)
    mid_x, mid_z = draw_w / 2, draw_h / 2
    grid_size = site_image_info['grid_size'] * scale
    offset_x, offset_z = site_image_info['offset_x'] * scale, site_image_info['offset_z'] * scale
    dir_x, dir_z = site_image_info['dir_x'], site_image_info['dir_z']
    rev_xz = site_image_info['rev_xz']

    crop_bbox = site_image_info.get('crop_bbox', None)
    if crop_bbox:
        crop_x, crop_y = crop_bbox[0], crop_bbox[1]
        crop_x2, crop_y2 = crop_bbox[0] + crop_bbox[2], crop_bbox[1] + crop_bbox[3]
        site_image = site_image.crop((crop_x, crop_y, crop_x2, crop_y2))
        draw_w = int(crop_bbox[2] * scale)
        draw_h = int(crop_bbox[3] * scale)
        offset_x -= crop_bbox[0] * scale
        offset_z -= crop_bbox[1] * scale

    # 根据天气调整地图颜色
    site_image = resize_keep_ratio(site_image, MYSEKAI_HARVEST_MAP_SITE_BG_IMAGE_DOWNSAMPLE, 'scale')
    site_image = multiply_image_by_color(site_image, phenomena_color_info['ground'])

    # 游戏资源位置映射到绘图位置
    def game_pos_to_draw_pos(x, z) -> Tuple[int, int]:
        if rev_xz:
            x, z = z, x
        x = x * grid_size * dir_x
        z = z * grid_size * dir_z
        x += mid_x + offset_x
        z += mid_z + offset_z
        x = max(0, min(x, draw_w))
        z = max(0, min(z, draw_h))
        return (int(x), int(z))

    # 获取资源掉落的位置
    all_res = {}
    for item in harvest_map['userMysekaiSiteHarvestResourceDrops']:
        res_type = item['resourceType']
        res_id = item['resourceId']
        res_key = f"{res_type}_{res_id}"
        res_status = item['mysekaiSiteHarvestResourceDropStatus']
        if not show_harvested and res_status != "before_drop": continue

        x, z = game_pos_to_draw_pos(item['positionX'], item['positionZ'])
        pkey = f"{x}_{z}"
        
        if pkey not in all_res:
            all_res[pkey] = {}
        if res_key not in all_res[pkey]:
            all_res[pkey][res_key] = {
                "id": res_id,
                "type": res_type,
                'x': x, 'z': z,
                'quantity': item['quantity'],
                'image': await get_mysekai_res_icon(ctx, res_key),
                'small_icon': False,
                'del': False,
            }
        else:
            all_res[pkey][res_key]['quantity'] += item['quantity']

    def is_birthday_drop(res):
        return res['type'] == "material" and 174 <= res['id'] <= 199

    for pkey in all_res:
        # 删除固定数量常规掉落(石头木头)
        is_birthday_sapling = False
        is_cotton_flower = False
        has_material_drop = False
        for res_key, item in all_res[pkey].items():
            if res_key in ['mysekai_material_1', 'mysekai_material_6'] and item['quantity'] == 6:
                all_res[pkey][res_key]['del'] = True
            if res_key in ['mysekai_material_21', 'mysekai_material_22']:
                is_cotton_flower = True
            if res_key.startswith("mysekai_material"):
                has_material_drop = True
            if is_birthday_drop(item) and item['quantity'] > 16:
                is_birthday_sapling = True
        # 设置是否需要使用小图标（1.非素材掉落 2.棉花的其他掉落 3. 生日树苗的其他掉落），以及隐藏非生日树苗的生日露滴
        for res_key, item in all_res[pkey].items():
            if not res_key.startswith("mysekai_material") and has_material_drop:
                all_res[pkey][res_key]['small_icon'] = True
            if is_cotton_flower and res_key not in ['mysekai_material_21', 'mysekai_material_22']:
                all_res[pkey][res_key]['small_icon'] = True
            if is_birthday_sapling:
                all_res[pkey][res_key]['small_icon'] = not is_birthday_drop(item)
            elif is_birthday_drop(item):
                all_res[pkey][res_key]['del'] = True

    # 获取所有资源点的位置
    harvest_points = []
    harvest_point_fid_pkeys: dict[int, str] = {}
    for item in harvest_map['userMysekaiSiteHarvestFixtures']:
        fid = item['mysekaiSiteHarvestFixtureId']
        fstatus = item['userMysekaiSiteHarvestFixtureStatus']
        if not show_harvested and fstatus != "spawned": 
            continue
        x, z = game_pos_to_draw_pos(item['positionX'], item['positionZ'])
        harvest_point_fid_pkeys[fid] = f"{x}_{z}"
        harvest_points.append({"id": fid, 'x': x, 'z': z})
    harvest_points.sort(key=lambda x: (x['z'], x['x']))

    # 获取需要的资源点图标
    for fid, pkey in harvest_point_fid_pkeys.items():
        if fid in harvest_point_image_offsets_cache:
            continue
        try: 
            harvest_fixture = (await ctx.md.mysekai_site_harvest_fixtures.find_by_id(fid))
            asset_name = harvest_fixture['assetbundleName']
            ftype = harvest_fixture['mysekaiSiteHarvestFixtureType']

            if ftype == 'birthday_plant':
                # 检查该位置的掉落以判断是哪个角色的生日花
                cid = None
                for res_key in all_res.get(pkey, {}):
                    res_id = int(res_key.rsplit("_", 1)[-1])
                    if 174 <= res_id <= 199:
                        cid = res_id - 173
                        break
                assert cid
                chara_eng_name = (await ctx.md.game_characters.find_by_id(cid))['givenNameEnglish'].lower()
                image = await ctx.rip.img(
                    f'mysekai/birthday/{chara_eng_name}_{datetime.now().year}/icon_refresh.png',
                    use_img_cache=True,
                    default=None
                )
                if image is None:
                    # 使用全透明图暂时替代
                    image = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
                point_img_size = 50 * scale
                xoffset = point_img_size * 0.15
                zoffset = 0
                image = resize_keep_ratio(image, point_img_size)

            else:
                rarity = harvest_fixture['mysekaiSiteHarvestFixtureRarityType']
                image = ctx.static_imgs.get(f"mysekai/harvest_fixture_icon/{rarity}/{asset_name}.png", (128, None))

                point_img_size = 160 * scale
                xoffset = 0
                zoffset = -point_img_size * 0.3  # 道具和资源点图标整体偏上，以让资源点对齐实际位置
                image = resize_keep_ratio(image, point_img_size)

            # 提前shrink以提高绘制效率
            # try:
            #     res = await run_in_pool(shrink_image, image, 5, 0)
            #     xoffset += res.extra_info['bbox'][0]
            #     zoffset += res.extra_info['bbox'][1]
            #     image = res.image
            # except Exception as e:
            #     logger.warning(f"资源点 {fid} 图标shrink失败: {get_exc_desc(e)}")

            offset = (
                int(-point_img_size * 0.5 + xoffset),
                int(-point_img_size * 0.5 + zoffset),
            )
            harvest_point_image_offsets_cache[fid] = (image, offset)
        except Exception as e:
            logger.warning(f"获取资源点 {fid} 图标失败: {get_exc_desc(e)}")
        

    # 绘制
    with Canvas(bg=FillBg(WHITE), w=draw_w, h=draw_h) as canvas:
        ImageBox(site_image, size=(draw_w, draw_h))

        # 绘制资源点
        for point in harvest_points:
            if point['id'] in harvest_point_image_offsets_cache:
                img, offset = harvest_point_image_offsets_cache[point['id']]
                ImageBox(img, use_alphablend=True).set_offset((point['x'] + offset[0], point['z'] + offset[1]))

        # 绘制出生点
        spawn_x, spawn_z = game_pos_to_draw_pos(0, 0)
        spawn_img = ctx.static_imgs.get("mysekai/mark.png")
        spawn_size = int(20 * scale)
        ImageBox(spawn_img, size=(spawn_size, spawn_size)).set_offset((spawn_x, spawn_z)).set_offset_anchor('c')

        @dataclass
        class ResDrawCall:
            res_id: int = None
            image: Image.Image = None
            quantity: int = None
            small_icon: bool = None
            size: int = None
            x: int = None
            z: int = None
            draw_order: int = None
            outline: Optional[Tuple[Tuple[int, int, int, int], int]] = None
            light_size: int = None
            attachment: Image.Image = None

        # 获取所有资源掉落绘制
        res_draw_calls: List[ResDrawCall] = []
        for pkey in all_res:
            pres = sorted(list(all_res[pkey].values()), key=lambda x: (-x['quantity'], x['id']))

            # 统计小图标和大图标的数量
            small_total, large_total = 0, 0
            for item in pres:
                if item['del']: continue
                if item['small_icon']:  small_total += 1
                else:                   large_total += 1
            small_idx, large_idx = 0, 0

            icon_zoffset = -160 * scale * 0.2

            for item in pres:
                if item['del']: continue
                if not item['image']: continue

                res_key = f"{item['type']}_{item['id']}"
                rarity = get_mysekai_res_rarity(res_key)
                call = ResDrawCall(
                    res_id=item['id'],
                    image=item['image'],
                    quantity=item['quantity'],
                    small_icon=item['small_icon'],
                )

                # 计算大小和位置
                large_size, small_size = 35 * scale, 17 * scale
                if item['type'] == 'mysekai_material' and item['id'] == 24:
                    large_size *= 1.5
                if item['type'] == 'mysekai_music_record':
                    large_size *= 1.5
                    has_music_record = mysekai_info and find_by(mysekai_info['updatedResources'].get('userMysekaiMusicRecords', []), 'mysekaiMusicRecordId', item['id']) is not None
                    call.attachment = ctx.static_imgs.get("mysekai/music_record.png") if has_music_record else None
                if item['small_icon']:
                    call.size = small_size
                    call.x = int(item['x'] + 0.5 * large_size * large_total - 0.6 * small_size)
                    call.z = int(item['z'] - 0.45 * large_size + 1.0 * small_size * small_idx + icon_zoffset)
                    small_idx += 1
                else:
                    call.size = large_size
                    call.x = int(item['x'] - 0.5 * large_size * large_total + large_size * large_idx)
                    call.z = int(item['z'] - 0.5 * large_size + icon_zoffset)
                    large_idx += 1

                # 对于高度可能超过的情况
                if call.z <= 0:
                    call.z += int(0.5 * large_size)

                # 绘制顺序 先从上到下再从左到右，小图标>稀有资源>其他
                if item['small_icon']:
                    call.draw_order = item['z'] * 100 + item['x'] + 1000000
                elif rarity == 2:
                    call.draw_order = item['z'] * 100 + item['x'] + 100000
                else:
                    call.draw_order = item['z'] * 100 + item['x']

                # 小图标和稀有资源添加边框
                if rarity == 2:
                    call.outline = ((255, 50, 50, 150), 2)
                elif item['small_icon']:
                    call.outline = ((50, 50, 255, 100), 1)

                # 稀有资源（非活动道具）添加发光
                if rarity == 2 and not res_key.startswith("material"):
                    if item['small_icon']:
                        call.light_size = int(45 * scale * config.get('mysekai.rare_res_light.small_size'))
                    else:
                        call.light_size = int(45 * scale * config.get('mysekai.rare_res_light.large_size'))

                res_draw_calls.append(call)
                    
        # 排序资源掉落
        res_draw_calls.sort(key=lambda x: x.draw_order)

        # 绘制稀有资源发光
        light_strength = (phenomena_color_info['ground'][0] 
                        + phenomena_color_info['ground'][1] 
                        + phenomena_color_info['ground'][2]) / (3 * 255)
        effect = config.get('mysekai.rare_res_light.map_brightness_effect')
        light_strength = 1.0 * (1.0 - effect) + light_strength * effect
        for call in res_draw_calls:
            if call.light_size:
                ImageBox(ctx.static_imgs.get("mysekai/light.png"), size=(call.light_size, call.light_size), 
                         use_alphablend=True, alpha_adjust=light_strength) \
                    .set_offset((int(call.x + call.size / 2), int(call.z + call.size / 2))).set_offset_anchor('c')

        # 绘制资源
        for call in res_draw_calls:
            with Frame().set_offset((call.x, call.z)).set_content_align('c'):
                ImageBox(call.image, size=(call.size, call.size), use_alphablend=True, alpha_adjust=0.8)
                if call.outline:
                    stroke, stroke_w = call.outline
                    Frame().set_bg(FillBg(stroke=stroke, stroke_width=stroke_w, fill=TRANSPARENT)) \
                        .set_size((call.size+stroke_w*2, call.size+stroke_w*2))

        # 绘制附件
        for call in res_draw_calls:
            if call.attachment:
                attach_size = int(call.size * 0.6)
                ImageBox(call.attachment, size=(attach_size, attach_size), use_alphablend=True) \
                    .set_offset((int(call.x + call.size - attach_size * 0.7), int(call.z + call.size - attach_size * 0.7)))

        # 绘制资源数量
        for call in res_draw_calls:
            if not call.small_icon:
                style = TextStyle(font=DEFAULT_BOLD_FONT, size=int(11 * scale), color=(50, 50, 50, 255))
                if call.quantity == 2:
                    style = TextStyle(font=DEFAULT_HEAVY_FONT, size=int(13 * scale), color=(200, 20, 0, 255))
                elif call.quantity > 2:
                    style = TextStyle(font=DEFAULT_HEAVY_FONT, size=int(13 * scale), color=(200, 20, 200, 255))
                x_offset, z_offset = -1, -1
                if call.quantity >= 10:
                    x_offset = 1
                    z_offset = int(call.size - 13 * scale) - 3
                TextBox(f"{call.quantity}", style).set_offset((call.x + x_offset, call.z + z_offset))

    return await canvas.get_img()

# 合成mysekai资源图片 返回图片列表
async def compose_mysekai_res_image(ctx: SekaiHandlerContext, qid: int, show_harvested: bool, check_time: bool) -> List[Image.Image]:
    with ProfileTimer("msr.get_basic_profile"):
        uid = get_player_bind_id(ctx)

        # 字节服额外验证
        if bd_limit_uid := get_bd_msr_limit_uid(ctx, qid):
            if bd_limit_uid != uid:
                raise MsrIdNotMatchException(f"""
你当前绑定ID与该指令限制ID（{process_hide_uid(ctx, bd_limit_uid, keep=6)}）不符，无法查询数据。发送\"/msr换绑\"可更换限制ID为当前绑定ID（一周内仅可换绑一次）
""".strip())

        basic_profile = await get_basic_profile(ctx, uid)

    with ProfileTimer("msr.get_mysekai_info"):
        mysekai_info, pmsg = await get_mysekai_info(ctx, qid, raise_exc=True)

    upload_time = datetime.fromtimestamp(mysekai_info['upload_time'] / 1000)
    if upload_time < get_mysekai_last_refresh_time_and_reason(ctx)[0] and check_time:
        raise ReplyException(f"数据已过期: {upload_time.strftime('%y-%m-%d %H:%M:%S')} from {mysekai_info.get('source', '?')}")
    
    assert_and_reply('userMysekaiHarvestMaps' in mysekai_info.get('updatedResources', {}), 
                     f"你的Mysekai抓包数据不完整，可以尝试退出游戏到标题界面后重新上传抓包数据")

    # 获取天气预报信息
    schedule = mysekai_info['mysekaiPhenomenaSchedules']
    phenom_ids = [item['mysekaiPhenomenaId'] for item in schedule]
    h1, h2 = get_mysekai_refresh_hours(ctx)

    # 判断当前天气
    current_hour = upload_time.hour
    if h1 < h2:
        phenom_idx = 1 if current_hour < h1 or current_hour >= h2 else 0
    else:
        phenom_idx = 1 if h2 <= current_hour < h1 else 0
    cur_phenom_id = phenom_ids[phenom_idx]
    phenom_color_info = get_mysekai_phenomena_color_info(cur_phenom_id)
    phenom_bg = FillBg(LinearGradient(c1=phenom_color_info['sky1'], c2=phenom_color_info['sky2'], p1=(0.25, 1.0), p2=(0.75, 0.0)))

    # 获取待绘制天气绘制参数
    phenom_start_dt = upload_time.replace(hour=h1, minute=0, second=0, microsecond=0)
    if current_hour < h1:
        phenom_start_dt -= timedelta(days=1)
    phenom_imgs, phenom_texts, phenom_bg_fills, phenom_text_fills = [], [], [], []

    async def add_phenom(refresh_reason: str, is_current: bool, start_dt: datetime, phenom_id=None):
        phenom_texts.append(start_dt.strftime('%H:%M'))
        if refresh_reason == 'natural':
            asset_name = (await ctx.md.mysekai_phenomenas.find_by_id(phenom_id))['iconAssetbundleName']
            phenom_imgs.append(await ctx.rip.img(f"mysekai/thumbnail/phenomena/{asset_name}.png"))
            phenom_bg_fills.append((255, 255, 255, 150) if is_current else (255, 255, 255, 75))
        else:
            refresh_reason = refresh_reason.split('_')
            bd_status, cid = refresh_reason[0], int(refresh_reason[1])
            img = await ctx.rip.img(f"thumbnail/material/material{cid+173}.png")    # 露滴道具
            img = img.resize((50, 50), Image.LANCZOS)
            if bd_status == "bdend":
                draw = ImageDraw.Draw(img)
                draw.line((0, 0, img.width, img.height), fill=(150, 150, 150, 255), width=5)
                draw.line((0, img.height, img.width, 0), fill=(150, 150, 150, 255), width=5)
            phenom_imgs.append(img)
            phenom_bg_fills.append((255, 255, 200, 255) if is_current else (255, 255, 200, 150))
        phenom_text_fills.append((0, 0, 0, 255) if is_current else (125, 125, 125, 255))

    for i, item in enumerate(schedule):
        i_is_current = (i == phenom_idx)  # 是否是当前天气（需要进一步判断生日刷新）
        # 判断到下一次自然刷新之前是否有生日刷新
        phenom_end_dt = phenom_start_dt + timedelta(hours=11, minutes=59)
        last_refresh_of_end, reason = get_mysekai_last_refresh_time_and_reason(ctx, phenom_end_dt)
        mid_is_current = None
        if last_refresh_of_end != phenom_start_dt:
            mid_is_current = upload_time >= last_refresh_of_end
            i_is_current = i_is_current and not mid_is_current
        await add_phenom('natural', i_is_current, phenom_start_dt, phenom_ids[i])
        if mid_is_current is not None:
            await add_phenom(reason, mid_is_current, last_refresh_of_end)
        phenom_start_dt += timedelta(hours=12)
    
    # 获取到访角色和对话记录
    chara_visit_data = mysekai_info['userMysekaiGateCharacterVisit']
    gate_id = chara_visit_data['userMysekaiGate']['mysekaiGateId']
    gate_level = chara_visit_data['userMysekaiGate']['mysekaiGateLevel']
    visit_cids = []
    reservation_cid = None
    for item in chara_visit_data['userMysekaiGateCharacters']:
        cgid = item['mysekaiGameCharacterUnitGroupId']
        group = await ctx.md.mysekai_game_character_unit_groups.find_by_id(cgid)
        if len(group) == 2:
            visit_cids.append(cgid)
            if item.get('isReservation'):
                reservation_cid = cgid
    read_cids = set()
    # 更新到访记录（只有当天的查询才让更新，所以只有check_time=True时才更新）
    if check_time:
        all_user_read_cids = file_db.get(f'{ctx.region}_mysekai_all_user_read_cids', {})
        if phenom_idx == 0:
            all_user_read_cids[str(uid)] = {
                "time": int(datetime.now().timestamp()),
                "cids": visit_cids
            }
            file_db.set(f'{ctx.region}_mysekai_all_user_read_cids', all_user_read_cids)
        else:
            read_info = all_user_read_cids.get(str(uid))
            if read_info:
                read_time = datetime.fromtimestamp(read_info['time'])
                if (datetime.now() - read_time).days < 1:
                    read_cids = set(read_info['cids'])

    # 计算资源数量
    site_res_num = { site_id : {} for site_id in SITE_ID_ORDER }
    harvest_maps = mysekai_info['updatedResources']['userMysekaiHarvestMaps']
    for site_map in harvest_maps:
        site_id = site_map['mysekaiSiteId']
        res_drops = site_map['userMysekaiSiteHarvestResourceDrops']
        for res_drop in res_drops:
            res_type = res_drop['resourceType']
            res_id = res_drop['resourceId']
            res_status = res_drop['mysekaiSiteHarvestResourceDropStatus']
            res_quantity = res_drop['quantity']
            res_key = f"{res_type}_{res_id}"

            if not show_harvested and res_status != "before_drop": continue

            if res_key not in site_res_num[site_id]:
                site_res_num[site_id][res_key] = 0
            site_res_num[site_id][res_key] += res_quantity

    # 获取资源地图图片
    site_imgs = {
        site_id: await ctx.rip.img(f"mysekai/site/sitemap/texture_rip/img_harvest_site_{site_id}.png") 
        for site_id in site_res_num
    }

    # 排序
    site_res_num = [(site_id, site_res_num[site_id]) for site_id in SITE_ID_ORDER]
    site_harvest_map_imgs = []
    def get_res_order(item):
        key, num = item
        rarity = get_mysekai_res_rarity(key)
        if rarity == 2:
            num -= 1000000
        elif rarity == 1:
            num -= 100000
        return (-num, key)
    for i in range(len(site_res_num)):
        site_id, res_num = site_res_num[i]
        res_nums = sorted([(item, get_res_order(item)) for item in res_num.items()], key=lambda x: x[1])
        site_res_num[i] = (site_id, [x[0] for x in res_nums])

    # 绘制资源位置图
    with ProfileTimer("msr.compose_harvest_map"):
        for i in range(len(site_res_num)):
            site_id, res_num = site_res_num[i]
            site_harvest_map = find_by(harvest_maps, "mysekaiSiteId", site_id)
            if site_harvest_map:
                site_harvest_map_imgs.append(compose_mysekai_harvest_map_image(ctx, site_harvest_map, show_harvested, phenom_color_info, mysekai_info))
        site_harvest_map_imgs = await asyncio.gather(*site_harvest_map_imgs)
   
    try: 
        phenom_bg_img = ctx.static_imgs.get(f"mysekai/phenom_bg/{cur_phenom_id}.png")
        bg = ImageBg(phenom_bg_img)
    except: 
        bg = SEKAI_BLUE_BG

    def draw_watermark(size):
        if ctx.region in BD_MYSEKAI_REGIONS:
            TextBox("禁止将该图片转发到其他群聊或社交平台", 
                    TextStyle(
                        font=DEFAULT_BOLD_FONT, size=size, color=(255, 255, 255, 150), 
                        use_shadow=True, shadow_color=(50, 50, 50, 150), shadow_offset=2,
                    ))
    
    # 绘制数量图
    with Canvas(bg=bg).set_padding(BG_PADDING).set_content_align('c') as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:

            with HSplit().set_sep(28).set_content_align('lb'):
                await get_mysekai_info_card(ctx, mysekai_info, basic_profile, pmsg)

                # 天气预报
                with HSplit().set_sep(8).set_content_align('lb').set_bg(roundrect_bg()).set_padding(10):
                    for i in range(len(phenom_imgs)):
                        with Frame():
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(5).set_bg(roundrect_bg(fill=phenom_bg_fills[i])).set_padding(8):
                                TextBox(phenom_texts[i], TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=phenom_text_fills[i])).set_w(60).set_content_align('c')
                                ImageBox(phenom_imgs[i], size=(None, 50), use_alphablend=True) 
            
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg()):
                # 到访角色列表
                with HSplit().set_bg(roundrect_bg()).set_content_align('c').set_item_align('c').set_padding(16).set_sep(16):
                    gate_icon = ctx.static_imgs.get(f'mysekai/gate_icon/gate_{gate_id}.png')
                    with Frame().set_size((64, 64)).set_margin((16, 0)).set_content_align('rb'):
                        ImageBox(gate_icon, size=(64, 64), use_alphablend=True, shadow=True).set_offset((0, -4))
                        TextBox(
                            f"Lv.{gate_level}", 
                            TextStyle(DEFAULT_FONT, 16, UNIT_COLORS[gate_id-1], use_shadow=True, shadow_color=ADAPTIVE_SHADOW),
                        ).set_content_align('c').set_offset((4, 2))

                    for cid in visit_cids:
                        chara_icon = await get_character_sd_image(cid)
                        with Frame().set_content_align('lt'):
                            ImageBox(chara_icon, size=(80, None), use_alphablend=True)
                            if cid not in read_cids:
                                gcid = (await ctx.md.game_character_units.find_by_id(cid))['gameCharacterId']
                                chara_item_icon = await ctx.rip.img(f"mysekai/item_preview/material/item_memoria_{gcid}.png")
                                ImageBox(chara_item_icon, size=(40, None), use_alphablend=True, shadow=True).set_offset((80 - 40, 80 - 40))
                            if cid == reservation_cid:
                                invitation_icon = ctx.static_imgs.get('mysekai/invitationcard.png')
                                ImageBox(invitation_icon, size=(25, None), use_alphablend=True, shadow=True).set_offset((10, 80 - 30))
                    Spacer(w=16, h=1)

                # 每个地区的资源
                for site_id, res_num in site_res_num:
                    if not res_num: continue
                    with HSplit().set_bg(roundrect_bg()).set_content_align('lt').set_item_align('lt').set_padding(16).set_sep(16):
                        ImageBox(site_imgs[site_id], size=(None, 85))
                        
                        with Grid(col_count=5).set_content_align('lt').set_sep(hsep=5, vsep=5):
                            for res_key, res_quantity in res_num:
                                res_img = await get_mysekai_res_icon(ctx, res_key)
                                if not res_img: continue
                                with HSplit().set_content_align('l').set_item_align('l').set_sep(5):
                                    text_color = (100, 100, 100) 
                                    rarity = get_mysekai_res_rarity(res_key)
                                    if rarity == 2:
                                        text_color = (200, 50, 0)
                                    elif rarity == 1:
                                        text_color = (50, 0, 200)

                                    if res_key.startswith("mysekai_music_record"):
                                        record_id = int(res_key.split("_")[-1])
                                        has_music_record = find_by(mysekai_info['updatedResources'].get('userMysekaiMusicRecords', []), 'mysekaiMusicRecordId', record_id) is not None
                                        with Frame().set_content_align('rb'):
                                            ImageBox(res_img, size=(40, 40), use_alphablend=True)
                                            if has_music_record:
                                                music_record_icon = ctx.static_imgs.get('mysekai/music_record.png')
                                                ImageBox(music_record_icon, size=(25, 25), use_alphablend=True, shadow=True).set_offset((5, 5))
                                    else:
                                        ImageBox(res_img, size=(40, 40), use_alphablend=True)
                                    TextBox(
                                        f"{res_quantity}", 
                                        TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=text_color,
                                                    use_shadow=True, shadow_color=WHITE),
                                    ).set_w(80).set_content_align('l')
        draw_watermark(30)

    # 绘制位置图
    with Canvas(bg=phenom_bg).set_padding(BG_PADDING).set_content_align('c') as canvas2:
        with Grid(col_count=2, vertical=True).set_sep(16, 16).set_padding(0):
            for img in site_harvest_map_imgs:
                ImageBox(img)
        draw_watermark(60)
    
    if ctx.region not in BD_MYSEKAI_REGIONS:
        add_watermark(canvas)
        add_watermark(canvas2, text=DEFAULT_WATERMARK_CFG.get() + ", map view from MiddleRed")

    with ProfileTimer("msr.get_imgs"):
        return await asyncio.gather(canvas.get_img(), canvas2.get_img())

# 获取mysekai家具类别的名称和图片
async def get_mysekai_fixture_genre_name_and_image(ctx: SekaiHandlerContext, gid: int, is_main_genre: bool) -> Tuple[str, Image.Image]:
    if is_main_genre:
        genre = await ctx.md.mysekai_fixture_maingenres.find_by_id(gid)
    else:
        genre = await ctx.md.mysekai_fixture_subgenres.find_by_id(gid)
    asset_name = genre['assetbundleName']
    image = await ctx.rip.img(f"mysekai/icon/category_icon/{asset_name}.png", use_img_cache=True)
    return genre['name'], image

# 合成mysekai家具列表图片
async def compose_mysekai_fixture_list_image(
    ctx: SekaiHandlerContext, 
    qid: int, 
    show_id: bool, 
    only_craftable: bool, 
) -> Image.Image:
    # 获取玩家已获得的蓝图对应的家具ID
    obtained_fids = None
    if qid:
        uid = get_player_bind_id(ctx)
        basic_profile = await get_basic_profile(ctx, uid)
        mysekai_info, mimsg = await get_mysekai_info(ctx, qid, raise_exc=True)

        assert_and_reply(
            'updatedResources' in mysekai_info,
            "你的Mysekai抓包数据不完整，请尝试退出游戏到标题界面后重新上传抓包数据"
        )
        assert_and_reply(
            'userMysekaiBlueprints' in mysekai_info['updatedResources'],
            "你的抓包数据来源没有提供蓝图数据"
        )

        obtained_fids = set()
        for item in mysekai_info['updatedResources']['userMysekaiBlueprints']:
            bid = item['mysekaiBlueprintId']
            blueprint = await ctx.md.mysekai_blueprints.find_by_id(bid)
            if blueprint and blueprint['mysekaiCraftType'] == 'mysekai_fixture':
                fid = blueprint['craftTargetId']
                obtained_fids.add(fid)

    # 获取所有可合成的家具ID
    craftable_fids = None
    if only_craftable:
        craftable_fids = set()
        for item in await ctx.md.mysekai_blueprints.get():
            if item['mysekaiCraftType'] =='mysekai_fixture':
                craftable_fids.add(item['id'])

    # 记录收集进度
    total_obtained, total_all = 0, 0
    main_genre_obtained, main_genre_all = {}, {}
    sub_genre_obtained, sub_genre_all = {}, {}

    # 获取需要的家具信息
    fixtures = {}
    all_fixtures = []
    birthday_cids = {}
    for item in await ctx.md.mysekai_fixtures.get():
        fid = item['id']
        if craftable_fids and fid not in craftable_fids:
            continue
        
        fname = item['name']
        is_birthday = False
        for chara in await ctx.md.game_characters.get():
            if fname.endswith(f"（{chara['givenName']}）"):
                is_birthday = True
                birthday_cids[fid] = chara['id']
                break
                
        ftype = item['mysekaiFixtureType']
        main_genre_id = item['mysekaiFixtureMainGenreId']
        sub_genre_id = item.get('mysekaiFixtureSubGenreId', -1)
        color_count = 1
        if item.get('mysekaiFixtureAnotherColors'):
            color_count += len(item['mysekaiFixtureAnotherColors'])

        if ftype == "gate": continue

        # 处理错误归类
        if fid == 4: 
            sub_genre_id = 14
        if main_genre_id in (4, 5, 7, 8, 9, 10, 11, 12, 13):
            sub_genre_id = -1

        if main_genre_id not in fixtures:
            fixtures[main_genre_id] = {}
        if sub_genre_id not in fixtures[main_genre_id]:
            fixtures[main_genre_id][sub_genre_id] = []

        obtained = not obtained_fids or fid in obtained_fids
        fixtures[main_genre_id][sub_genre_id].append((fid, obtained))
        all_fixtures.append(item)

        # 统计收集进度（生日家具不统计）
        if not is_birthday:
            total_all += 1
            total_obtained += obtained
            if main_genre_id not in main_genre_all:
                main_genre_all[main_genre_id] = 0
                main_genre_obtained[main_genre_id] = 0
            main_genre_all[main_genre_id] += 1
            main_genre_obtained[main_genre_id] += obtained
            if main_genre_id not in sub_genre_all:
                sub_genre_all[main_genre_id] = {}
                sub_genre_obtained[main_genre_id] = {}
            if sub_genre_id not in sub_genre_all[main_genre_id]:
                sub_genre_all[main_genre_id][sub_genre_id] = 0
                sub_genre_obtained[main_genre_id][sub_genre_id] = 0
            sub_genre_all[main_genre_id][sub_genre_id] += 1
            sub_genre_obtained[main_genre_id][sub_genre_id] += obtained
    
    # 获取家具图标
    fixture_icons = {}
    result = await batch_gather(*[get_mysekai_fixture_icon(ctx, item) for item in all_fixtures])
    for fixture, icon in zip(all_fixtures, result):
        fixture_icons[fixture['id']] = icon

    text_color = (75, 75, 75)

    # 绘制
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                if qid:
                    await get_mysekai_info_card(ctx, mysekai_info, basic_profile, mimsg)

            if qid and only_craftable:
                TextBox(f"总收集进度（不含生日家具）: {total_obtained}/{total_all} ({total_obtained/total_all*100:.1f}%)", 
                        TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color)) \
                        .set_padding(16).set_bg(roundrect_bg())

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
                # 一级分类
                for main_genre_id in sorted(fixtures.keys()):
                    if count_dict(fixtures[main_genre_id], 2) == 0: continue

                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg()).set_padding(8):
                        # 标签
                        main_genre_name, main_genre_image = await get_mysekai_fixture_genre_name_and_image(ctx, main_genre_id, True)
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                            ImageBox(main_genre_image, size=(None, 30), use_alphablend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                            TextBox(main_genre_name, TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=text_color))
                            if qid and only_craftable:
                                a, b = main_genre_obtained[main_genre_id], main_genre_all[main_genre_id]
                                TextBox(f"{a}/{b} ({a/b*100:.1f}%)", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=text_color))

                        # 二级分类
                        for sub_genre_id in sorted(fixtures[main_genre_id].keys()):
                            if len(fixtures[main_genre_id][sub_genre_id]) == 0: continue

                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8):
                                # 标签
                                if sub_genre_id != -1 and len(fixtures[main_genre_id]) > 1:  # 无二级分类或只有1个二级分类的不加标签
                                    sub_genre_name, sub_genre_image = await get_mysekai_fixture_genre_name_and_image(ctx, sub_genre_id, False)
                                    with HSplit().set_content_align('c').set_item_align('c').set_sep(5):
                                        ImageBox(sub_genre_image, size=(None, 23), use_alphablend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                                        TextBox(sub_genre_name, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=text_color))
                                        if qid and only_craftable:
                                            a, b = sub_genre_obtained[main_genre_id][sub_genre_id], sub_genre_all[main_genre_id][sub_genre_id]
                                            TextBox(f"{a}/{b} ({a/b*100:.1f}%)", TextStyle(font=DEFAULT_FONT, size=12, color=text_color))

                                # 绘制单个家具
                                def draw_single_fid(fid: int, obtained: bool):
                                    f_sz = 30
                                    image = fixture_icons.get(fid)
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(0):
                                        with Frame().set_content_align('rt'):
                                            ImageBox(image, size=(f_sz, f_sz), use_alphablend=True)
                                            if cid := birthday_cids.get(fid):
                                                ImageBox(get_chara_icon_by_chara_id(cid), size=(12, 12), use_alphablend=False)
                                            if not obtained:
                                                Spacer(w=f_sz, h=f_sz).set_bg(RoundRectBg(fill=(0,0,0,80), radius=2))
                                        if show_id:
                                            TextBox(f"{fid}", TextStyle(font=DEFAULT_FONT, size=10, color=(50, 50, 50)))

                                # 家具列表
                                COL_COUNT, cur_idx = 20, 0
                                sep = 3
                                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(sep):
                                    while True:
                                        cur_x = 0
                                        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(sep):
                                            while cur_x < COL_COUNT:
                                                fids, obtaineds = fixtures[main_genre_id][sub_genre_id][cur_idx]
                                                draw_single_fid(fids, obtaineds)
                                                cur_x += 1
                                                cur_idx += 1
                                                if cur_idx >= len(fixtures[main_genre_id][sub_genre_id]):
                                                    break   
                                        if cur_idx >= len(fixtures[main_genre_id][sub_genre_id]):
                                            break                       

    add_watermark(canvas)

    # 缓存非玩家查询的msf
    cache_key = None
    if not qid and show_id and not only_craftable:
        cache_key = f"{ctx.region}_msf"

    return await canvas.get_img(cache_key=cache_key)

# 获取mysekai照片和拍摄时间
async def get_mysekai_photo_and_time(ctx: SekaiHandlerContext, qid: int, seq: int) -> Tuple[Image.Image, datetime]:
    qid, seq = int(qid), int(seq)
    assert_and_reply(seq != 0, "请输入正确的照片编号（从1或-1开始）")

    mysekai_info, pmsg = await get_mysekai_info(ctx, qid, raise_exc=True)
    photos = mysekai_info['updatedResources']['userMysekaiPhotos']
    if seq < 0:
        seq = len(photos) + seq + 1
    assert_and_reply(seq <= len(photos), f"照片编号大于照片数量({len(photos)})")
    
    photo = photos[seq-1]
    photo_time = datetime.fromtimestamp(photo['obtainedAt'] / 1000)

    url = get_gameapi_config(ctx).mysekai_photo_api_url
    assert_and_reply(url, f"暂不支持查询 {ctx.region} 的MySekai照片")

    image_bytes = await request_gameapi(url, data_type='bytes', json=photo)
    return Image.open(io.BytesIO(image_bytes)), photo_time

# 从本地的my.sekai.run网页html提取数据
async def load_mysekairun_data(ctx: SekaiHandlerContext):
    global mysekairun_friendcode_data, mysekairun_friendcode_mtime
    path = f"{SEKAI_ASSET_DIR}/mysekairun/{ctx.region}.html"
    if not os.path.exists(path):
        logger.warning(f"my.sekai.run 文件不存在，取消加载")
        return
    if mysekairun_friendcode_mtime and os.path.getmtime(path) == mysekairun_friendcode_mtime:
        return
    mysekairun_friendcode_data = {}

    from bs4 import BeautifulSoup
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")
        result = soup.find("div", id="result")
        for genre in result.find_all("div"):
            table = genre.find("table")
            if not table: continue
            tbody = table.find("tbody")
            if not tbody: continue
            for tr in tbody.find_all("tr"):
                try:
                    _, name, ids = tr.find_all("td")
                    name = str(name.text).strip()
                    ids = list(ids.stripped_strings)
                    mysekairun_friendcode_data[name] = ids
                except:
                    pass

    mysekairun_friendcode_mtime = os.path.getmtime(path)
    logger.info(f"my.sekai.run 数据加载完成: 加载了 {len(mysekairun_friendcode_data)} 个家具")

# 获取mysekai家具好友码，返回（好友码，来源）
async def get_mysekai_fixture_friend_codes(ctx: SekaiHandlerContext, fid: int) -> Tuple[List[str], str]:
    if ctx.region not in ['jp']:
        return [], ""

    fixture = await ctx.md.mysekai_fixtures.find_by_id(fid)
    assert_and_reply(fixture, f"家具{fid}不存在")

    try:
        data = await sekai8823_friendcode_data.get()
        friend_codes = find_by(data['fixtures'], 'id', fid)['friendCodes']
        return friend_codes, "sekai.8823.eu.org"
    except Exception as e:
        logger.warning(f"从 sekai.8823.eu.org 获取家具 {fid} 好友码失败: {e}")

    try:
        await load_mysekairun_data(ctx)
        fname = fixture['name']
        friend_codes = mysekairun_friendcode_data.get(fname.strip())
        return friend_codes, "my.sekai.run"
    except Exception as e:
        logger.warning(f"从 my.sekai.run 获取家具 {fid} 好友码失败: {e}")
    
    return [], ""
    
# 获取mysekai家具详情卡片控件 返回Widget
async def get_mysekai_fixture_detail_image_card(ctx: SekaiHandlerContext, fid: int) -> Widget:
    await load_mysekairun_data(ctx)

    fixture = await ctx.md.mysekai_fixtures.find_by_id(fid)
    assert_and_reply(fixture, f"家具{ctx.region.upper()}-{fid}不存在")

    ## 获取基本信息
    fname = fixture['name']

    translated_name = None
    if ctx.region in NEED_TRANSLATE_REGIONS:
        for r in TRANSLATED_REGIONS:
            if r in MYSEKAI_REGIONS:
                if f := await SekaiHandlerContext.from_region(r).md.mysekai_fixtures.find_by_id(fid):
                    translated_name = f['name']
                    break
        if not translated_name:
            translated_name = await translate_text(fname, additional_info="要翻译的内容是家具/摆设的名字")

    fsize = fixture['gridSize']
    is_assemble = fixture.get('isAssembled', False)
    is_disassembled = fixture.get('isDisassembled', False)
    is_character_action = fixture.get('isGameCharacterAction', False)
    is_player_action = fixture.get('mysekaiFixturePlayerActionType', "no_action") != "no_action"
    # 配色
    if colors := fixture.get('mysekaiFixtureAnotherColors'):
        fcolorcodes = [fixture["colorCode"]] + [item['colorCode'] for item in colors]
    else:
        fcolorcodes = [None]
    # 类别
    main_genre_id = fixture['mysekaiFixtureMainGenreId']
    sub_genre_id = fixture.get('mysekaiFixtureSubGenreId')
    main_genre_name, main_genre_image = await get_mysekai_fixture_genre_name_and_image(ctx, main_genre_id, True)
    if sub_genre_id:
        sub_genre_name, sub_genre_image = await get_mysekai_fixture_genre_name_and_image(ctx, sub_genre_id, False)
    # 图标
    fimgs = [await get_mysekai_fixture_icon(ctx, fixture, i) for i in range(len(fcolorcodes))]
    # 标签
    tags = []
    for key, val in fixture.get('mysekaiFixtureTagGroup', {}).items():
        if key != 'id':
            tag = await ctx.md.mysekai_fixture_tags.find_by_id(val)
            tags.append(tag['name'])
    # 交互角色
    react_chara_group_imgs = [[] for _ in range(10)]  # react_chara_group_imgs[交互人数]=[[id1, id2], [id3, id4], ...]]
    has_chara_react = False
    react_data = await ctx.rip.json(
        'mysekai/system/fixture_reaction_data_rip/fixture_reaction_data.asset', 
        cache_expire_secs=60*60*24, 
    )
    if react_data:
        react_data = find_by(react_data['FixturerRactions'], 'FixtureId', fid)
        if react_data:
            for item in react_data['ReactionCharacter']:
                chara_imgs = [await get_chara_icon_by_chara_unit_id(ctx, cuid) for cuid in item['CharacterUnitIds']]
                react_chara_group_imgs[len(chara_imgs)].append(chara_imgs)
                has_chara_react = True
    # 制作材料
    blueprint = await ctx.md.mysekai_blueprints.find_by("craftTargetId", fid, mode='all')
    blueprint = find_by(blueprint, "mysekaiCraftType", "mysekai_fixture")
    if blueprint:
        is_sketchable = blueprint['isEnableSketch']
        can_obtain_by_convert = blueprint['isObtainedByConvert']
        craft_count_limit = blueprint.get('craftCountLimit')
        cost_materials = await ctx.md.mysekai_blueprint_material_cost.find_by("mysekaiBlueprintId", blueprint['id'], mode='all')
        cost_materials = [(
            await get_mysekai_res_icon(ctx, f"mysekai_material_{item['mysekaiMaterialId']}"),
            item['quantity']
        ) for item in cost_materials]
    # 回收材料
    recycle_materials = []
    only_diassemble_materials = await ctx.md.mysekai_fixture_only_disassemble_materials.find_by("mysekaiFixtureId", fid, mode='all')
    if only_diassemble_materials:
        recycle_materials = [(
            await get_mysekai_res_icon(ctx, f"mysekai_material_{item['mysekaiMaterialId']}"),
            item['quantity']
        ) for item in only_diassemble_materials]
    elif blueprint and is_disassembled:
        recycle_materials = [(img, quantity // 2) for img, quantity in cost_materials if quantity > 1]
    # 抄写好友码
    friendcodes, friendcode_source = await get_mysekai_fixture_friend_codes(ctx, fid)

    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(25, 25, 25))
    text_style = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50))

    w = 600
    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16) as vs:
        # 标题
        title_text = f"【{ctx.region.upper()}-{fid}】{fname}"
        if translated_name: title_text += f" ({translated_name})"
        TextBox(title_text, TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(0, 0, 0)), use_real_line_count=True).set_padding(8).set_bg(roundrect_bg()).set_w(w+16)
        # 缩略图列表
        with Grid(col_count=min(5, len(fcolorcodes))).set_content_align('c').set_item_align('c').set_sep(8, 4).set_padding(8).set_bg(roundrect_bg()).set_w(w+16):
            for color_code, img in zip(fcolorcodes, fimgs):
                with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                    ImageBox(img, size=(None, 100), use_alphablend=True, shadow=True)
                    if color_code:
                        Frame().set_size((100, 20)).set_bg(RoundRectBg(
                            fill=color_code_to_rgb(color_code), 
                            radius=4,
                            stroke=(150, 150, 150, 255), stroke_width=3,
                        ))
        # 基本信息
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8).set_bg(roundrect_bg()).set_w(w+16):
            with HSplit().set_content_align('c').set_item_align('c').set_sep(2):
                TextBox(f"【类型】", text_style)
                ImageBox(main_genre_image, size=(None, text_style.size+2), use_alphablend=True).set_bg(RoundRectBg(fill=(150,150,150,255), radius=2))
                TextBox(main_genre_name, text_style)
                if sub_genre_id:
                    TextBox(f" > ", text_style)
                    ImageBox(sub_genre_image, size=(None, text_style.size+2), use_alphablend=True).set_bg(RoundRectBg(fill=(150,150,150,255), radius=2))
                    TextBox(sub_genre_name, text_style)

            with HSplit().set_content_align('c').set_item_align('c').set_sep(8):    
                TextBox(f"【大小】长x宽x高={fsize['width']}x{fsize['depth']}x{fsize['height']}", text_style)
                TextBox(f"【放置消耗】{fixture.get('firstPutCost', 0)} (首次) / {fixture.get('secondPutCost', 0)} (重复)", text_style)
            
            with HSplit().set_content_align('c').set_item_align('c').set_sep(2):
                TextBox(f"【🔨可制作】" if is_assemble else "【❌不可制作】", text_style)
                TextBox(f"【♻️可回收】" if is_disassembled else "【❌不可回收】", text_style)
                TextBox(f"【👋玩家可交互】" if is_player_action else "【❌玩家不可交互】", text_style)
                TextBox(f"【🎡角色可交互】" if is_character_action else "【❌角色无交互】", text_style)

            if blueprint:
                with HSplit().set_content_align('c').set_item_align('c').set_sep(2):
                    TextBox(f"【📝蓝图可抄写】" if is_sketchable else "【蓝图不可抄写】", text_style)
                    TextBox(f"【🎁蓝图可合成】" if can_obtain_by_convert else "【蓝图不可合成】", text_style)
                    TextBox(f"【最多制作{craft_count_limit}次】" if craft_count_limit else "【无制作次数限制】", text_style)

        # 制作材料
        if blueprint and cost_materials:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg()):
                TextBox("制作材料", title_style).set_w(w)
                with Grid(col_count=8).set_content_align('lt').set_sep(6, 6):
                    for img, quantity in cost_materials:
                        with VSplit().set_content_align('c').set_item_align('c').set_sep(2):
                            ImageBox(img, size=(50, 50), use_alphablend=True)
                            TextBox(f"x{quantity}", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(25, 25, 25)))

        # 回收材料
        if recycle_materials:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg()):
                TextBox("回收材料", title_style).set_w(w)
                with Grid(col_count=8).set_content_align('lt').set_sep(6, 6):
                    for img, quantity in recycle_materials:
                        with VSplit().set_content_align('c').set_item_align('c').set_sep(2):
                            ImageBox(img, size=(50, 50), use_alphablend=True)
                            TextBox(f"x{quantity}", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(25, 25, 25)))

        # 交互角色
        if has_chara_react:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg()):
                TextBox("角色互动", title_style).set_w(w)
                with Flow().set_content_align('lt').set_item_align('lt').set_sep(6, 6).set_w(w):
                    for chara_group_imgs in react_chara_group_imgs:
                        chara_num = len(chara_group_imgs[0]) if chara_group_imgs else None
                        if not chara_num: continue
                        for imgs in chara_group_imgs:
                            with HSplit().set_content_align('c').set_item_align('c').set_sep(4).set_padding(4).set_bg(roundrect_bg(radius=8)):
                                for img in imgs:
                                    ImageBox(img, size=(40, 40), use_alphablend=True)

        # 标签
        if tags:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg()):
                TextBox("标签", title_style).set_w(w)
                with Flow().set_content_align('lt').set_item_align('lt').set_sep(2, 4).set_w(w):
                    for tag in tags:
                        TextBox(f"【{tag}】", text_style)

        # 抄写好友码
        if friendcodes and is_sketchable:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg()):
                with HSplit().set_content_align('lb').set_item_align('lb').set_sep(8).set_w(w):
                    TextBox("抄写蓝图可前往", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50)))
                    TextBox(f"(数据来自{friendcode_source})", TextStyle(font=DEFAULT_FONT, size=16, color=(75, 75, 75)))
                friendcodes = random.sample(friendcodes, min(4, len(friendcodes)))
                with Flow().set_content_align('lt').set_item_align('lt').set_sep(24, 4).set_w(w):
                    for code in friendcodes:
                        TextBox(code, text_style)

    return vs

# 获取mysekai家具详情
async def compose_mysekai_fixture_detail_image(ctx: SekaiHandlerContext, fids: List[int]) -> Image.Image:
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
            for fid in fids:
                await get_mysekai_fixture_detail_image_card(ctx, fid)
    add_watermark(canvas)
    return await canvas.get_img()

# 合成mysekai门升级材料图片
async def compose_mysekai_door_upgrade_image(ctx: SekaiHandlerContext, qid: int, spec_gate_id: int = None) -> Image.Image:
    GATE_MAX_LV = 40

    profile = None
    if qid:
        profile, pmsg = await get_detailed_profile(
            ctx, 
            qid, 
            filter=get_detailed_profile_card_filter('userMysekaiMaterials','userMysekaiGates'),
            raise_exc=True, ignore_hide=True)

    # 获取玩家的材料
    user_materials = {}
    if profile:
        lv_materials = profile.get('userMysekaiMaterials', [])
        user_materials = {item['mysekaiMaterialId']: item['quantity'] for item in lv_materials}

    # 获取每级升级材料
    gate_materials = {}
    for item in await ctx.md.mysekai_gate_material_groups.get():
        gid = item['groupId'] // 1000
        level = item['groupId'] % 1000
        mid = item['mysekaiMaterialId']
        quantity = item['quantity']
        if gid not in gate_materials:
            gate_materials[gid] = [[] for _ in range(GATE_MAX_LV)]
        gate_materials[gid][level - 1].append({
            'mid': mid,
            'quantity': quantity,
            'color': (50, 50, 50),
            'sum_quantity': None,
        })

    # 获取指定lv
    spec_lvs = {}
    if profile:
        gates = profile.get('userMysekaiGates', [])
        if not gates:
            raise ReplyException("查询不到你的烤森门（需要更新Suite抓包数据）")
        for item in gates:
            gid = item['mysekaiGateId']
            lv = item['mysekaiGateLevel']
            spec_lvs[gid] = lv
        if not spec_gate_id:
            # 如果没有指定门，则使用最大等级的门
            for gid, lv in spec_lvs.items():
                if lv > spec_lvs.get(spec_gate_id, 0) and lv != GATE_MAX_LV:
                    spec_gate_id = gid
            if not spec_gate_id:
                raise ReplyException("你的所有门已经满级")

    # 根据指定lv截断
    for gid, lv_materials in gate_materials.items():
        spec_lv = spec_lvs.get(gid, 0)
        gate_materials[gid] = lv_materials[spec_lv:]

    # 指定门
    if spec_gate_id:
        gate_materials = {spec_gate_id: gate_materials[spec_gate_id]}
        if spec_lvs.get(spec_gate_id, 0) == GATE_MAX_LV:
            raise ReplyException("查询的门已经满级")

    # 统计总和
    for gid, lv_materials in gate_materials.items():
        sum_materials = {}
        for items in lv_materials:
            for item in items:
                mid = item['mid']
                quantity = item['quantity']
                if mid not in sum_materials:
                    sum_materials[mid] = 0
                sum_materials[mid] += quantity
                item['sum_quantity'] = sum_materials[mid]

    red_color = (200, 0, 0)
    green_color = (0, 200, 0)

    # 计算玩家材料和需要的材料文本
    if profile:
        for gid, lv_materials in gate_materials.items():
            for items in lv_materials:
                for item in items:
                    mid = item['mid']
                    sum_quantity = item['sum_quantity']
                    user_quantity = user_materials.get(mid, 0)
                    if user_quantity >= 10000:
                        user_quantity_text = f"{user_quantity // 1000}k"
                    elif user_quantity >= 1000:
                        user_quantity_text = f"{user_quantity // 1000}k{user_quantity % 1000 // 100}"
                    else:
                        user_quantity_text = str(user_quantity)
                    if user_quantity >= sum_quantity:
                        item['color'] = green_color
                        item['sum_quantity'] = f"{user_quantity_text}/{sum_quantity}"
                    else:
                        item['color'] = red_color
                        item['sum_quantity'] = f"{user_quantity_text}/{sum_quantity}"
    
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            if profile:
                await get_detailed_profile_card(ctx, profile, pmsg)

            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_bg(roundrect_bg()).set_padding(8):
                for gid, lv_materials in gate_materials.items():
                    gate_icon = ctx.static_imgs.get(f'mysekai/gate_icon/gate_{gid}.png')
                    with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_item_bg(roundrect_bg()).set_padding(8):
                        spec_lv = spec_lvs.get(gid, 0)
                        with HSplit().set_content_align('c').set_item_align('c').set_omit_parent_bg(True):
                            ImageBox(gate_icon, size=(None, 40))
                            if spec_lv:
                                color = lerp_color(UNIT_COLORS[gid - 1], BLACK, 0.2)
                                TextBox(f"Lv.{spec_lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=color, use_shadow=True, shadow_color=ADAPTIVE_SHADOW))
                        lv_color = (50, 50, 50) if not profile else green_color
                        for level, items in enumerate(lv_materials, spec_lv + 1):
                            for item in items:
                                if any(i['color'] == red_color for i in items):
                                    lv_color = red_color

                            with HSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(8):
                                TextBox(f"{level}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=lv_color), overflow='clip').set_w(50)
                                for item in items:
                                    mid, quantity, color, sum_quantity = item['mid'], item['quantity'], item['color'], item['sum_quantity']
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(4):
                                        img = await get_mysekai_res_icon(ctx, f"mysekai_material_{mid}")
                                        with Frame():
                                            sz = 64
                                            ImageBox(img, size=(sz, sz))
                                            TextBox(f"x{quantity}", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50))) \
                                                .set_offset((sz, sz)).set_offset_anchor('rb')
                                        TextBox(sum_quantity, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=color))
    add_watermark(canvas)
    
    # 缓存full查询
    cache_key = None
    if profile is None and spec_gate_id is None:
        cache_key = f"{ctx.region}_pjsk_msg"
    return await canvas.get_img(cache_key=cache_key)

# 合成mysekai唱片列表
async def compose_mysekai_musicrecord_image(ctx: SekaiHandlerContext, qid: int, show_id: bool = False) -> Image.Image:
    mysekai_info, pmsg = await get_mysekai_info(ctx, qid, raise_exc=True)
    uid = get_player_bind_id(ctx)
    basic_profile = await get_basic_profile(ctx, uid)
    user_records = mysekai_info['updatedResources'].get('userMysekaiMusicRecords', [])

    category_mids = { tag: [] for tag in MUSIC_TAG_UNIT_MAP.keys() }
    mid_obtained_at = {}
    for record in await ctx.md.mysekai_musicrecords.get():
        if record['mysekaiMusicTrackType'] != 'music': continue
        rid, mid = record['id'], record['externalId']
        if not await is_valid_music(ctx, mid, leak=False): continue
        user_record = find_by(user_records, 'mysekaiMusicRecordId', rid)
        if user_record:
            mid_obtained_at[mid] = user_record['obtainedAt']
        tags = [t for t in await ctx.md.music_tags.find_by('musicId', mid, mode='all') if t['musicTag'] not in ['all', 'vocaloid']]
        if tags: tag = tags[0]['musicTag']
        else:    tag = 'vocaloid'
        category_mids[tag].append(mid)

    for tag in category_mids:
        category_mids[tag].sort(key=lambda x: mid_obtained_at.get(x, x * 1e12))

    total_num, obtained_num = 0, 0
    category_total_num = { tag: 0 for tag in MUSIC_TAG_UNIT_MAP.keys() }
    category_obtained_num = { tag: 0 for tag in MUSIC_TAG_UNIT_MAP.keys() }
    for tag, mids in category_mids.items():
        for mid in mids:
            total_num += 1
            category_total_num[tag] += 1
            if mid in mid_obtained_at:
                obtained_num += 1
                category_obtained_num[tag] += 1

    music_covers = {}
    for tag, mids in category_mids.items():
        music_covers[tag] = await batch_gather(*[get_music_cover_thumb(ctx, i) for i in mids])
        
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                await get_mysekai_info_card(ctx, mysekai_info, basic_profile, pmsg)

                a, b = obtained_num, total_num
                TextBox(f"总收集进度: {a}/{b} ({a/b*100:.1f}%)", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(25, 25, 25))) \
                    .set_padding(16).set_bg(roundrect_bg())

                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
                   for tag, mids in category_mids.items():
                        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5).set_item_bg(roundrect_bg()).set_padding(8):
                            # 标签
                            with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                                if MUSIC_TAG_UNIT_MAP[tag]:
                                    tag_icon = get_unit_icon(MUSIC_TAG_UNIT_MAP[tag])
                                    ImageBox(tag_icon, size=(None, 30))
                                else:
                                    TextBox("其他", TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=(50, 50, 50)))
                                a, b = category_obtained_num[tag], category_total_num[tag]
                                TextBox(f"{a}/{b} ({a/b*100:.1f}%)", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50)))

                            # 歌曲列表
                            sz = 30
                            with Grid(col_count=20).set_content_align('lt').set_item_align('lt').set_sep(3, 3).set_padding(8):
                                for mid, cover in zip(mids, music_covers[tag]):
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(3):
                                        with Frame():
                                            ImageBox(cover, size=(sz, sz))
                                            if mid not in mid_obtained_at:
                                                Spacer(w=sz, h=sz).set_bg(FillBg((0,0,0,150)))
                                        if show_id:
                                            TextBox(f"{mid}", TextStyle(font=DEFAULT_FONT, size=10, color=(50, 50, 50)))

    add_watermark(canvas)
    return await canvas.get_img()

# 合成mysekai对话列表图片
async def compose_mysekai_talk_list_image(
    ctx: SekaiHandlerContext, 
    qid: int, 
    show_id: bool, 
    cid: int, 
    unit: str = None,
    show_all_talks: bool = False,
) -> Image.Image:
    # 获取玩家已获得的蓝图对应的家具ID
    uid = get_player_bind_id(ctx)
    basic_profile = await get_basic_profile(ctx, uid)
    mysekai_info, mimsg = await get_mysekai_info(ctx, qid, raise_exc=True)
    assert_and_reply(
        'updatedResources' in mysekai_info,
        "你的Mysekai抓包数据不完整，请尝试退出游戏到标题界面后重新上传抓包数据"
    )
    assert_and_reply(
        'userMysekaiBlueprints' in mysekai_info['updatedResources'],
        "你的抓包数据来源没有提供蓝图数据"
    )
    obtained_fids = set()
    for item in mysekai_info['updatedResources']['userMysekaiBlueprints']:
        bid = item['mysekaiBlueprintId']
        blueprint = await ctx.md.mysekai_blueprints.find_by_id(bid)
        if blueprint and blueprint['mysekaiCraftType'] == 'mysekai_fixture':
            fid = blueprint['craftTargetId']
            obtained_fids.add(fid)

    # 获取所有可合成的家具ID
    craftable_fids = None
    craftable_fids = set()
    for item in await ctx.md.mysekai_blueprints.get():
        if item['mysekaiCraftType'] =='mysekai_fixture':
            craftable_fids.add(item['id'])

    # 获取需要的家具信息
    fixtures = {}
    all_fixtures = []
    for item in await ctx.md.mysekai_fixtures.get():
        fid = item['id']
        if craftable_fids and fid not in craftable_fids:
            obtained_fids.add(fid)
        
        ftype = item['mysekaiFixtureType']
        main_genre_id = item['mysekaiFixtureMainGenreId']
        sub_genre_id = item.get('mysekaiFixtureSubGenreId', -1)
        color_count = 1
        if item.get('mysekaiFixtureAnotherColors'):
            color_count += len(item['mysekaiFixtureAnotherColors'])

        if ftype == "gate": continue

        # 处理错误归类
        if fid == 4: 
            sub_genre_id = 14
        if main_genre_id in (5, 7, 8, 9, 10, 11, 12, 13):
            sub_genre_id = -1

        if main_genre_id not in fixtures:
            fixtures[main_genre_id] = {}
        if sub_genre_id not in fixtures[main_genre_id]:
            fixtures[main_genre_id][sub_genre_id] = []

        obtained = not obtained_fids or fid in obtained_fids
        fixtures[main_genre_id][sub_genre_id].append((fid, obtained))
        all_fixtures.append(item)
    
    # 获取家具图标
    fixture_icons = {}
    result = await batch_gather(*[get_mysekai_fixture_icon(ctx, item) for item in all_fixtures])
    for fixture, icon in zip(all_fixtures, result):
        fixture_icons[fixture['id']] = icon

    # 获取家具对应的角色对话已读情况
    if cid:
        # 获取vs角色的cuid
        mysekai_cuids = set([item['gameCharacterUnitId'] for item in await ctx.md.mysekai_gate_character_lotteries.get()])
        cus = [cu for cu in await ctx.md.game_character_units.find_by('gameCharacterId', cid, mode='all') if cu['id'] in mysekai_cuids]
        if len(cus) > 1:
            assert_and_reply(unit, f"查询存在多个组合的V家角色时需要同时指定组合，例如\"{ctx.original_trigger_cmd} miku ln\"")
            cu = find_by(cus, "unit", unit)
            assert_and_reply(cu, f"找不到要查询的角色")
        else:
            cu = cus[0]
            assert_and_reply(not unit or cu['unit'] == unit, f"找不到要查询的角色")
        cuid = cu['id']
        chara_icon = await get_character_sd_image(cuid)

        if not show_all_talks:
            profile, pmsg = await get_detailed_profile(
                ctx, 
                qid, 
                filter=get_detailed_profile_card_filter('userMysekaiCharacterTalks'),
                raise_exc=True)
            assert_and_reply('userMysekaiCharacterTalks' in profile, "你的Suite抓包数据来源没有提供角色家具对话数据")
            user_character_talks = profile['userMysekaiCharacterTalks']
        else:
            profile = None
            user_character_talks = []

        # 获取角色收集对话项目的对应家具id和已读情况
        aid_reads = {}
        fixture_conds = await ctx.md.mysekai_character_talk_conditions.find_by("mysekaiCharacterTalkConditionType", "mysekai_fixture_id", mode='all')
        for fixture in all_fixtures:
            fid = fixture['id']
            conds = find_by(fixture_conds, "mysekaiCharacterTalkConditionTypeValue", fid, mode='all')
            conditions_ids = set([cond['id'] for cond in conds])
            groups = await ctx.md.mysekai_character_talk_condition_groups.collect_by('mysekaiCharacterTalkConditionId', conditions_ids)
            group_ids = set([group['id'] for group in groups])
            talks = await ctx.md.mysekai_character_talks.collect_by('mysekaiCharacterTalkConditionGroupId', group_ids)
            for t in talks:
                # 获取对话的cuid
                chara_group = await ctx.md.mysekai_game_character_unit_groups.find_by_id(t['mysekaiGameCharacterUnitGroupId'])
                group_cuids = []
                for i in range(1, 10):
                    if f'gameCharacterUnitId{i}' in chara_group:
                        group_cuids.append(chara_group[f'gameCharacterUnitId{i}'])
                # 获取对话在角色收集对话项目的aid和显示情况
                tid = t['id']
                aid = t['characterArchiveMysekaiCharacterTalkGroupId']
                archive_info = await ctx.md.character_archive_mysekai_character_talk_groups.find_by_id(aid)
                display = archive_info and archive_info['archiveDisplayType'] == 'normal'
                # 有效的对话
                if cuid in group_cuids and display:
                    user_talk = find_by(user_character_talks, "mysekaiCharacterTalkId", tid)
                    has_read = bool(user_talk is not None and user_talk['isRead'])
                    if aid not in aid_reads:
                        aid_reads[aid] = {
                            'fids': set(),
                            'has_read': False,
                            'cuids': group_cuids,
                        }
                    aid_reads[aid]['fids'].add(fid)
                    aid_reads[aid]['has_read'] = aid_reads[aid]['has_read'] or has_read

        # 统计家具id以及对应收集情况
        fids_single_reads = {}  # 单人对话
        fids_multi_reads = {}   # 多人对话
        for aid, item in aid_reads.items():
            cuids = item['cuids']
            fids = " ".join(sorted([str(fid) for fid in item['fids']]))
            reads = fids_single_reads if len(cuids) == 1 else fids_multi_reads
            if fids not in reads:
                reads[fids] = {
                    'total': 0,
                    'read': 0,
                    'cuids_set': set(),
                }
            reads[fids]['total'] += 1
            reads[fids]['read'] += int(item['has_read'])
            if not item['has_read']:
                reads[fids]['cuids_set'].add(tuple(cuids))

        # 对话进度
        total_talk_num, total_read_num = 0, 0

        # 统计多人对话的进度
        for fids, item in fids_multi_reads.items():
            total_talk_num += item['total']
            total_read_num += item['read']
                
        # 重新构造单人对话的fixtures，包含组合的多个家具，并统计进度
        def find_genre(fid: int) -> Tuple[int, int]:
            for main_genre_id in fixtures:
                for sub_genre_id in fixtures[main_genre_id]:
                    if fid in [item[0] for item in fixtures[main_genre_id][sub_genre_id]]:
                        return main_genre_id, None  # 只分类一层
            return -1, -1
        single_talk_fixtures = {}
        for fids, item in fids_single_reads.items():
            fids = [int(fid) for fid in fids.split()]
            total_talk_num += item['total']
            total_read_num += item['read']
            if not fids: 
                continue
            if item['total'] == item['read']:
                continue
            main_genre_id, sub_genre_id = find_genre(fids[0])
            if main_genre_id not in single_talk_fixtures:
                single_talk_fixtures[main_genre_id] = {}
            if sub_genre_id not in single_talk_fixtures[main_genre_id]:
                single_talk_fixtures[main_genre_id][sub_genre_id] = []
            obtained = [not obtained_fids or fid in obtained_fids for fid in fids]
            single_talk_fixtures[main_genre_id][sub_genre_id].append((fids, obtained))
        # 多家具的排在前面
        for main_genre_id in single_talk_fixtures:
            for sub_genre_id in single_talk_fixtures[main_genre_id]:
                single_talk_fixtures[main_genre_id][sub_genre_id].sort(key=lambda x: (len(x[0]), x[0][0]), reverse=True)

    # 绘制单个家具
    f_sz = 40
    def draw_single_fid(fid: int):
        image = fixture_icons.get(fid)
        with VSplit().set_content_align('c').set_item_align('c').set_sep(2):
            with Frame():
                ImageBox(image, size=(None, f_sz), use_alphablend=True)
                if fid not in obtained_fids:
                    Spacer(w=f_sz, h=f_sz).set_bg(RoundRectBg(fill=(0,0,0,80), radius=2))
            if show_id:
                TextBox(f"{fid}", TextStyle(font=DEFAULT_FONT, size=10, color=(50, 50, 50)))

    # 绘制包含多个家具组合以及已读情况
    def draw_fids(fids: str, reads: Dict[str, Any]):
        with Frame().set_content_align('rb'):
            with HSplit().set_content_align('c').set_item_align('c').set_sep(2).set_bg(roundrect_bg(radius=4)).set_padding(4):
                for fid in fids:
                    draw_single_fid(fid)
            read_info = reads[" ".join([str(fid) for fid in fids])]
            noread_num = read_info['total'] - read_info['read']
            if noread_num > 1:
                TextBox(f"x{noread_num}", TextStyle(font=DEFAULT_FONT, size=12, color=(255, 0, 0))).set_offset((5, 5))

    text_color = (75, 75, 75)
                                        
    # 绘制
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                if qid and profile:
                    await get_mysekai_and_detail_profile_card(ctx, mysekai_info, profile, mimsg + pmsg)
                elif qid:
                    await get_mysekai_info_card(ctx, mysekai_info, basic_profile, mimsg)
                elif profile:
                    await get_detailed_profile_card(ctx, profile, pmsg)

            # 进度
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                with HSplit().set_content_align('l').set_item_align('l').set_sep(5):
                    ImageBox(chara_icon, size=(None, 60))
                    if not show_all_talks:
                        TextBox(f"未读对话家具列表 - 进度: {total_read_num}/{total_talk_num} ({total_read_num/total_talk_num*100:.1f}%)", 
                                TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color))
                    else:
                        TextBox(f"对话家具列表 - 共 {total_talk_num} 条对话", 
                                TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color))
                if not show_all_talks:
                    TextBox(f"*仅展示未读对话家具，灰色表示未获得蓝图", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=text_color))
            
            # 单人家具
            TextBox(f"单人对话家具", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color)) \
                .set_padding(12).set_bg(roundrect_bg())
            
            sep = 5
            row_w = 15 * (f_sz + 8 + sep)

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
                has_single = False
                # 一级分类
                for main_genre_id in sorted(single_talk_fixtures.keys()):
                    if count_dict(single_talk_fixtures[main_genre_id], 2) == 0: continue
                    has_single = True

                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8):
                        # 标签
                        main_genre_name, main_genre_image = await get_mysekai_fixture_genre_name_and_image(ctx, main_genre_id, True)
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                            ImageBox(main_genre_image, size=(None, 30), use_alphablend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                            TextBox(main_genre_name, TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=text_color))

                        # 家具列表
                        for sub_genre_id in sorted(single_talk_fixtures[main_genre_id].keys()):
                            if len(single_talk_fixtures[main_genre_id][sub_genre_id]) == 0: continue
                            
                            with Flow().set_item_align('lt').set_content_align('lt').set_sep(sep, sep).set_w(row_w):
                                for fids, _ in single_talk_fixtures[main_genre_id][sub_genre_id]:
                                    draw_fids(fids, fids_single_reads)   

                if not has_single:
                    TextBox("全部已读", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 150, 50))).set_padding(12)

            # 多人家具
            TextBox(f"多人对话家具", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color)) \
                .set_padding(12).set_bg(roundrect_bg())    

            with Flow().set_item_align('l').set_content_align('l').set_sep(16, 8).set_w(row_w + 16).set_padding(8).set_bg(roundrect_bg()) as flow:
                has_multi = False
                for fids, item in fids_multi_reads.items():
                    if not fids or item['total'] == item['read']:
                        continue
                    has_multi = True
                    fids = list(map(int, fids.split()))
                    with HSplit().set_content_and_item_align('c').set_sep(6):
                        draw_fids(fids, fids_multi_reads)
                        with Flow().set_content_and_item_align('l').set_sep(6, 6).set_w(row_w // 2).set_size_policy(w_policy='fit'):
                            for cuids in item['cuids_set']:
                                with HSplit().set_content_and_item_align('c').set_sep(5).set_padding(4).set_bg(roundrect_bg()):
                                    for cuid in cuids:
                                        ImageBox(await get_chara_icon_by_chara_unit_id(ctx, cuid), size=(None, 45))

                if not has_multi:
                    TextBox("全部已读", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 150, 50))).set_padding(4)
                    flow.set_w(None).set_row_or_col_count(1)

    add_watermark(canvas)
    return await canvas.get_img()

# 获取字节服msr限制uid，不限制则返回None
def get_bd_msr_limit_uid(ctx: SekaiHandlerContext, qid: int) -> str | None:
    if ctx.region not in BD_MYSEKAI_REGIONS or int(qid) in SUPERUSER_CFG.get():
        return None
    qid = str(qid)
    msr_binds: dict[str, str] = bd_msr_bind_db.get(f"{ctx.region}_bind", {})
    if qid not in msr_binds:
        return update_bd_msr_limit_uid(ctx, qid)
    return msr_binds[qid]

# 切换字节服msr限制uid为当前绑定的ID，返回绑定的ID
async def update_bd_msr_limit_uid(ctx: SekaiHandlerContext, qid: int, force: bool = False):
    assert_and_reply(ctx.region in BD_MYSEKAI_REGIONS, "指令对此区服无效")
    assert_and_reply(bd_msr_sub.is_subbed(ctx.region, ctx.group_id), "指令在此群无效")
    qid = str(ctx.user_id)
    next_time = bd_msr_bind_db.get(f"{ctx.region}_next_time.{qid}", 0)
    if not force and next_time > datetime.now().timestamp():
        raise ReplyException(f"请于{datetime.fromtimestamp(next_time).strftime('%m-%d %H:%M:%S')}后再试")
    uid = get_player_bind_id(ctx)
    
    async def do_update(new_ctx: HandlerContext):
        msr_binds: dict[str, str] = bd_msr_bind_db.get(f"{ctx.region}_bind", {})
        last_bind = msr_binds.get(qid)
        assert_and_reply(last_bind != str(uid), f"你的MSR限制ID已经是当前ID，无需换绑")
        msr_binds[qid] = str(uid)
        bd_msr_bind_db.set(f"{ctx.region}_bind", msr_binds)

        next_time = int((datetime.now() + timedelta(days=7)).timestamp())
        bd_msr_bind_db.set(f"{ctx.region}_next_time.{qid}", next_time)
        await new_ctx.asend_reply_msg(f"已将你的{get_region_name(ctx.region)}MSR查询限制ID切换为当前绑定的ID: "
                                f"{process_hide_uid(ctx, uid, keep=6)}，一周内不可再次切换")
        
    await add_need_confirm_action(ctx, do_update, 
        additional_msg=f"是否将你的{get_region_name(ctx.region)}MSR查询限制ID切换为当前绑定的ID: "
        f"{process_hide_uid(ctx, uid, keep=6)}？一周内只能切换一次")



# ======================= 指令处理 ======================= #

# 查询mysekai资源
pjsk_mysekai_res = SekaiCmdHandler([
    "/pjsk mysekai res", "/msr", "/msmap", "/msa",
    "/mysekai 资源", 
], regions=MYSEKAI_REGIONS, priority=1)
pjsk_mysekai_res.check_cdrate(cd).check_wblist(gbl)
@pjsk_mysekai_res.handle()
async def _(ctx: SekaiHandlerContext):
    with ProfileTimer("msr.total"):
        if ctx.region in bd_msr_sub.regions and not bd_msr_sub.is_subbed(ctx.region, ctx.group_id): 
            raise ReplyException(f"不支持{get_region_name(ctx.region)}的msr查询")
        args = ctx.get_args().strip()
        show_harvested = 'all' in args
        check_time = not 'force' in args
        imgs = await compose_mysekai_res_image(ctx, ctx.user_id, show_harvested, check_time)
        imgs = [await get_image_cq(img, low_quality=True) for img in imgs]
        await ctx.asend_reply_msg("".join(imgs))


# 查询mysekai蓝图
pjsk_mysekai_blueprint = SekaiCmdHandler([
    "/pjsk mysekai blueprint", "/mysekai blueprint",
    "/msb", "/mysekai 蓝图",
], regions=MYSEKAI_REGIONS)
pjsk_mysekai_blueprint.check_cdrate(cd).check_wblist(gbl)
@pjsk_mysekai_blueprint.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    show_id = False
    if 'id' in args:
        show_id = True
        args = args.replace('id', '').strip()

    show_all_talks = False
    if 'all' in args:
        show_all_talks = True
        args = args.replace('all', '').strip()
    unit, args = extract_unit(args)
    cid = get_cid_by_nickname(args)

    if not cid:
        img = await compose_mysekai_fixture_list_image(
            ctx, 
            qid=ctx.user_id, 
            show_id=True, 
            only_craftable=True, 
        )
    else:
        img = await compose_mysekai_talk_list_image(
            ctx, 
            qid=ctx.user_id, 
            show_id=True, 
            cid=cid, 
            unit=unit, 
            show_all_talks=show_all_talks
        )

    return await ctx.asend_reply_msg(await get_image_cq(img, low_quality=True))


# 查询mysekai家具列表/家具
pjsk_mysekai_furniture = SekaiCmdHandler([
    "/pjsk mysekai furniture", "/pjsk mysekai fixture", 
    "/msf", "/mysekai 家具", "/家具列表",
], regions=MYSEKAI_REGIONS)
pjsk_mysekai_furniture.check_cdrate(cd).check_wblist(gbl)
@pjsk_mysekai_furniture.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    try: fids = list(map(int, args.split()))
    except: fids = None
    # 查询指定家具
    if fids:
        assert_and_reply(len(fids) <= 10, "最多一次查询10个家具")
        return await ctx.asend_reply_msg(await get_image_cq(
            await compose_mysekai_fixture_detail_image(ctx, fids),
            low_quality=True
        ))
    
    # 查询家具列表
    show_all_talks = False
    if 'all' in args:
        show_all_talks = True
        args = args.replace('all', '').strip()
    unit, args = extract_unit(args)
    cid = get_cid_by_nickname(args)

    if not cid:
        img = await compose_mysekai_fixture_list_image(
            ctx, 
            qid=None, 
            show_id=True, 
            only_craftable=False, 
        )
    else:
        img = await compose_mysekai_talk_list_image(
            ctx, 
            qid=ctx.user_id, 
            show_id=True, 
            cid=cid, 
            unit=unit, 
            show_all_talks=show_all_talks
        )

    return await ctx.asend_reply_msg(await get_image_cq(img, low_quality=True))


# 下载mysekai照片
pjsk_mysekai_photo = SekaiCmdHandler([
    "/pjsk mysekai photo", "/pjsk mysekai picture", 
    "/msp", "/mysekai 照片" ,
], regions=MYSEKAI_REGIONS)
pjsk_mysekai_photo.check_cdrate(cd).check_wblist(gbl)
@pjsk_mysekai_photo.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    try: seq = int(args)
    except: raise Exception("请输入正确的照片编号（从1或-1开始）")

    photo, time = await get_mysekai_photo_and_time(ctx, ctx.user_id, seq)
    msg = await get_image_cq(photo) + f"拍摄时间: {time.strftime('%Y-%m-%d %H:%M')}"

    return await ctx.asend_reply_msg(msg)


# 查询烤森抓包数据
pjsk_check_mysekai_data = SekaiCmdHandler([
    "/pjsk check mysekai data",
    "/pjsk烤森抓包数据", "/pjsk烤森抓包", "/烤森抓包", "/烤森抓包数据",
    "/msd",
], regions=MYSEKAI_REGIONS)
pjsk_check_mysekai_data.check_cdrate(cd).check_wblist(gbl)
@pjsk_check_mysekai_data.handle()
async def _(ctx: SekaiHandlerContext):
    cqs = extract_cq_code(ctx.get_msg())
    qid = int(cqs['at'][0]['qq']) if 'at' in cqs else ctx.user_id
    uid = get_player_bind_id(ctx)

    task1 = get_mysekai_info(ctx, qid, raise_exc=False, mode="local", filter=['upload_time'])
    task2 = get_mysekai_info(ctx, qid, raise_exc=False, mode="haruki", filter=['upload_time'])
    (local_profile, local_err), (haruki_profile, haruki_err) = await asyncio.gather(task1, task2)

    msg = f"{process_hide_uid(ctx, uid, keep=6)}({ctx.region.upper()}) Mysekai数据\n"

    if local_err:
        local_err = local_err[local_err.find(']')+1:].strip()
        msg += f"[本地数据]\n获取失败: {local_err}\n"
    else:
        msg += "[本地数据]\n"
        upload_time = datetime.fromtimestamp(local_profile['upload_time'] / 1000)
        upload_time_text = upload_time.strftime('%m-%d %H:%M:%S') + f"({get_readable_datetime(upload_time, show_original_time=False)})"
        if ctx.region in BD_MYSEKAI_REGIONS:
            process_sensitive_cmd_source(local_profile)
        if local_source := local_profile.get('local_source'):
            upload_time_text = local_source + " " + upload_time_text
        msg += f"{upload_time_text}\n"

    if haruki_err:
        haruki_err = haruki_err[haruki_err.find(']')+1:].strip()
        msg += f"[Haruki工具箱]\n获取失败: {haruki_err}\n"
    else:
        msg += "[Haruki工具箱]\n"
        upload_time = datetime.fromtimestamp(haruki_profile['upload_time'] / 1000)
        upload_time_text = upload_time.strftime('%m-%d %H:%M:%S') + f"({get_readable_datetime(upload_time, show_original_time=False)})"
        msg += f"{upload_time_text}\n"

    mode = get_user_data_mode(ctx, ctx.user_id)
    msg += f"---\n"
    msg += f"该指令查询Mysekai数据，查询Suite数据请使用\"/{ctx.region}抓包状态\"\n"
    # msg += f"数据获取模式: {mode}，使用\"/{ctx.region}抓包模式\"来切换模式\n"
    msg += f"发送\"/抓包\"获取抓包教程"

    return await ctx.asend_reply_msg(msg)


# 查询烤森门升级数据
pjsk_mysekai_gate = SekaiCmdHandler([
    "/pjsk mysekai gate",  "/msg", "/msgate",
], regions=MYSEKAI_REGIONS)
pjsk_mysekai_gate.check_cdrate(cd).check_wblist(gbl)
@pjsk_mysekai_gate.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    qid = ctx.user_id
    if 'all' in args:
        qid = None
        args = args.replace('all', '').strip()
    
    try: 
        unit, args = extract_unit(args)
        gate_id = UNIT_GATEID_MAP[unit]
    except: 
        gate_id = None

    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_mysekai_door_upgrade_image(ctx, qid, gate_id),
        low_quality=True
    ))


# 查询烤森唱片数据
pjsk_mysekai_musicrecord = SekaiCmdHandler([
    "/pjsk mysekai musicrecord", "/msm", "/mss", "/mssong", 
], regions=MYSEKAI_REGIONS)
pjsk_mysekai_musicrecord.check_cdrate(cd).check_wblist(gbl)
@pjsk_mysekai_musicrecord.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    show_id = False
    if 'id' in args:
        show_id = True
        args = args.replace('id', '').strip()

    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_mysekai_musicrecord_image(
            ctx, 
            ctx.user_id,
            show_id=show_id,
        ),
        low_quality=True
    ))


# msr换绑
msr_change_bind = SekaiCmdHandler([
    "/msr换绑",
], regions=BD_MYSEKAI_REGIONS)
msr_change_bind.check_cdrate(cd).check_wblist(gbl)
@msr_change_bind.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    force = False
    if 'force' in args and check_superuser(ctx.event):
        force = True
        args = args.replace('force', '', 1).strip()
    assert_and_reply(not args, "该指令用于切换MSR查询限制ID为你当前绑定的ID，不需要添加参数。请你确认要更换的ID为你当前绑定的ID")
    await update_bd_msr_limit_uid(ctx, ctx.user_id, force)
    

# ======================= 定时任务 ======================= #

# MSR自动推送 & MSR订阅更新
@repeat_with_interval(config.item('mysekai.msr_push_interval_seconds'), 'MSR自动推送', logger)
async def msr_auto_push():
    for region in ALL_SERVER_REGIONS:
        region_name = get_region_name(region)
        ctx = SekaiHandlerContext.from_region(region)

        get_upload_time_url = get_gameapi_config(ctx).mysekai_upload_time_api_url
        if not get_upload_time_url: continue
        if region not in msr_sub.regions: continue

        # 获取订阅的用户列表和抓包模式
        qids = list(set([qid for qid, gid in msr_sub.get_all_gid_uid(region)]))
        uid_modes: list[tuple[int, int]] = []
        for qid in qids:
            for i in range(get_player_bind_count(ctx, qid)):
                try:
                    if uid := get_player_bind_id(ctx, qid, index=i):
                        uid_modes.append((uid, get_user_data_mode(ctx, qid)))
                except:
                    pass
        if not uid_modes: continue

        # 向api服务器更新msr订阅信息
        update_msr_sub_url = get_gameapi_config(ctx).update_msr_sub_api_url
        if update_msr_sub_url:
            try:
                await request_gameapi(update_msr_sub_url, json=uid_modes, method='PUT')
            except Exception as e:
                logger.warning(f"更新{region_name}Mysekai订阅信息失败: {get_exc_desc(e)}")

        # 获取不同uid_mode的Mysekai上传时间
        try:
            upload_times: list[int] = await request_gameapi(get_upload_time_url, json=uid_modes)
        except Exception as e:
            logger.warning(f"获取{region_name}Mysekai上传时间失败: {get_exc_desc(e)}")
            continue
        upload_times: dict[tuple[str, str], int] = { uid_mode: ts for uid_mode, ts in zip(uid_modes, upload_times) }

        need_push_uid_modes = [] # 需要推送的uid_mode（有及时更新数据并且没有距离太久的）
        last_refresh_time = get_mysekai_last_refresh_time_and_reason(ctx)[0]
        for uid_mode, ts in upload_times.items():
            update_time = datetime.fromtimestamp(ts / 1000)
            if update_time > last_refresh_time and datetime.now() - update_time < timedelta(minutes=10):
                need_push_uid_modes.append(uid_mode)

        tasks = []
                
        for qid, gid in msr_sub.get_all_gid_uid(region):
            if check_in_blacklist(qid): continue
            if not gbl.check_id(gid): continue
            if region in bd_msr_sub.regions and not bd_msr_sub.is_subbed(region, gid): continue
            
            for i in range(get_player_bind_count(ctx, qid)):
                msr_last_push_time = file_db.get(f"{region}_msr_last_push_time", {})

                uid = get_player_bind_id(ctx, qid, index=i)
                mode = get_user_data_mode(ctx, qid)
                if not uid or (uid, mode) not in need_push_uid_modes:
                    continue

                # 检查这个uid-qid刷新后是否已经推送过
                key = f"{uid}-{qid}"
                if key in msr_last_push_time:
                    last_push_time = datetime.fromtimestamp(msr_last_push_time[key] / 1000)
                    if last_push_time >= last_refresh_time:
                        continue
                msr_last_push_time[key] = int(datetime.now().timestamp() * 1000)
                file_db.set(f"{region}_msr_last_push_time", msr_last_push_time)
                
                tasks.append((gid, qid, uid))

        async def push(task):
            gid, qid, uid = task
            user_ctx = SekaiHandlerContext.from_region(region)
            user_ctx.user_id = int(qid)
            user_ctx.group_id = int(gid)

            index = get_player_bind_id_index(ctx, qid, uid)
            if index is None: return
            user_ctx.uid_arg = f"u{index+1}"
            
            try:
                logger.info(f"在 {gid} 中自动推送用户 {qid} 的{region_name}Mysekai资源查询")
                contents = [
                    await get_image_cq(img, low_quality=True) for img in 
                    await compose_mysekai_res_image(user_ctx, qid, False, True)
                ]
                contents = [f"[CQ:at,qq={qid}]的{region_name}MSR推送"] + contents
                await send_group_msg_by_bot(gid, "".join(contents))
            except MsrIdNotMatchException as e:
                logger.warning(f'在 {gid} 中自动推送用户 {qid} 的{region_name}Mysekai资源查询失败: 限制id不匹配')
            except Exception as e:
                logger.print_exc(f'在 {gid} 中自动推送用户 {qid} 的{region_name}Mysekai资源查询失败')
                try: await send_group_msg_by_bot(gid, f"自动推送用户 [CQ:at,qq={qid}] 的{region_name}Mysekai资源查询失败: {get_exc_desc(e)}")
                except: pass

        await batch_gather(*[push(task) for task in tasks], batch_size=MSR_PUSH_CONCURRENCY_CFG.get())
