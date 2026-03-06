from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *


HONOR_DIFF_SCORE_MAP = {
    3009: ("easy", "fullCombo"),
    3010: ("normal", "fullCombo"),
    3011: ("hard", "fullCombo"),
    3012: ("expert", "fullCombo"),
    3013: ("master", "fullCombo"),
    3014: ("master", "allPerfect"),
    4700: ("append", "fullCombo"),
    4701: ("append", "allPerfect"),
}

bonds_honor_sd_face_pos_config = Config('sekai.bonds_honor_sd_face_pos')


# ======================= 处理逻辑 ======================= #

# 从cuid获取cid
async def get_cid_by_cuid(ctx: SekaiHandlerContext, cuid: int):
    return (await ctx.md.game_character_units.find_by_id(cuid))['gameCharacterId']

# 获取某个vs对应团的cuid
async def get_vs_cuid(ctx: SekaiHandlerContext, cid: int, unit: str):
    for item in await ctx.md.game_character_units.get():
        if item['gameCharacterId'] == cid and item['unit'] == unit:
            return item['id']
    raise ValueError(f"未找到vs {cid} 在组合 {unit} 的cuid")

# 合成完整头衔图片
async def compose_full_honor_image(ctx: SekaiHandlerContext, profile_honor: Dict, is_main: bool, profile=None):
    logger.info(f"合成头衔 profile_honor={profile_honor}, is_main={is_main}")
    if profile_honor is None:
        ms = 'm' if is_main else 's'
        img = ctx.static_imgs.get(f'honor/empty_honor_{ms}.png')
        padding = 3
        bg = Image.new('RGBA', (img.size[0] + padding * 2, img.size[1] + padding * 2), (0, 0, 0, 0))
        bg.paste(img, (padding, padding), img)
        return bg
    hid = profile_honor['honorId']
    htype = profile_honor.get('profileHonorType', 'normal')
    hwid = profile_honor.get('bondsHonorWordId', 0)
    hlv = profile_honor.get('honorLevel', 0)
    ms = "main" if is_main else "sub"

    async def add_frame(img: Image.Image, rarity: str, frame_name: str=None, level: int=None):
        RARE_MAP = {'low': 1, 'middle': 2, 'high': 3, 'highest': 4}
        r = RARE_MAP.get(rarity, 1)
        try:
            assert frame_name
            start_rare = 2
            if frame_name.startswith('event'):  # wl牌子从3阶开始有特殊框
                start_rare = 3
            assert r >= start_rare
            frame = await ctx.rip.img(f'honor_frame/{frame_name}/frame_degree_{ms[0]}_{r}.png')
        except:
            frame = ctx.static_imgs.get(f'honor/frame_degree_{ms[0]}_{r}.png')
        img.paste(frame, (8, 0) if rarity == 'low' else (0, 0), frame)
        # 添加生日牌子的等级标志
        if frame_name and frame_name.startswith('honor_frame_birthday'):
            icon = await ctx.rip.img(f'honor_frame/{frame_name}/frame_degree_level_{r}.png')
            w, h = img.size
            sz = 18
            icon = icon.resize((sz, sz))
            for i in range(level):
                img.paste(icon, (int(w / 2 - sz * level / 2 + i * sz), h - sz), icon)

    def add_lv_star(img: Image.Image, lv):
        if lv > 10: lv = lv - 10
        lv_img = ctx.static_imgs.get('honor/icon_degreeLv.png')
        lv6_img = ctx.static_imgs.get('honor/icon_degreeLv6.png')
        for i in range(0, min(lv, 5)):
            img.paste(lv_img, (50 + 16 * i, 61), lv_img)
        for i in range(5, lv):
            img.paste(lv6_img, (50 + 16 * (i - 5), 61), lv6_img)

    def add_fcap_lv(img: Image.Image, profile):
        try:
            diff_count = profile['userMusicDifficultyClearCount']
            diff, score = HONOR_DIFF_SCORE_MAP[hid]
            lv = str(find_by(diff_count, 'musicDifficultyType', diff)[score])
        except:
            lv = "?"
        font = get_font(path=DEFAULT_BOLD_FONT, size=22)
        text_w, _ = get_text_size(font, lv)
        offset = 215 if is_main else 37
        draw = ImageDraw.Draw(img)
        draw.text((offset + 50 - text_w // 2, 46), lv, font=font, fill=WHITE)

    def get_bond_bg(c1, c2, is_main, swap):
        if swap: c1, c2 = c2, c1
        suffix = '_sub' if not is_main else ''
        img1 = ctx.static_imgs.get(f'honor/bonds/{c1}{suffix}.png').copy()
        img2 = ctx.static_imgs.get(f'honor/bonds/{c2}{suffix}.png').copy()
        x = 190 if is_main else 90
        img2 = img2.crop((x, 0, 380, 80))
        img1.paste(img2, (x, 0))
        return img1
  
    if htype == 'normal':
        # 普通牌子
        honor = await ctx.md.honors.find_by_id(hid)
        group_id = honor['groupId']
        try:
            level_honor = find_by(honor['levels'], 'level', hlv)
            asset_name = level_honor['assetbundleName']
            rarity = level_honor['honorRarity']
        except:
            asset_name = honor['assetbundleName']
            rarity = honor['honorRarity']

        group = await ctx.md.honor_groups.find_by_id(group_id)
        bg_asset_name = group.get('backgroundAssetbundleName', None)
        gtype = group['honorType']
        gname = group['name']
        frame_name = group.get('frameName', None)
        
        if gtype == 'rank_match':
            img = (await ctx.rip.img(f"rank_live/honor/{bg_asset_name or asset_name}_rip/degree_{ms}.png")).copy()
            rank_img = await ctx.rip.img(f"rank_live/honor/{asset_name}_rip/{ms}.png", allow_error=True, default=None, timeout=3)
        else:
            img = (await ctx.rip.img(f"honor/{bg_asset_name or asset_name}_rip/degree_{ms}.png")).copy()
            if gtype == 'event' and bg_asset_name:
                rank_img = await ctx.rip.img(f"honor/{asset_name}_rip/rank_{ms}.png", allow_error=True, default=None, timeout=3)
            else:
                rank_img = None

        await add_frame(img, rarity, frame_name, hlv)
        if rank_img:
            if gtype == 'rank_match':   # 排位
                img.paste(rank_img, (190, 0) if is_main else (17, 42), rank_img)
            elif "event" in asset_name and 'top' in asset_name: # wl角色牌
                img.paste(rank_img, (0, 0) if is_main else (0, 0), rank_img)
            else:   # 普通活动
                img.paste(rank_img, (190, 0) if is_main else (34, 42), rank_img)

        if hid in HONOR_DIFF_SCORE_MAP.keys():
            scroll_img = await ctx.rip.img(f"honor/{asset_name}_rip/scroll.png", allow_error=True)
            if scroll_img:
                img.paste(scroll_img, (215, 3) if is_main else (37, 3), scroll_img)
            add_fcap_lv(img, profile)
        elif gtype == 'character' or gtype == 'achievement':
            add_lv_star(img, hlv)
        return img
    
    elif htype == 'bonds':
        # 羁绊牌子
        bhonor = await ctx.md.bonds_honnors.find_by_id(hid)
        cuid1 = bhonor['gameCharacterUnitId1']
        cuid2 = bhonor['gameCharacterUnitId2']
        rarity = bhonor['honorRarity']
        view_type = profile_honor['bondsHonorViewType'] 
        rev = 'reverse' in view_type

        img = get_bond_bg(cuid1, cuid2, is_main, rev)

        if 'unit_virtual_singer' in view_type:
            # 先将vs的id换到第一个
            swapped = False
            if cuid2 > 20:
                cuid1, cuid2 = cuid2, cuid1
                swapped = True
            # 找到vs对应的cuid
            cuid1 = await get_vs_cuid(ctx, cuid1, CID_UNIT_MAP[cuid2])
            # 换回去
            if swapped:
                cuid1, cuid2 = cuid2, cuid1

        c1_img = await ctx.rip.img(f"bonds_honor/character/chr_sd_{cuid1:02d}_01.png")
        c2_img = await ctx.rip.img(f"bonds_honor/character/chr_sd_{cuid2:02d}_01.png")
        face_pos = bonds_honor_sd_face_pos_config.get_all() or {}
        c1_face = face_pos.get(cuid1, c1_img.size[0] // 2)
        c2_face = face_pos.get(cuid2, c2_img.size[0] // 2)

        if rev: 
            c1_img, c2_img = c2_img, c1_img
            c1_face, c2_face = c2_face, c1_face

        w, h = img.size
        scale = 0.8
        c1_img = resize_keep_ratio(c1_img, scale, mode='scale')
        c2_img = resize_keep_ratio(c2_img, scale, mode='scale')
        c1w, c1h = c1_img.size
        c2w, c2h = c2_img.size
        c1_face = int(c1_face * scale)
        c2_face = int(c2_face * scale)

        offset_to_mid = 120 if is_main else 30
        mid = w // 2
        c1_face_x = mid - offset_to_mid
        c2_face_x = mid + offset_to_mid

        overlap1 = (c1_face_x - c1_face + c1w) - mid
        if overlap1 > 0:
            c1_img = c1_img.crop((0, 0, c1w - overlap1, c1h))
        overlap2 = mid - (c2_face_x - c2_face)
        if overlap2 > 0:
            c2_img = c2_img.crop((overlap2, 0, c2w, c2h))
            c2_face -= overlap2

        img.paste(c1_img, (c1_face_x - c1_face, h - c1h), c1_img)
        img.paste(c2_img, (c2_face_x - c2_face, h - c2h), c2_img)

        _, _, _, mask = ctx.static_imgs.get(f"honor/mask_degree_{ms}.png").split()
        img.putalpha(mask)

        await add_frame(img, rarity)

        if is_main:
            cid1 = await get_cid_by_cuid(ctx, cuid1)
            cid2 = await get_cid_by_cuid(ctx, cuid2)
            if abs(hid - hwid) < 100:
                wordbundlename = f"honorname_{cid1:02d}{cid2:02d}_{(hwid%100):02d}_01"
            else:
                if hwid % 10 == 1:
                    wordbundlename = f"honorname_{cid1:02d}{cid2:02d}_default_{cuid1:02d}{cid2:02d}_01"
                else:
                    wordbundlename = f"honorname_{cid1:02d}{cid2:02d}_default_{cid2:02d}{cuid1:02d}_01"
            word_img = await ctx.rip.img(f"bonds_honor/word/{wordbundlename}.png")
            img.paste(word_img, (int(190-(word_img.size[0]/2)), int(40-(word_img.size[1]/2))), word_img)

        add_lv_star(img, hlv)
        return img

    raise NotImplementedError()
    
