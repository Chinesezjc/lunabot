from ...utils import *
import shutil
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from .profile import get_player_bind_id, get_basic_profile
from ..custom_profile.drawer import compose_custom_profile_card_image
from ..custom_profile.model import CustomProfileCardRenderRequest
from ..custom_profile.split import build_profile_context


# /cp 渲染串行化（单次渲染约需 128MB 内存，防并发 OOM）
_custom_profile_render_lock = asyncio.Lock()

CUSTOM_PROFILE_HELP = """使用方式:
/自定义个人信息 1
/自定义个人信息 2
/自定义个人信息 u2 3

数字为要渲染的自定义个人信息页序号，每次只渲染一张。
u序号 可选择自己绑定列表中的账号。"""


# ======================= 资源收集 ======================= #

def _collect_ids(card: dict, profile: dict) -> dict:
    """
    从档案布局 + profile 响应收集各类资源 ID（对照 Haruki-Cloud newCustomProfileResourceCollector）
    """
    data = card.get('customProfileCard') or {}
    ids = {
        'player_info': set(), 'general_bg': set(), 'story_bg': set(), 'stand_member': set(),
        'collection': set(), 'collection_targets': {}, 'other': set(), 'shape': set(),
        'text_color': set(), 'text_font': set(), 'stamp': set(), 'character_icon': set(),
        'material': set(), 'user_interface': set(), 'card': set(),
    }

    def add(target: set, value):
        try:
            v = int(value)
            if v > 0:
                target.add(v)
        except (TypeError, ValueError):
            pass

    for item in data.get('generals') or []:
        # renderer 的 content_data_id 对 general 用 type 字段作为资源 key
        add(ids['player_info'], item.get('type') or item.get('playerInfoResourceId') or item.get('id'))
    for item in data.get('generalBackgrounds') or []:
        add(ids['general_bg'], item.get('id'))
    for item in data.get('storyBackgrounds') or []:
        add(ids['story_bg'], item.get('id'))
    for item in data.get('standMembers') or []:
        add(ids['stand_member'], item.get('id'))
    for item in data.get('collections') or []:
        add(ids['collection'], item.get('id'))
        ids['collection_targets'].setdefault(int(item.get('id') or 0), set())
        add(ids['collection_targets'][int(item.get('id') or 0)], item.get('targetId'))
    for item in data.get('others') or []:
        add(ids['other'], item.get('id'))
    for item in data.get('shapes') or []:
        add(ids['shape'], item.get('id'))
        add(ids['text_color'], item.get('colorId'))
        add(ids['text_color'], item.get('outlineColorId'))
    for item in data.get('texts') or []:
        add(ids['text_color'], item.get('colorId'))
        add(ids['text_color'], item.get('outlineColorId'))
        add(ids['text_font'], item.get('fontId'))
    for item in data.get('stamps') or []:
        add(ids['stamp'], item.get('id'))
    for item in data.get('characterIcons') or []:
        add(ids['character_icon'], item.get('id'))
    for item in data.get('materials') or []:
        add(ids['material'], item.get('id'))
    for item in data.get('userInterfaceIcons') or []:
        add(ids['user_interface'], item.get('id'))
    for item in data.get('cardMembers') or []:
        add(ids['card'], item.get('id'))

    # profile 主队卡
    deck = (profile or {}).get('userDeck') or {}
    for key in ('leader', 'member1', 'member2', 'member3', 'member4', 'member5'):
        add(ids['card'], deck.get(key))

    return ids


def _resolve_custom_profile_resource_image_path(resource: dict, fallback_dir: str = "") -> str:
    """
    由 resourceLoadVal + fileName 生成 rip 相对路径（对照 Haruki resolveCustomProfileResourceImagePath）
    """
    file_name = str(resource.get('fileName') or "").strip('/')
    if not file_name:
        return ""
    if not file_name.lower().endswith('.png'):
        file_name += '.png'
    load_val = str(resource.get('resourceLoadVal') or "").strip('/')
    rels = []
    if load_val.startswith('custom_profile/'):
        rels.append(f"{load_val}/{file_name}")
    elif load_val == 'custom_profile':
        rels.append(f"custom_profile/{file_name}")
    elif load_val:
        rels.append(f"custom_profile/{load_val}/{file_name}")
        rels.append(f"{load_val}/{file_name}")
    elif fallback_dir:
        rels.append(f"custom_profile/{fallback_dir}/{file_name}")
    else:
        rels.append(f"custom_profile/{file_name}")
    return rels[0]


async def _load_master_subset(ctx: SekaiHandlerContext, wrapper, ids: set) -> dict:
    """
    按 id 子集从 masterdata 加载，返回 {id: row}
    """
    if not ids:
        return {}
    rows = await wrapper.collect_by_ids(list(ids))
    return {int(row.get('id') or 0): row for row in rows if isinstance(row, dict)}


async def build_custom_profile_resources(
    ctx: SekaiHandlerContext,
    region: str,
    card: dict,
    profile: dict,
) -> dict:
    """
    收集自定义档案渲染所需资源（对照 Haruki-Cloud buildCustomProfileResources）
    返回 resources dict（renderer 的 load_resource_index 直接消费）
    """
    resources: dict = {}

    # 角色图标路径映射
    chara_map = {}
    for cid in range(1, 27):
        nickname = get_character_first_nickname(cid)
        if nickname:
            chara_map[str(cid)] = f"static_images/chara_icon/{nickname}.png"
    resources["charaRankIconPathMap"] = chara_map

    ids = _collect_ids(card, profile)

    # 12 张 customProfile master 表
    tables = [
        ("customProfileTextColors", ctx.md.custom_profile_text_colors, ids['text_color'], False, ""),
        ("customProfileTextFonts", ctx.md.custom_profile_text_fonts, ids['text_font'], False, ""),
        ("customProfileShapeResources", ctx.md.custom_profile_shape_resources, ids['shape'], True, "shape"),
        ("customProfilePlayerInfoResources", ctx.md.custom_profile_player_info_resources, ids['player_info'], True, ""),
        ("customProfileGeneralBackgroundResources", ctx.md.custom_profile_general_background_resources, ids['general_bg'], True, ""),
        ("customProfileStoryBackgroundResources", ctx.md.custom_profile_story_background_resources, ids['story_bg'], True, ""),
        ("customProfileMemberStandingPictureResources", ctx.md.custom_profile_member_standing_picture_resources, ids['stand_member'], True, ""),
        ("customProfileCollectionResources", ctx.md.custom_profile_collection_resources, ids['collection'], True, ""),
        ("customProfileEtcResources", ctx.md.custom_profile_etc_resources, ids['other'], True, ""),
        ("customProfileCharacterIconResources", ctx.md.custom_profile_character_icon_resources, ids['character_icon'], True, ""),
        ("customProfileMaterialResources", ctx.md.custom_profile_material_resources, ids['material'], True, ""),
        ("customProfileUserInterfaceIconResources", ctx.md.custom_profile_user_interface_icon_resources, ids['user_interface'], True, ""),
    ]
    collection_rows = None
    for key, wrapper, id_set, with_path, fallback_dir in tables:
        rows = await _load_master_subset(ctx, wrapper, id_set)
        if with_path:
            for row in rows.values():
                if image_path := _resolve_custom_profile_resource_image_path(row, fallback_dir):
                    row['imagePath'] = image_path
        resources[key] = rows
        if key == "customProfileCollectionResources":
            collection_rows = rows

    # omikujis（collection 类型为 omikuji 的 targetId）
    omikuji_ids = set()
    if collection_rows:
        for rid, row in collection_rows.items():
            if str(row.get('customProfileResourceCollectionType') or "").lower() == 'omikuji':
                omikuji_ids.update(ids['collection_targets'].get(rid, set()))
    if omikuji_ids:
        resources["omikujis"] = await _load_master_subset(ctx, ctx.md.omikujis, omikuji_ids)

    # 贴纸
    if ids['stamp']:
        rows = await ctx.md.stamps.collect_by_ids(list(ids['stamp']))
        stamps = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp_id = int(row.get('id') or 0)
            if stamp_id:
                stamps[stamp_id] = {
                    'id': stamp_id,
                    'assetbundleName': row.get('assetbundleName'),
                    'characterId': row.get('characterId'),
                }
        resources["stamps"] = stamps

    # 卡牌（含 renderer 拼路径所需的字段）
    if ids['card']:
        rows = await ctx.md.cards.collect_by_ids(list(ids['card']))
        cards = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            card_id = int(row.get('id') or 0)
            if card_id:
                cards[card_id] = {
                    'id': card_id,
                    'characterId': row.get('characterId'),
                    'cardRarityType': row.get('cardRarityType'),
                    'attr': row.get('attr'),
                    'prefix': row.get('prefix'),
                    'assetbundleName': row.get('assetbundleName'),
                    'releaseAt': row.get('releaseAt'),
                    'initialSpecialTrainingStatus': row.get('initialSpecialTrainingStatus'),
                }
        resources["cards"] = cards

    # 称号相关 masterdata（renderer fallback 构建 honor request 需要）
    honor_ids = set()
    for item in (card.get('customProfileCard') or {}).get('honors') or []:
        try:
            hid = int(item.get('id') or 0)
            if hid > 0:
                honor_ids.add(hid)
        except (TypeError, ValueError):
            pass
    if honor_ids:
        rows = await ctx.md.honors.collect_by_ids(list(honor_ids))
        resources["honors"] = {int(r['id']): r for r in rows if isinstance(r, dict) and r.get('id')}
        group_ids = set()
        for r in rows:
            if isinstance(r, dict) and r.get('honorGroupId'):
                try:
                    group_ids.add(int(r['honorGroupId']))
                except (TypeError, ValueError):
                    pass
        if group_ids:
            groups = await ctx.md.honor_groups.collect_by_ids(list(group_ids))
            resources["honorGroups"] = {int(r['id']): r for r in groups if isinstance(r, dict) and r.get('id')}

    # 羁绊称号 masterdata（bondsHonors 元素）
    bonds_ids = set()
    for item in (card.get('customProfileCard') or {}).get('bondsHonors') or []:
        try:
            bid = int(item.get('id') or 0)
            if bid > 0:
                bonds_ids.add(bid)
        except (TypeError, ValueError):
            pass
    if bonds_ids:
        rows = await ctx.md.bonds_honnors.collect_by_ids(list(bonds_ids))
        resources["bondsHonors"] = {int(r['id']): r for r in rows if isinstance(r, dict) and r.get('id')}
        word_ids = set()
        for r in rows:
            if isinstance(r, dict) and r.get('bondsHonorWordId'):
                try:
                    word_ids.add(int(r['bondsHonorWordId']))
                except (TypeError, ValueError):
                    pass
        if word_ids:
            words = await ctx.md.bonds_honor_words.collect_by_ids(list(word_ids))
            resources["bondsHonorWords"] = {int(r['id']): r for r in words if isinstance(r, dict) and r.get('id')}

    # gameCharacterUnits（羁绊称号 SD 立绘等）
    try:
        units = await ctx.md.game_character_units.get()
        if isinstance(units, list):
            resources["gameCharacterUnits"] = {int(r['id']): r for r in units if isinstance(r, dict) and r.get('id')}
    except Exception as e:
        logger.warning(f"加载 gameCharacterUnits 失败: {get_exc_desc(e)}")

    return resources


# ======================= 素材预取 ======================= #

def _collect_asset_paths(resources: dict, card: dict, region: str) -> set:
    """
    收集渲染所需的所有 rip 相对素材路径（custom_profile 素材/贴纸/卡面/banner 等）
    """
    paths: set = set()
    for key in (
        "customProfileShapeResources", "customProfilePlayerInfoResources",
        "customProfileGeneralBackgroundResources", "customProfileStoryBackgroundResources",
        "customProfileMemberStandingPictureResources", "customProfileCollectionResources",
        "customProfileEtcResources", "customProfileCharacterIconResources",
        "customProfileMaterialResources", "customProfileUserInterfaceIconResources",
    ):
        for row in (resources.get(key) or {}).values():
            if isinstance(row, dict) and row.get('imagePath'):
                paths.add(row['imagePath'])

    # 贴纸
    for row in (resources.get('stamps') or {}).values():
        bundle = (row or {}).get('assetbundleName')
        if bundle:
            paths.add(f"stamp/{bundle}/{bundle}.png")

    # 卡面（normal/after_training 全图 + 小图 + 立绘 + clip）
    for row in (resources.get('cards') or {}).values():
        bundle = (row or {}).get('assetbundleName')
        if not bundle:
            continue
        for f in ("card_normal.png", "card_after_training.png"):
            paths.add(f"character/member/{bundle}/{f}")
            paths.add(f"character/member_small/{bundle}/{f}")
        for f in ("normal.png", "after_training.png", "deck.png",
                  "card_normal_trim.png", "card_after_training_trim.png"):
            paths.add(f"character/member_cutout/{bundle}/{f}")
            paths.add(f"character/member_cutout_trm/{bundle}/{f}")
        for f in ("normal.png", "after_training.png"):
            paths.add(f"thumbnail/chara/{bundle}_{f.replace('.png','')}.png")

    return paths


def _prepare_custom_profile_fonts(ctx: SekaiHandlerContext) -> None:
    """
    创建字体目录并软链思源黑体（游戏专有字体不可得，渲染器按文件名降级）
    """
    import os
    try:
        fonts_dir = f"{ctx.rip.cache_dir}/custom_profile/font"
        create_parent_folder(fonts_dir)
        src_font = "data/utils/fonts/SourceHanSansCN-Bold.otf"
        if not os.path.exists(os.path.join(fonts_dir, "FOT-RodinNTLGPro-DB.otf")):
            try:
                os.symlink(os.path.abspath(src_font), os.path.join(fonts_dir, "FOT-RodinNTLGPro-DB.otf"))
                os.symlink(os.path.abspath(src_font), os.path.join(fonts_dir, "FOT-RodinNTLGPro-DB.ttf"))
            except OSError:
                shutil.copyfile(src_font, os.path.join(fonts_dir, "FOT-RodinNTLGPro-DB.otf"))
    except Exception as e:
        logger.warning(f"准备自定义档案字体失败: {get_exc_desc(e)}")


async def preload_custom_profile_assets(ctx: SekaiHandlerContext, paths: set) -> None:
    """
    把素材下载到 rip 本地缓存（data/sekai/assets/{region}/），渲染器同步读取
    """
    _prepare_custom_profile_fonts(ctx)
    paths = {p for p in paths if p}
    if not paths:
        return
    results = await batch_gather_with_progress(
        *[_preload_one(ctx, p) for p in sorted(paths)],
        progress_name="加载自定义档案素材",
    )
    ok = sum(1 for r in results if r)
    logger.info(f"自定义档案素材加载完成: {ok}/{len(paths)}")


async def _preload_one(ctx: SekaiHandlerContext, path: str) -> bool:
    try:
        await ctx.rip.get_asset(path, use_cache=True, allow_error=True, timeout=15)
        return True
    except Exception as e:
        logger.debug(f"预取素材失败 {path}: {get_exc_desc(e)}")
        return False


# ======================= 指令 ======================= #

def _parse_custom_profile_seq(args: str) -> int:
    args = str(args or "").strip()
    if not args.isdigit():
        raise ReplyException(
            f"使用方式:\n/自定义个人信息 1\n/自定义个人信息 2\n/自定义个人信息 u2 3\n数字为要渲染的自定义个人信息页序号，每次只渲染一张"
        )
    seq = int(args)
    assert_and_reply(seq > 0, "自定义个人信息页序号必须是正整数")
    return seq


pjsk_custom_profile = SekaiCmdHandler([
    "/自定义个人信息", "/cp",
])
pjsk_custom_profile.check_cdrate(cd).check_wblist(gbl)


@pjsk_custom_profile.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    if args in ("help", "-help", "--help", "帮助"):
        return await ctx.asend_reply_msg(CUSTOM_PROFILE_HELP)

    seq = _parse_custom_profile_seq(args)
    uid = get_player_bind_id(ctx)

    profile = await get_basic_profile(
        ctx, int(uid), use_cache=True, use_remote_cache=True, raise_when_no_found=True,
    )
    cards = profile.get('userCustomProfileCards') or []
    assert_and_reply(cards, "当前公开profile中没有自定义档案")

    ordered = sorted(cards, key=lambda c: int(c.get('seq') or 0))
    assert_and_reply(seq <= len(ordered), f"未找到第{seq}页自定义档案，当前共有{len(ordered)}页")
    card = ordered[seq - 1]

    resources = await build_custom_profile_resources(ctx, ctx.region, card, profile)

    asset_paths = _collect_asset_paths(resources, card, ctx.region)
    await preload_custom_profile_assets(ctx, asset_paths)

    request = CustomProfileCardRenderRequest(
        region=ctx.region,
        card=card,  # 整个 userCustomProfileCards 元素（含 customProfileCard 嵌套）
        resources=resources,
        profile_context=build_profile_context(profile),
    )
    async with _custom_profile_render_lock:
        image = await compose_custom_profile_card_image(request)
    return await ctx.asend_reply_msg(await get_image_cq(image, low_quality=True))
