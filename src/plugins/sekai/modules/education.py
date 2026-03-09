from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..suite import Suite
from .resbox import get_res_box_info, get_res_icon
from .profile import (
    get_detailed_profile,
    get_detailed_profile_card,
    get_detailed_profile_card_filter,
    get_player_avatar_info_by_detailed_profile,
)

@dataclass
class AreaItemFilter:
    unit: str = None        # 某个团的世界里面的所有道具
    cid: int = None         # 某个角色的所有道具
    attr: str = None        # 某个属性的所有道具
    tree: bool = None       # 所有树
    flower: bool = None     # 所有花

FLOWER_AREA_ID = 13
TREE_AREA_ID = 11
UNIT_SEKAI_AREA_IDS = {
    "light_sound": 5,
    "idol": 7,
    "street": 8,
    "theme_park": 9,
    "school_refusal": 10,
}


# ======================= 处理逻辑 ======================= #

# 获取玩家挑战live信息，返回（rank, score, remain_jewel, remain_fragment）
async def get_user_challenge_live_info(ctx: SekaiHandlerContext, profile: Suite) -> Dict[int, Tuple[int, int, int, int]]:
    challenge_info = {}
    challenge_results = profile.userChallengeLiveSoloResults
    challenge_stages = profile.userChallengeLiveSoloStages
    challenge_rewards = profile.userChallengeLiveSoloHighScoreRewards
    for cid in range(1, 27):
        stages = find_by(challenge_stages, 'characterId', cid, mode='all')
        rank = max([stage['rank'] for stage in stages]) if stages else 0
        result = find_by(challenge_results, 'characterId', cid)
        score = result['highScore'] if result else 0
        remain_jewel, remain_fragment = 0, 0
        completed_reward_ids = [item['challengeLiveHighScoreRewardId'] for item in find_by(challenge_rewards, 'characterId', cid, mode='all')]
        for reward in await ctx.md.challenge_live_high_score_rewards.get():
            if reward['id'] in completed_reward_ids or reward['characterId'] != cid:
                continue
            res_box = await get_res_box_info(ctx, 'challenge_live_high_score', reward['resourceBoxId'])
            for res in res_box:
                if res['type'] == 'jewel':
                    remain_jewel += res['quantity']
                if res['type'] == 'material' and res['id'] == 15:
                    remain_fragment += res['quantity']
        challenge_info[cid] = (rank, score, remain_jewel, remain_fragment)
    return challenge_info

# 合成挑战live详情图片
async def compose_challenge_live_detail_image(ctx: SekaiHandlerContext, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, qid, 
        filter=get_detailed_profile_card_filter('userChallengeLiveSoloResults','userChallengeLiveSoloStages','userChallengeLiveSoloHighScoreRewards'), 
        raise_exc=True)
    
    challenge_info = await get_user_challenge_live_info(ctx, profile)

    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    w1, w2, w3, w4, w5, w6 = 80, 80, 150, 300, 80, 80

    max_score = max([item['highScore'] for item in await ctx.md.challenge_live_high_score_rewards.get()])

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, err_msg)
            with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                # 标题
                with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_h(header_h).set_padding(4).set_bg(roundrect_bg()):
                    TextBox("角色", header_style).set_w(w1).set_content_align('c')
                    TextBox("等级", header_style).set_w(w2).set_content_align('c')
                    TextBox("分数", header_style).set_w(w3).set_content_align('c')
                    TextBox(f"进度(上限{max_score//10000}w)", header_style).set_w(w4).set_content_align('c')
                    with Frame().set_w(w5).set_content_align('c'):
                        ImageBox(ctx.static_imgs.get("jewel.png"), size=(None, 40))
                    with Frame().set_w(w6).set_content_align('c'):
                        ImageBox(ctx.static_imgs.get("shard.png"), size=(None, 40))

                # 项目
                for cid in range(1, 27):
                    bg_color = (255, 255, 255, 150) if cid % 2 == 0 else (255, 255, 255, 100)
                    rank = str(challenge_info[cid][0]) if challenge_info[cid][0] else "-"
                    score = str(challenge_info[cid][1]) if challenge_info[cid][1] else "-"
                    jewel = str(challenge_info[cid][2])
                    fragment = str(challenge_info[cid][3])
                    with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_h(row_h).set_padding(4).set_bg(roundrect_bg(fill=bg_color)):
                        with Frame().set_w(w1).set_content_align('c'):
                            ImageBox(get_chara_icon_by_chara_id(cid), size=(None, 40))
                        TextBox(rank, text_style).set_w(w2).set_content_align('c')
                        TextBox(score, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w3).set_content_align('c')

                        with Frame().set_w(w4).set_content_align('lt'):
                            x = challenge_info[cid][1]
                            progress = max(min(x / max_score, 1), 0)
                            total_w, total_h, border = w4, 14, 2
                            progress_w = int((total_w - border * 2) * progress)
                            progress_h = total_h - border * 2
                            color = (255, 50, 50, 255)
                            if x > 250_0000:    color = (100, 255, 100, 255)
                            elif x > 200_0000:  color = (255, 255, 100, 255)
                            elif x > 150_0000:  color = (255, 200, 100, 255)
                            elif x > 100_0000:  color = (255, 150, 100, 255)
                            elif x > 50_0000:   color = (255, 100, 100, 255)
                            if progress > 0:
                                Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 255), radius=total_h//2))
                                Spacer(w=progress_w, h=progress_h).set_bg(RoundRectBg(fill=color, radius=(total_h-border)//2)).set_offset((border, border))

                                def draw_line(line_x: int):
                                    p = line_x / max_score
                                    if p <= 0 or p >= 1: return
                                    lx = int((total_w - border * 2) * p)
                                    color = (100, 100, 100, 255) if line_x < x else (150, 150, 150, 255)
                                    Spacer(w=1, h=total_h//2-1).set_bg(FillBg(color)).set_offset((border + lx - 1, total_h//2))
                                for line_x in range(0, max_score, 50_0000):
                                    draw_line(line_x)
                            else:
                                Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 100), radius=total_h//2))

                        TextBox(jewel, text_style).set_w(w5).set_content_align('c')
                        TextBox(fragment, text_style).set_w(w6).set_content_align('c')

    add_watermark(canvas)
    return await canvas.get_img()

# 获取玩家加成信息
async def get_user_power_bonus(ctx: SekaiHandlerContext, profile: Suite) -> Dict[str, int]:
    # 获取区域道具
    area_items: List[dict] = []
    for user_area in profile.userAreas:
        for user_area_item in user_area.get('areaItems', []):
            item_id = user_area_item['areaItemId']
            lv = user_area_item['level']
            area_items.append(find_by(find_by(await ctx.md.area_item_levels.get(), 'areaItemId', item_id, mode='all'), 'level', lv))

    # 角色加成 = 区域道具 + 角色等级 + 烤森家具
    chara_bonus = { i : {
        'area_item': 0,
        'rank': 0,
        'fixture': 0,
    } for i in range(1, 27) }
    for item in area_items:
        if item.get('targetGameCharacterId', "any") != "any":
            chara_bonus[item['targetGameCharacterId']]['area_item'] += item['power1BonusRate']
    for chara in profile.userCharacters:
        rank = find_by(await ctx.md.character_ranks.find_by('characterId', chara['characterId'], mode='all'), 'characterRank', chara['characterRank'])
        chara_bonus[chara['characterId']]['rank'] += rank['power1BonusRate']
    for fb in profile.userMysekaiFixtureGameCharacterPerformanceBonuses:
        chara_bonus[fb['gameCharacterId']]['fixture'] += fb['totalBonusRate'] * 0.1
    
    # 组合加成 = 区域道具 + 烤森门
    unit_bonus = { unit : {
        'area_item': 0,
        'gate': 0,
    } for unit in UNITS }
    for item in area_items:
        if item.get('targetUnit', "any") != "any":
            unit_bonus[item['targetUnit']]['area_item'] += item['power1BonusRate']
    max_bonus = 0
    for gate in profile.userMysekaiGates:
        gate_id = gate['mysekaiGateId']
        bonus = find_by(await ctx.md.mysekai_gate_levels.find_by('mysekaiGateId', gate_id, mode='all'), 'level', gate['mysekaiGateLevel'])
        unit_bonus[UNITS[gate_id - 1]]['gate'] += bonus['powerBonusRate']
        max_bonus = max(max_bonus, bonus['powerBonusRate'])
    unit_bonus[UNIT_VS]['gate'] += max_bonus

    # 属性加成 = 区域道具
    attr_bouns = { attr : {
        'area_item': 0,
    } for attr in CARD_ATTRS }
    for item in area_items:
        if item.get('targetCardAttr', "any") != "any":
            attr_bouns[item['targetCardAttr']]['area_item'] += item['power1BonusRate']

    for _, bonus in chara_bonus.items():
        bonus['total'] = sum(bonus.values())
    for _, bonus in unit_bonus.items():
        bonus['total'] = sum(bonus.values())
    for _, bonus in attr_bouns.items():
        bonus['total'] = sum(bonus.values())
    
    return {
        "chara": chara_bonus,
        "unit": unit_bonus,
        "attr": attr_bouns
    }

# 合成加成详情图片
async def compose_power_bonus_detail_image(ctx: SekaiHandlerContext, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userAreas','userCharacters','userMysekaiFixtureGameCharacterPerformanceBonuses','userMysekaiGates'), 
        raise_exc=True)
 
    bonus = await get_user_power_bonus(ctx, profile)
    chara_bonus = bonus['chara']
    unit_bonus = bonus['unit']
    attr_bonus = bonus['attr']

    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=16, color=(100, 100, 100, 255))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, err_msg)
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg()).set_bg(roundrect_bg()).set_padding(16):
                # 角色加成
                cid_parts = [range(1, 5), range(5, 9), range(9, 13), range(13, 17), range(17, 21), range(21, 27)]
                for cids in cid_parts:
                    with Grid(col_count=2).set_content_align('l').set_item_align('l').set_sep(20, 4).set_padding(16):
                        for cid in cids:
                            with HSplit().set_content_align('l').set_item_align('l').set_sep(4):
                                ImageBox(get_chara_icon_by_chara_id(cid), size=(None, 40))
                                TextBox(f"{chara_bonus[cid]['total']:.1f}%", header_style).set_w(100).set_content_align('r').set_overflow('clip')
                                detail = f"区域道具{chara_bonus[cid]['area_item']:.1f}% + 角色等级{chara_bonus[cid]['rank']:.1f}% + 烤森玩偶{chara_bonus[cid]['fixture']:.1f}%"
                                TextBox(detail, text_style)
                        
                # 组合加成
                with Grid(col_count=3).set_content_align('l').set_item_align('l').set_sep(20, 4).set_padding(16):
                    for unit in UNITS:
                        with HSplit().set_content_align('l').set_item_align('l').set_sep(4):
                            ImageBox(get_unit_icon(unit), size=(None, 40))
                            TextBox(f"{unit_bonus[unit]['total']:.1f}%", header_style).set_w(100).set_content_align('r').set_overflow('clip')
                            detail = f"区域道具{unit_bonus[unit]['area_item']:.1f}% + 烤森门{unit_bonus[unit]['gate']:.1f}%"
                            TextBox(detail, text_style)

                # 属性加成
                with Grid(col_count=5).set_content_align('l').set_item_align('l').set_sep(20, 4).set_padding(16):
                    for attr in CARD_ATTRS:
                        with HSplit().set_content_align('l').set_item_align('l').set_sep(4):
                            ImageBox(get_attr_icon(attr), size=(None, 40))
                            TextBox(f"{attr_bonus[attr]['total']:.1f}%", header_style).set_w(100).set_content_align('r').set_overflow('clip')
                            # detail = f"区域道具{attr_bonus[attr]['area_item']:.1f}%"
                            # TextBox(detail, text_style)

    add_watermark(canvas)
    return await canvas.get_img()

# 合成区域道具升级材料图片
async def compose_area_item_upgrade_materials_image(ctx: SekaiHandlerContext, qid: int, filter: AreaItemFilter) -> Image.Image:
    profile = None
    if qid:
        profile, pmsg = await get_detailed_profile(
            ctx, 
            qid, 
            filter=get_detailed_profile_card_filter('userMaterials','userGamedata','userAreas',),
            raise_exc=True, 
            ignore_hide=True)
        
    COIN_ID = -1
    user_materials: dict[int, int] = {}
    user_area_item_lvs: dict[int, int] = {}
    
    if profile:
        # 获取玩家材料（金币当作id=-1的材料）
        assert_and_reply(profile.has_field('userMaterials'), "你的Suite数据来源没有提供userMaterials数据（可能需要重传）")
        user_materials = {}
        user_materials[COIN_ID] = profile.userGamedata.get('coin', 0)
        for item in profile.userMaterials:
            user_materials[item['materialId']] = item['quantity']
        # 获取玩家区域道具等级
        user_area_item_lvs = {}
        for area in profile.userAreas:
            for area_item in area.get('areaItems', []):
                user_area_item_lvs[area_item['areaItemId']] = area_item['level']

    # 筛选vs额外判断
    filter_piapro = False
    if filter.unit == 'piapro':
        filter.unit = None
        filter_piapro = True

    # 获取区域道具信息，同时筛选需要展示的区域道具id
    item_ids: set[int] = set()
    area_item_icons: dict[int, Image.Image] = {}
    area_item_target_icons: dict[int, Image.Image] = {}
    area_item_level_bonuses: dict[int, dict[int, float]] = {}
    area_item_max_levels: dict[int, int] = {}
    for item in await ctx.md.area_items.get():
        item_id, area_id, asset_name = item['id'], item['areaId'], item['assetbundleName']

        is_vs_item = False

        area_item_icons[item_id] = await ctx.rip.img(f"areaitem/{asset_name}/{asset_name}.png")
        for item_lv in await ctx.md.area_item_levels.find_by('areaItemId', item_id, mode='all'):
            area_item_level_bonuses.setdefault(item_id, {})[item_lv['level']] = item_lv['power1BonusRate']
            area_item_max_levels[item_id] = max(area_item_max_levels.get(item_id, 0), item_lv['level'])

            if item_id not in area_item_target_icons:
                if item_lv.get('targetUnit', 'any') != 'any':
                    area_item_target_icons[item_id] = get_unit_icon(item_lv['targetUnit'])
                    if item_lv['targetUnit'] == 'piapro':
                        if filter_piapro:
                            item_ids.add(item_id)
                        is_vs_item = True
                elif item_lv.get('targetGameCharacterId', 'any') != 'any':
                    area_item_target_icons[item_id] = get_chara_icon_by_chara_id(item_lv['targetGameCharacterId'])
                    if filter.cid and item_lv['targetGameCharacterId'] == filter.cid:
                        item_ids.add(item_id)
                    if item_lv['targetGameCharacterId'] in UNIT_CID_MAP['piapro']:
                        is_vs_item = True
                elif item_lv.get('targetCardAttr', 'any') != 'any':
                    area_item_target_icons[item_id] = get_attr_icon(item_lv['targetCardAttr'])
                    if filter.attr and item_lv['targetCardAttr'] == filter.attr:
                        item_ids.add(item_id)

        if filter.flower and area_id == FLOWER_AREA_ID:
            item_ids.add(item_id)
        if filter.tree and area_id == TREE_AREA_ID:
            item_ids.add(item_id)
        if filter.unit and area_id == UNIT_SEKAI_AREA_IDS[filter.unit] and not is_vs_item:
            item_ids.add(item_id)

    item_ids = sorted(item_ids)

    # 统计展示的最低等级
    user_area_item_lower_lv = None
    for item_id in item_ids:
        lv = user_area_item_lvs.get(item_id, 0)
        if user_area_item_lower_lv is None or lv < user_area_item_lower_lv:
            user_area_item_lower_lv = lv
    if user_area_item_lower_lv is None:
        user_area_item_lower_lv = 0

    # 获取区域道具等级对应的shopItem的resboxId ids[item_id][level] = resbox_id
    area_item_lv_shop_item_resbox_ids: dict[int, dict[int, int]] = {}
    for box_id, box in (await ctx.md.resource_boxes.get())['shop_item'].items():
        if details := box.get('details'):
            detail = details[0]
            res_type = detail.get('resourceType')
            res_id = detail.get('resourceId')
            res_lv = detail.get('resourceLevel')
            if res_type == 'area_item' and res_id in item_ids:
                area_item_lv_shop_item_resbox_ids.setdefault(res_id, {})[res_lv] = box_id
                
    # 获取区域道具升级材料列表 m[item_id][level][material_id] = quantity
    area_item_lv_materials: dict[int, dict[int, dict[int, int]]] = {}
    for item_id in item_ids:
        for lv, resbox_id in area_item_lv_shop_item_resbox_ids[item_id].items():
            for cost in (await ctx.md.shop_items.find_by('resourceBoxId', resbox_id)).get('costs', []):
                cost = cost['cost']
                res_id = cost['resourceId']
                if cost['resourceType'] == 'coin':
                    res_id = COIN_ID
                quantity = cost['quantity']
                area_item_lv_materials.setdefault(item_id, {}).setdefault(lv, {})[res_id] = quantity

    # 计算从玩家当前等级到目标等级所需材料（没有提供profile则从0累计）
    area_item_lv_sum_materials: dict[int, dict[int, dict[int, dict]]] = {}
    for item_id, lv_materials in area_item_lv_materials.items():
        user_lv = user_area_item_lvs.get(item_id, 0)
        sum_materials: dict[int, int] = {}
        # 枚举等级和材料
        for lv in range(user_lv + 1, area_item_max_levels[item_id] + 1):
            for mid, quantity in lv_materials[lv].items():
                sum_materials[mid] = sum_materials.get(mid, 0) + quantity
                area_item_lv_sum_materials.setdefault(item_id, {}).setdefault(lv, {})[mid] = sum_materials[mid]

    def get_quant_text(q: int) -> str:
        if q >= 10000000:
            return f"{q//10000000}kw"
        elif q >= 10000:
            x, y = q//10000, (q%10000)//1000
            if x < 10 and y > 0:
                return f"{x}w{y}"
            return f"{x}w"
        elif q >= 1000:
            x, y = q//1000, (q%1000)//100
            if x < 10 and y > 0:
                return f"{x}k{y}"
            return f"{x}k"
        else:
            return str(q)
    
    # 绘图
    gray_color, red_color, green_color = (50, 50, 50), (200, 0, 0), (0, 200, 0)
    ok_color = green_color if profile else gray_color
    no_color = red_color if profile else gray_color
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            if profile:
                await get_detailed_profile_card(ctx, profile, pmsg)

            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_bg(roundrect_bg()).set_padding(8):
                for item_id, lv_materials in area_item_lv_materials.items():
                    lv_sum_materials = area_item_lv_sum_materials.get(item_id, {})
                    current_lv = user_area_item_lvs.get(item_id, 0)
                    # 每个道具的列
                    with VSplit().set_content_align('l').set_item_align('l').set_sep(8).set_item_bg(roundrect_bg()).set_padding(8):
                        # 列头
                        with HSplit().set_content_align('c').set_item_align('c').set_omit_parent_bg(True):
                            ImageBox(area_item_target_icons.get(item_id, UNKNOWN_IMG), size=(None, 64))
                            ImageBox(area_item_icons.get(item_id, UNKNOWN_IMG), size=(128, 64), image_size_mode='fit') \
                                .set_content_align('c')
                            if current_lv:
                                TextBox(f"Lv.{current_lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=gray_color))

                        lv_can_upgrade = True
                        for lv in range(user_area_item_lower_lv + 1, area_item_max_levels[item_id] + 1):
                            # 统计道具是否足够
                            if lv > current_lv:
                                material_is_enough: dict[int, bool] = {}
                                for mid, quantity in lv_sum_materials[lv].items():
                                    material_is_enough[mid] = user_materials.get(mid, 0) >= quantity
                                lv_can_upgrade = lv_can_upgrade and all(material_is_enough.values())

                            # 列项
                            with HSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(8):
                                bonus_text = f"+{area_item_level_bonuses[item_id][lv]:.1f}%"
                                with VSplit().set_content_align('c').set_item_align('c').set_sep(4):
                                    color = ok_color if lv_can_upgrade else no_color
                                    if lv <= current_lv:
                                        color = gray_color
                                    TextBox(f"{lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=color))
                                    TextBox(bonus_text, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=gray_color)).set_w(64)

                                if lv <= current_lv:
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(4):
                                        Spacer(w=64, h=64)
                                        TextBox(" ", TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=gray_color))
                                else:
                                    for mid, quantity in lv_materials[lv].items():
                                        with VSplit().set_content_align('c').set_item_align('c').set_sep(4):
                                            material_icon = await get_res_icon(ctx, 'coin' if mid == COIN_ID else 'material', mid)
                                            quantity_text = get_quant_text(quantity)
                                            have_text = get_quant_text(user_materials.get(mid, 0))
                                            sum_text = get_quant_text(lv_sum_materials[lv][mid])
                                            with Frame():
                                                sz = 64
                                                ImageBox(material_icon, size=(sz, sz))
                                                TextBox(f"x{quantity_text}", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50))) \
                                                    .set_offset((sz, sz)).set_offset_anchor('rb')
                                            color = ok_color if material_is_enough.get(mid) else no_color
                                            text = f"{have_text}/{sum_text}" if profile else f"{sum_text}"
                                            TextBox(text, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=color))

    add_watermark(canvas)

    # 缓存full查询
    cache_key = None
    if profile is None:
        cache_key = f"{ctx.region}_area_item_{filter.unit}_{filter.cid}_{filter.attr}_{filter.flower}_{filter.tree}"
    return await canvas.get_img(scale=0.75, cache_key=cache_key)

# 合成羁绊等级图片
async def compose_bonds_image(ctx: SekaiHandlerContext, qid: int, cid: int | None) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userBonds', 'userCharacters'),
        raise_exc=True)
    
    user_bonds = profile.userBonds
    assert_and_reply(user_bonds, "你的Suite数据来源没有提供userBonds数据")

    def extract_cid_from_bgid(bgid: int) -> tuple[int, int]:
        return bgid // 100 % 100, bgid % 100

    # 收集羁绊等级需要的经验信息
    bond_level_total_exps: dict[int, int] = {}
    max_level = 0
    for item in await ctx.md.levels.find_by('levelType', 'bonds', mode='all'):
        lv, exp = item['level'], item['totalExp']
        bond_level_total_exps[lv] = exp
        max_level = max(max_level, lv)
    
    # 收集所有的羁绊角色
    bonds: dict[tuple[int, int], dict] = {}
    for item in await ctx.md.bonds.get():
        c1, c2 = extract_cid_from_bgid(item['groupId'])
        bonds[(c1, c2)] = {}
    
    # 收集用户的羁绊等级
    for item in user_bonds:
        c1, c2 = extract_cid_from_bgid(item['bondsGroupId'])
        bonds[(c1, c2)] = item

    # 收集用户的角色等级
    character_ranks = {}
    for item in profile.userCharacters:
        character_ranks[item['characterId']] = item['characterRank']

    if cid is not None:
        # 只保留与cid相关的羁绊，并且调整cid在前
        for c1, c2 in list(bonds.keys()):
            if c1 != cid and c2 != cid:
                bonds.pop((c1, c2))
            elif c1 != cid:
                bonds[(cid, c1)] = bonds.pop((c1, c2))
        bond_keys = [(cid, i) for i in range(1, 27) if i != cid]
    else:
        # 保留羁绊等级前topk的角色，并调整角色等级高的在前
        TOPK = 25
        bond_keys = sorted(bonds.keys(), key=lambda k: bonds[k]['rank'] if bonds[k] else 0, reverse=True)[:TOPK]
        bond_keys = [(x, y) if character_ranks.get(x, 0) >= character_ranks.get(y, 0) else (y, x) for x, y in bond_keys]
        bonds = { k: bonds.get(k, bonds.get((k[1], k[0]), 0)) for k in bond_keys }
        
    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    w1, w2, w3, w4, w5 = 100, 120, 100, 350, 150

    # 绘图
    async def get_chara_color(c: int):
        return color_code_to_rgb((await ctx.md.game_character_units.find_by_id(c))['colorCode'])

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, err_msg)
            with VSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                # 标题
                with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_h(header_h).set_padding(4).set_bg(roundrect_bg()):
                    TextBox("角色", header_style).set_w(w1).set_content_align('c')
                    TextBox("角色等级", header_style).set_w(w2).set_content_align('c')
                    TextBox("羁绊等级", header_style).set_w(w3).set_content_align('c')
                    TextBox(f"进度(上限{max_level}级)", header_style).set_w(w4).set_content_align('c')
                    TextBox("升级经验", header_style).set_w(w5).set_content_align('c')

                # 项目
                index = 0
                for key in bond_keys:
                    c1, c2 = key
                    bg_color = (255, 255, 255, 150) if index % 2 == 0 else (255, 255, 255, 100)
                    index += 1

                    has_bond = key in bonds
                    
                    level = 0
                    if has_bond and bonds[key]:
                        level = bonds[key]['rank']

                    level_text, need_exp_text = "-", "-"
                    if has_bond:
                        if level:
                            level_text = str(level)
                        if level == max_level:
                            need_exp_text = "MAX"
                        elif level > 0:
                            exp = bonds[key]['exp'] if bonds[key] else 0
                            level_exp = bond_level_total_exps[level + 1] - bond_level_total_exps[level]
                            need_exp_text = str(level_exp - exp)

                    color1 = await get_chara_color(c1)
                    color2 = await get_chara_color(c2)

                    crank1 = character_ranks.get(c1, 0)
                    crank2 = character_ranks.get(c2, 0)
                    chara_rank_text = f"{crank1} & {crank2}"

                    level_color = (50, 50, 50, 255)
                    if min(crank1, crank2) <= level and level < max_level:
                        level_color = (150, 0, 0, 255)

                    with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_h(row_h).set_padding(4).set_bg(roundrect_bg(fill=bg_color)):
                        with Frame().set_w(w1).set_content_align('c'):
                            ImageBox(get_chara_icon_by_chara_id(c1),   size=(None, 40)).set_offset((-13, 0))
                            ImageBox(get_chara_icon_by_chara_id(c2),    size=(None, 40)).set_offset((13, 0))

                        TextBox(chara_rank_text, text_style.replace(font=DEFAULT_BOLD_FONT, color=level_color)).set_w(w2).set_content_align('c')
                        TextBox(level_text, text_style.replace(font=DEFAULT_BOLD_FONT, color=level_color)).set_w(w3).set_content_align('c')

                        with Frame().set_w(w4).set_content_align('lt'):
                            x = level
                            progress = max(min(x / max_level, 1), 0)
                            total_w, total_h, border = w4, 14, 2
                            progress_w = int((total_w - border * 2) * progress)
                            progress_h = total_h - border * 2
                            color = LinearGradient(c1=color1, c2=color2, p1=(0, 0.5), p2=(1, 0.5))
                            if has_bond and progress > 0:
                                Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 255), radius=total_h//2))
                                Spacer(w=progress_w, h=progress_h).set_bg(RoundRectBg(fill=color, radius=(total_h-border)//2)).set_offset((border, border))

                                def draw_line(line_x: int):
                                    p = line_x / max_level
                                    if p <= 0 or p >= 1: return
                                    lx = int((total_w - border * 2) * p)
                                    color = (100, 100, 100, 255) if line_x < x else (150, 150, 150, 255)
                                    Spacer(w=1, h=total_h//2-1).set_bg(FillBg(color)).set_offset((border + lx - 1, total_h//2))
                                for line_x in range(0, max_level, 10):
                                    draw_line(line_x)
                            else:
                                Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 100), radius=total_h//2))

                        TextBox(need_exp_text, text_style).set_w(w5).set_content_align('c')

    add_watermark(canvas)
    return await canvas.get_img()

# 合成队长次数图片
async def compose_leader_count_image(ctx: SekaiHandlerContext, qid: int) -> Image.Image:
    profile, err_msg = await get_detailed_profile(
        ctx, 
        qid, 
        filter=get_detailed_profile_card_filter('userCharacterMissionV2s', 'userCharacterMissionV2Statuses'),
        raise_exc=True)

    ucms = profile.userCharacterMissionV2s
    ucm_ss = profile.userCharacterMissionV2Statuses
    assert_and_reply(ucms, "你的Suite数据来源没有提供userCharacterMissionV2s数据")
    assert_and_reply(ucm_ss, "你的Suite数据来源没有提供userCharacterMissionV2Statuses数据")

    # 获取游玩次数上限和ex每次次数
    max_playcount = 0
    ex_seq_pc_list: list[tuple[int, int]] = []
    for item in await ctx.md.character_mission_v2_parameter_groups.find_by('id', 1, mode='all'):
        max_playcount = max(max_playcount, item['requirement'])
    for item in await ctx.md.character_mission_v2_parameter_groups.find_by('id', 101, mode='all'):
        ex_seq_pc_list.append((item['seq'], item['requirement']))
    ex_seq_pc_list.append((100000, 0))
    ex_seq_pc_list.sort()
    
    # 收集用户游玩次数
    playcounts: dict[int, int] = {}
    playcounts_ex: dict[int, int] = {}
    ex_level: dict[int, int] = {}
    for item in find_by(ucms, 'characterMissionType', 'play_live', mode='all'):
        playcounts[item['characterId']] = item['progress']
    for item in find_by(ucms, 'characterMissionType', 'play_live_ex', mode='all'):
        playcounts_ex[item['characterId']] = item['progress']
        ex_level[item['characterId']] = 0
    for item in find_by(ucm_ss, 'parameterGroupId', 101, mode='all'):
        cid, seq = item['characterId'], item['seq']
        ex_level[cid] = max(ex_level.get(cid, 0), seq)
        for i in range(len(ex_seq_pc_list)):
            if ex_seq_pc_list[i+1][0] > seq:
                playcounts_ex[cid] += ex_seq_pc_list[i][1]
                break
        
    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    w1, w2, w3, w4, w5 = 80, 100, 100, 100, 350

    # 绘图
    async def get_chara_color(c: int):
        return color_code_to_rgb((await ctx.md.game_character_units.find_by_id(c))['colorCode'])

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, err_msg)
            with VSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(16).set_bg(roundrect_bg()):

                # 标题
                with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_h(header_h).set_padding(4).set_bg(roundrect_bg()):
                    TextBox("角色", header_style).set_w(w1).set_content_align('c')
                    TextBox("队长次数", header_style).set_w(w2).set_content_align('c')
                    TextBox("EX等级", header_style).set_w(w3).set_content_align('c')
                    TextBox("EX次数", header_style).set_w(w4).set_content_align('c')
                    TextBox(f"进度(上限{max_playcount})", header_style).set_w(w5).set_content_align('c')

                # 项目
                index = 0
                for cid in range(1, 27):
                    bg_color = (255, 255, 255, 150) if index % 2 == 0 else (255, 255, 255, 100)
                    index += 1

                    pc = 0 if cid not in playcounts else playcounts[cid]
                    pc_text = "-" if cid not in playcounts else str(playcounts[cid])
                    pc_ex_text = "-" if cid not in playcounts_ex else str(playcounts_ex[cid])
                    ex_level_text = "-" if cid not in ex_level else f"x{ex_level[cid]}"

                    with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_h(row_h).set_padding(4).set_bg(roundrect_bg(fill=bg_color)):
                        with Frame().set_w(w1).set_content_align('c'):
                            ImageBox(get_chara_icon_by_chara_id(cid), size=(None, 40))

                        TextBox(pc_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w2).set_content_align('c')
                        TextBox(ex_level_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w3).set_content_align('c')
                        TextBox(pc_ex_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w4).set_content_align('c')

                        with Frame().set_w(w5).set_content_align('lt'):
                            x = pc
                            progress = max(min(x / max_playcount, 1), 0)
                            total_w, total_h, border = w5, 14, 2
                            progress_w = int((total_w - border * 2) * progress)
                            progress_h = total_h - border * 2
                            color = (255, 50, 50, 255)
                            if x > 50000:    color = (100, 255, 100, 255)
                            elif x > 40000:  color = (255, 255, 100, 255)
                            elif x > 30000:  color = (255, 200, 100, 255)
                            elif x > 20000:  color = (255, 150, 100, 255)
                            elif x > 10000:  color = (255, 100, 100, 255)
                            if progress > 0:
                                Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 255), radius=total_h//2))
                                Spacer(w=progress_w, h=progress_h).set_bg(RoundRectBg(fill=color, radius=(total_h-border)//2)).set_offset((border, border))

                                def draw_line(line_x: int):
                                    p = line_x / max_playcount
                                    if p <= 0 or p >= 1: return
                                    lx = int((total_w - border * 2) * p)
                                    color = (100, 100, 100, 255) if line_x < x else (150, 150, 150, 255)
                                    Spacer(w=1, h=total_h//2-1).set_bg(FillBg(color)).set_offset((border + lx - 1, total_h//2))
                                for line_x in range(0, max_playcount, 10000):
                                    draw_line(line_x)
                            else:
                                Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 100), radius=total_h//2))

    add_watermark(canvas)
    return await canvas.get_img()



# ======================= 指令处理 ======================= #

# 挑战信息
pjsk_challenge_info = SekaiCmdHandler([
    "/pjsk challenge info", "/pjsk_challenge_info",
    "/挑战信息", "/挑战详情", "/挑战进度", "/挑战一览", "/每日挑战", 
])
pjsk_challenge_info.check_cdrate(cd).check_wblist(gbl)
@pjsk_challenge_info.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_challenge_live_detail_image(ctx, ctx.user_id),
        low_quality=True,
    ))


# 加成信息
pjsk_power_bonus_info = SekaiCmdHandler([
    "/pjsk power bonus info", "/pjsk_power_bonus_info",
    "/加成信息", "/加成详情", "/加成进度", "/加成一览", "/角色加成",
])
pjsk_power_bonus_info.check_cdrate(cd).check_wblist(gbl)
@pjsk_power_bonus_info.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_power_bonus_detail_image(ctx, ctx.user_id),
        low_quality=True,
    ))


# 查询区域道具升级材料
pjsk_area_item = SekaiCmdHandler([
    "/pjsk area item", "/area item",
    "/区域道具", "/区域道具升级", "/区域道具升级材料",
])
pjsk_area_item.check_cdrate(cd).check_wblist(gbl)
@pjsk_area_item.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    HELP_TEXT = f"""
可用参数: 团名/角色名/属性/树/花
加上"all"可以查询所有级别材料，不加则查询你的账号的升级情况，示例：
"{ctx.original_trigger_cmd} 树" 所有树
"{ctx.original_trigger_cmd} miku" miku的道具
"{ctx.original_trigger_cmd} 25h" 25的SEKAI里的所有区域道具
"{ctx.original_trigger_cmd} miku all" miku的道具所有等级
""".strip()

    qid = ctx.user_id
    for keyword in ('all', 'full'):
        if keyword in args:
            qid = None
            args = args.replace(keyword, '').strip()
            break

    tree = False
    for keyword in ('树',):
        if keyword in args:
            tree = True
            args = args.replace(keyword, '').strip()
            break
    flower = False
    for keyword in ('花',):
        if keyword in args:
            flower = True
            args = args.replace(keyword, '').strip()
            break
    unit, args = extract_unit(args)
    attr, args = extract_card_attr(args)
    cid = get_cid_by_nickname(args)

    assert_and_reply(unit or attr or cid or tree or flower, HELP_TEXT)

    filter = AreaItemFilter(
        unit=unit,
        attr=attr,
        cid=cid,
        tree=tree,
        flower=flower,
    )
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_area_item_upgrade_materials_image(ctx, qid, filter),
        low_quality=True,
    ))


# 查询羁绊等级
pjsk_bonds = SekaiCmdHandler([
    "/pjsk bonds", "/pjsk bond",
    "/羁绊", "/羁绊等级", "/角色羁绊", "/羁绊信息", 
    "/牵绊等级", "/牵绊", "/角色牵绊", "/牵绊信息",
])
pjsk_bonds.check_cdrate(cd).check_wblist(gbl)
@pjsk_bonds.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    if args:
        cid = get_cid_by_nickname(args)
        assert_and_reply(cid is not None, f"请指定其中一个角色名称")
    else:
        cid = None

    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_bonds_image(ctx, ctx.user_id, cid),
        low_quality=True,
    ))


# 查询队长次数
pjsk_leader_count = SekaiCmdHandler([
    "/pjsk leader count",
    "/队长次数", "/角色次数", "/队长游玩次数", "/角色游玩次数",
])
pjsk_leader_count.check_cdrate(cd).check_wblist(gbl)
@pjsk_leader_count.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_leader_count_image(ctx, ctx.user_id),
        low_quality=True,
    ))
