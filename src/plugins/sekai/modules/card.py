from ...utils import *
from ...llm import translate_text, ChatSession, get_model_preset, ChatSessionResponse
from ..common import *
from ..handler import *
from ..asset import *
from ..costume3d import Costume3D
from ..draw import *
from .profile import (
    get_detailed_profile, 
    get_detailed_profile_card, 
    get_detailed_profile_card_filter,
    get_player_avatar_info_by_detailed_profile,
    has_after_training,
    only_has_after_training,
    get_card_thumbnail,
    get_card_full_thumbnail,
    get_unit_by_card_id,
)
from .event import (
    get_event_detail, 
    get_event_banner_img, 
    extract_ban_event,
    get_card_supply_type,
)


SEARCH_SINGLE_CARD_HELP = """
查单张卡的方式:
1. 直接使用卡牌ID
2. 角色昵称+负数 代表角色新卡，例如 mnr-1 代表mnr最新一张卡
""".strip()

SEARCH_MULTI_CARD_HELP = """
查询多张卡牌的筛选参数:
角色昵称：miku
团/团oc/团vs/纯vs：mmj mmjoc mmjv 纯v
稀有度/属性/技能：4 四星 生日 蓝 蓝星 判 分 p分
限定类型: 非限 限定 期间限定 fes
年份: 25年 去年
活动id或者箱活缩写: event123 mnr1
以上参数可以混合使用，用空格分隔
""".strip()


@dataclass
class SkillEffectInfo:
    id: int
    type: str
    judge_type: str
    unit_count: int
    cond_type: str
    durations: List[int]
    value_type: str
    values: List[int]
    values2: List[int]
    activate_rank: int
    enhance_value: int
    
@dataclass
class SkillInfo:
    type: str
    detail: str


DETAIL_SKILL_KEYWORDS_IDS = [
    (
        ["大分"],
        [4],
    ),
    (
        ["p分", "P分"],
        [11],
    ),
    (
        ["判分"], 
        [13]
    ),
    (
        ["血分"],
        [12]
    ),
    (
        ["组分", "团分"],
        [15, 16, 17, 18, 19]
    ),
]


# ======================= 处理逻辑 ======================= #

# 获取sd图
async def get_character_sd_image(cuid: int) -> Image.Image:
    return await SekaiHandlerContext.from_region('jp').rip.img(f"character/character_sd_l/chr_sp_{cuid}.png")

# 解析查单张卡的参数
async def search_single_card(ctx: SekaiHandlerContext, args: str) -> dict:
    args = args.strip()
    for nickname, cid in get_character_nickname_data().nickname_ids:
        if nickname in args:
            seq = args.replace(nickname, "").strip()
            chara_cards = await ctx.md.cards.find_by("characterId", cid, mode="all")
            chara_cards.sort(key=lambda x: x['releaseAt'])
            if seq.removeprefix('-').isdigit(): 
                seq = int(seq)
                assert_and_reply(seq < 0, "卡牌序号只能为负数")
                assert_and_reply(-seq <= len(chara_cards), f"角色{nickname}只有{len(chara_cards)}张卡")
                card = chara_cards[seq]
                return card
    assert_and_reply(
        args.isdigit(), 
        f"无法解析的参数: {args}\n" + SEARCH_SINGLE_CARD_HELP,
    )
    card = await ctx.md.cards.find_by_id(int(args))
    assert_and_reply(card, f"找不到卡牌{ctx.region.upper()}-{args}")
    return card

# 解析查多张卡的参数 返回筛选后的cards列表和剩余参数
async def search_multi_cards(ctx: SekaiHandlerContext, args: str, cards: List[dict]=None, contain_leak=True) -> Tuple[List[dict], str]:
    args = args.lower().strip()

    if cards is None:
        cards = await ctx.md.cards.get()

    # 筛选leak
    only_leak = False
    if 'leak' in args:
        only_leak = True
        args = args.replace('leak', '', 1).strip()

    year, args = extract_year(args)

    # 活动id
    event = None
    if m := re.match(r"event(\d+)", args):
        event_id = int(m.group(1))
        event = await ctx.md.events.find_by_id(event_id)
        args = args.replace(f"event{event_id}", "").strip()
    # 箱活
    if not event:
        event, args = await extract_ban_event(ctx, args)
    # 获取活动卡牌
    event_card_ids = None
    if event is not None:
        event_card_ids = [card['id'] for card in await get_cards_of_event(ctx, event['id'])]

    # 详细的技能类型
    skill_ids = None
    for keywords, sids in DETAIL_SKILL_KEYWORDS_IDS:
        for keyword in keywords:
            if keyword in args:
                skill_ids = sids
                args = args.replace(keyword, "").strip()
                break

    attr, args = extract_card_attr(args)
    supply, args = extract_card_supply(args)
    skill, args = extract_card_skill(args)

    vs_unit, args = extract_vs_unit(args)
    oc_unit, args = extract_oc_unit(args)
    unit, args = extract_unit(args)
    target_unit, target_main_unit, target_support_unit = None, None, None
    if vs_unit is not None:
        target_unit = None
        target_main_unit = "piapro"
        target_support_unit = "none" if vs_unit == "piapro" else vs_unit
    elif oc_unit is not None:
        target_unit = None
        target_main_unit = oc_unit
        target_support_unit = "none"
    else:
        target_unit = unit
        target_main_unit = None
        target_support_unit = None

    rare, args = extract_card_rare(args)
    nickname, args = extract_nickname_from_args(args)
    chara_id = get_cid_by_nickname(nickname)

    # 筛选卡牌
    ret = []
    for card in cards:
        card_id = card["id"]
        card_sid = card["skillId"]
        card_cid = card["characterId"]
        card_unit = CID_UNIT_MAP.get(card_cid, None)
        card_support_unit = card['supportUnit']
        release_time = datetime.fromtimestamp(card["releaseAt"] / 1000)

        if contain_leak:
            if only_leak and release_time <= datetime.now(): 
                continue
        elif release_time > datetime.now():
                continue

        if event_card_ids is not None and card_id not in event_card_ids: continue
        if skill_ids is not None and card_sid not in skill_ids: continue
        if attr is not None and card["attr"] != attr: continue

        supply_type = await get_card_supply_type(ctx, card["id"])
        card["supply_show_name"] = CARD_SUPPLIES_SHOW_NAMES.get(supply_type, None)
        if supply is not None:
            search_supplies = []
            if supply == "festival_limited":
                search_supplies = ["bloom_festival_limited", "colorful_festival_limited"]
            elif supply == "all_limited":
                search_supplies = CARD_SUPPLIES_SHOW_NAMES.keys()
            elif supply == "not_limited":
                search_supplies = ["normal"]
            else:
                search_supplies = [supply]
            if supply_type not in search_supplies: continue

        skill_type = (await ctx.md.skills.find_by_id(card["skillId"]))["descriptionSpriteName"]
        card["skill_type"] = skill_type
        if skill is not None:
            if skill_type != skill: continue

        if target_unit is not None and target_unit not in (card_unit, card_support_unit): continue
        if target_main_unit is not None and card_unit != target_main_unit: continue
        if target_support_unit is not None and card_support_unit != target_support_unit: continue
        
        if year is not None and release_time.year != int(year): continue
        if vs_unit is not None and card_support_unit != vs_unit: continue
        if rare is not None and card["cardRarityType"] != rare: continue
        if chara_id is not None and card_cid != int(chara_id): continue

        ret.append(card)

    return ret, args.strip()

# 获取角色名称
async def get_character_name_by_id(ctx: SekaiHandlerContext, cid: int, space_first_last = False) -> str:
    character = await ctx.md.game_characters.find_by_id(cid)
    if space_first_last:
        return f"{character.get('firstName', '')} {character.get('givenName', '')}"
    return f"{character.get('firstName', '')}{character.get('givenName', '')}"

# 获取某个活动的卡牌
async def get_cards_of_event(ctx: SekaiHandlerContext, event_id: int) -> List[dict]:
    cids = [ec['cardId'] for ec in await ctx.md.event_cards.find_by("eventId", event_id, mode='all')]
    assert_and_reply(cids, f"活动ID={event_id}不存在")
    cards = await ctx.md.cards.collect_by_ids(cids)
    return cards

# 合成卡牌列表图片
async def compose_card_list_image(ctx: SekaiHandlerContext, cards: List[Dict], qid: int):
    if qid:
        profile, pmsg = await get_detailed_profile(ctx, qid, filter=get_detailed_profile_card_filter('userCards'), raise_exc=True)
        if profile:
            box_card_ids = set([uc['cardId'] for uc in profile.userCards])
            cards = [c for c in cards if c['id'] in box_card_ids]

    assert_and_reply(len(cards) > 0,    f"找不到符合条件的卡牌")
    assert_and_reply(len(cards) < 90,   f"卡牌数量过多({len(cards)})，请缩小查询范围，或者使用\"/卡牌一览\"")

    if len(cards) == 1:
        return await compose_card_detail_image(ctx, cards[0]['id'])

    async def get_thumb_nothrow(card):
        try: 
            if qid:
                pcard = find_by(profile.userCards, "cardId", card['id'])
                img = await get_card_full_thumbnail(ctx, card, pcard=pcard)
                return img, None
            normal = await get_card_full_thumbnail(ctx, card, False) if not only_has_after_training(card) else None
            after = await get_card_full_thumbnail(ctx, card, True) if has_after_training(card) else None
            return normal, after
        except: 
            logger.print_exc(f"获取卡牌{card['id']}完整缩略图失败")
            return UNKNOWN_IMG, UNKNOWN_IMG
    thumbs = await batch_gather_with_progress(
        *[get_thumb_nothrow(card) for card in cards],
        progress_name=f"{ctx.region.upper()} 查卡缩略图加载",
    )
    card_and_thumbs = [(card, thumb) for card, thumb in zip(cards, thumbs) if thumb is not None]
    card_and_thumbs.sort(key=lambda x: (x[0]['releaseAt'], x[0]['id']), reverse=True)

    bg_unit = await get_unit_by_card_id(ctx, cards[0]['id'])
    
    sz = 100
    def draw_card(card, img):
        with Frame().set_content_align('rt'):
            ImageBox(img, size=(sz, sz), shadow=True)
            supply_name = card['supply_show_name']
            if supply_name in ['期间限定', 'WL限定', '联动限定']:
                ImageBox(ctx.static_imgs.get(f"card/term_limited.png"), size=(int(sz*0.6), None))
            elif supply_name in ['CFes限定', 'BFes限定']:
                ImageBox(ctx.static_imgs.get(f"card/fes_limited.png"), size=(int(sz*0.6), None))

    with Canvas(bg=random_unit_bg(bg_unit)).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(16).set_content_align('lt').set_item_align('lt'):
            if qid:
                await get_detailed_profile_card(ctx, profile, pmsg)

            with Grid(col_count=3).set_bg(roundrect_bg()).set_padding(16):
                for i, (card, (normal, after)) in enumerate(card_and_thumbs):

                    bg = roundrect_bg()
                    if card["supply_show_name"]: 
                        bg.fill = (255, 250, 220, 200)
                    
                    with Frame().set_content_align('lb').set_bg(bg):
                        if datetime.fromtimestamp(card['releaseAt'] / 1000) > datetime.now():
                            TextBox("LEAK", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 0, 0))).set_offset((4, -4))

                        with Frame().set_content_align('rb'):
                            skill_type_img = ctx.static_imgs.get(f"skill_{card['skill_type']}.png")
                            ImageBox(skill_type_img, image_size_mode='fit').set_w(32).set_margin(8)

                            with VSplit().set_content_align('c').set_item_align('c').set_sep(5).set_padding(8):
                                GW = 300
                                with HSplit().set_content_align('c').set_w(GW).set_padding(8).set_sep(16):
                                    if normal is not None:
                                        draw_card(card, normal)
                                    if after is not None:
                                        draw_card(card, after)

                                name_text = card['prefix']
                                TextBox(name_text, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)).set_w(GW).set_content_align('c')

                                id_text = f"ID:{card['id']}"
                                if card["supply_show_name"]:
                                    id_text += f"【{card['supply_show_name']}】"
                                TextBox(id_text, TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)).set_w(GW).set_content_align('c')

    add_watermark(canvas)
    return await canvas.get_img()

# 获取卡面图片
async def get_card_image(ctx: SekaiHandlerContext, cid_or_card: int, after_training: bool, allow_error: bool = True) -> Image.Image:
    image_type = "after_training" if after_training else "normal"
    if isinstance(cid_or_card, int):
        card = await ctx.md.cards.find_by_id(cid_or_card)
        if not card: raise Exception(f"找不到ID为{cid_or_card}的卡牌") 
    else:
        card = cid_or_card
    return await ctx.rip.img(f"character/member/{card['assetbundleName']}_rip/card_{image_type}.png", timeout=30, allow_error=allow_error)

# 获取卡面立绘图片
async def get_card_cutout_image(ctx: SekaiHandlerContext, cid: int, after_training: bool, allow_error: bool = True) -> str:
    image_type = "after_training" if after_training else "normal"
    card = await ctx.md.cards.find_by_id(cid)
    if not card: raise Exception(f"找不到ID为{cid}的卡牌") 
    return await ctx.rip.img(f"character/member_cutout_trm/{card['assetbundleName']}/{image_type}.png", timeout=30, allow_error=allow_error)

# 合成卡牌剧情总结文本版
async def compose_card_story_summary_msg_list(
    ctx: SekaiHandlerContext, 
    card: dict,
    card_thumbs: list[Image.Image],
    card_thumbs_cq: str,
    eps: list[dict],
    summary: dict,
) -> List[str]:
    cid = card['id']
    title = card['prefix']
    cn_title = await translate_text(title, additional_info="该文本是偶像抽卡游戏中卡牌的标题", default=title)

    msg_lists = []

    msg_lists.append(f"""
【{cid}】{title} - {cn_title} 
{card_thumbs_cq}
!! 剧透警告 !!
!! 内容由AI生成，不保证完全准确 !!
""".strip() + "\n" * 16)
    
    for i, ep in enumerate(eps, 1):
        with Canvas(bg=SEKAI_BLUE_BG).set_padding(8) as canvas:
            row_count = int(math.sqrt(len(ep['cids'])))
            with Grid(row_count=row_count).set_sep(2, 2):
                for cid in ep['cids']:
                    if not cid: continue
                    icon = get_chara_icon_by_chara_id(cid, raise_exc=False)
                    if not icon: continue
                    ImageBox(icon, size=(32, 32), use_alphablend=True)

        text = summary.get(ep['title'], {}).get('summary', '')
        text = add_watermark_to_text(text, STORYSUMMARY_WATERMARK)
        msg_lists.append(f"""
【{ep['title']}】
{await get_image_cq(await canvas.get_img())}
{text}
""".strip())

        chara_talk_count_text = "【角色对话次数】\n"
        for name, count in ep['talk_count']:
            chara_talk_count_text += f"{name}: {count}\n"
        msg_lists.append(chara_talk_count_text.strip())

    additional_info_text = ""
    for i, ep in enumerate(eps, 1):
        additional_info_text += f"EP#{i} {summary.get(ep['title'], {}).get('additional_info', '')}\n"

    msg_lists.append(f"""
以上内容由Lunabot生成
{additional_info_text.strip()}
使用\"/卡牌剧情 卡牌id\"查询对应活动总结
使用\"/卡牌剧情 卡牌id refresh\"可刷新AI活动总结
""".strip())
        
    return msg_lists

# 合成卡牌剧情总结图片版
async def compose_card_story_summary_image(
    ctx: SekaiHandlerContext, 
    card: dict,
    card_thumbs: list[Image.Image],
    card_thumbs_cq: str,
    eps: list[dict],
    summary: dict,
) -> Image.Image:
    cid = card['id']
    title = card['prefix']
    cn_title = await translate_text(title, additional_info="该文本是偶像抽卡游戏中卡牌的标题", default=title)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=25, color=(0, 0, 0))
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0))

    w = 720
    line_sep = 5

    with Canvas(bg=SEKAI_BLUE_BG_DAY).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(8).set_item_align('lt').set_content_align('lt').set_item_bg(roundrect_bg()):
            with HSplit().set_padding(16).set_sep(16).set_item_align('l').set_content_align('l'):
                for thumb in card_thumbs:
                    ImageBox(thumb, size=(80, None), shadow=True)
                with VSplit().set_padding(0).set_sep(4).set_item_align('l').set_content_align('l'):
                    TextBox(f"#{cid} {title}", style1)
                    TextBox(f"{cn_title} - 卡牌故事剧情总结", style1)
                    TextBox("内容由AI生成，请勿转载到其他地方", style2)

            for i, ep in enumerate(eps, 1):
                with VSplit().set_padding(16).set_sep(16).set_item_align('lt').set_content_align('lt'):
                    TextBox(f"第{i}章 {summary.get(f'ep_{i}_title', ep['title'])}", style1)
                    with VSplit().set_padding(0).set_sep(8).set_item_align('lt').set_content_align('lt'):
                        with HSplit().set_sep(2):
                            for cid in ep['cids']:
                                if not cid: continue
                                icon = get_chara_icon_by_chara_id(cid, raise_exc=False)
                                if not icon: continue
                                ImageBox(icon, size=(32, 32), use_alphablend=True)
                        text = summary.get(ep['title'], {}).get('summary', '')
                        text = add_watermark_to_text(text, STORYSUMMARY_WATERMARK)
                        TextBox(text, style2, use_real_line_count=True, line_sep=line_sep).set_w(w)

                    TextBox(f"角色对话次数", style1.replace(size=22))
                    chara_talk_count_text = ""
                    for name, count in ep['talk_count']:
                        chara_talk_count_text += f"{name}: {count} | "
                    chara_talk_count_text = chara_talk_count_text.strip().rstrip('|')
                    TextBox(chara_talk_count_text, style2, use_real_line_count=True, line_sep=line_sep).set_w(w)
        
    add_watermark(canvas)
    return await canvas.get_img(cache_key=f"card_story_{ctx.region}_{cid}")

# 获取卡牌剧情总结，返回待发送的消息列表或图片
async def get_card_story_summary(ctx: SekaiHandlerContext, card: dict, refresh: bool, summary_model: List[str], save: bool) -> list[str] | Image.Image:
    cid = card['id']
    
    card_thumbs = []
    if not only_has_after_training(card):
        card_thumbs.append(await get_card_full_thumbnail(ctx, card, False))
    if has_after_training(card):
        card_thumbs.append(await get_card_full_thumbnail(ctx, card, True))
    card_thumbs_cq = await get_image_cq(resize_keep_ratio(concat_images(card_thumbs, 'h'), 80, mode='short'))

    summary_db = get_file_db(f"{SEKAI_DATA_DIR}/story_summary/card/{ctx.region}/{cid}.json", logger)
    summary = summary_db.get_copy("summary", {})
    if not summary or refresh:
        await ctx.asend_reply_msg(f"{card_thumbs_cq}正在生成卡面剧情总结...")

    ## 读取数据
    stories = await ctx.md.card_episodes.find_by("cardId", cid, mode='all')
    stories.sort(key=lambda x: x['seq'])
    eps = []
    for i, story in enumerate(stories, 1):
        asset_name = story['assetbundleName']
        scenario_id = story['scenarioId']
        ep_title = story['title']
        ep_data = await ctx.rip.json(f"character/member/{asset_name}_rip/{scenario_id}.asset", allow_error=False)
        cids = set([
            (await ctx.md.characters_2ds.find_by_id(item['Character2dId'])).get('characterId', None)
            for item in ep_data['AppearCharacters']
        ])

        snippets = []
        chara_talk_count = {}
        for snippet in ep_data['Snippets']:
            action = snippet['Action']
            ref_idx = snippet['ReferenceIndex']
            if action == 1:     # 对话
                talk = ep_data['TalkData'][ref_idx]
                names = talk['WindowDisplayName'].split('・')
                snippets.append((names, talk['Body']))
                for name in names:
                    chara_talk_count[name] = chara_talk_count.get(name, 0) + 1
            elif action == 6:   # 标题特效
                effect = ep_data['SpecialEffectData'][ref_idx]
                if effect['EffectType'] == 8:
                    snippets.append((None, effect['StringVal']))

        eps.append({
            'title': ep_title,
            'cids': cids,
            'snippets': snippets,
            'talk_count': sorted(chara_talk_count.items(), key=lambda x: x[1], reverse=True),
        })
    
    assert_and_reply(eps, f"ID={cid}没有剧情")

    ## 获取总结
    if not summary or refresh:
        for i, ep in enumerate(eps, 1):
            # 获取剧情文本
            raw_story = ""
            for names, text in ep['snippets']:
                if names:
                    raw_story += f"---\n{' & '.join(names)}:\n{text}\n"
                else:
                    raw_story += f"---\n({text})\n"
            raw_story += "\n"

            summary_prompt_template = Path(f"{SEKAI_CONFIG_DIR}/story_summary/card_story_summary_prompt.txt").read_text()
            summary_prompt = summary_prompt_template.format(raw_story=raw_story,)

            timeout = config.get('story_summary.card.timeout')
            retry_num = config.get('story_summary.card.retry')
            output_len_limit = config.get('story_summary.card.output_len_limit')
            
            @retry(stop=stop_after_attempt(retry_num), wait=wait_fixed(1), reraise=True)
            async def do_summary():
                try:
                    session = ChatSession()
                    session.append_user_content(summary_prompt, verbose=False)
                    
                    def process(resp: ChatSessionResponse):
                        resp_text = resp.result
                        if len(resp_text) > output_len_limit:
                            raise Exception(f"生成文本超过长度限制({len(resp_text)}>{output_len_limit})")
                        start_idx = resp_text.find("{")
                        end_idx = resp_text.rfind("}") + 1
                        data = loads_json(resp_text[start_idx:end_idx])

                        ep_summary = {}
                        ep_summary['summary'] = data['summary']

                        additional_info = f"生成模型: {resp.model.get_full_name()} | {resp.prompt_tokens}+{resp.completion_tokens} tokens"
                        if resp.quota > 0:
                            price_unit = resp.model.get_price_unit()
                            if resp.cost == 0.0:
                                additional_info += f" | 0/{resp.quota:.2f}{price_unit}"
                            elif resp.cost >= 0.0001:
                                additional_info += f" | {resp.cost:.4f}/{resp.quota:.2f}{price_unit}"
                            else:
                                additional_info += f" | <0.0001/{resp.quota:.2f}{price_unit}"
                        ep_summary['additional_info'] = additional_info
                        return ep_summary
                    
                    return await session.get_response(summary_model, process_func=process, timeout=timeout)

                except Exception as e:
                    logger.warning(f"生成剧情总结失败: {e}")
                    await ctx.asend_reply_msg(f"生成剧情总结失败, 重新生成中...")
                    raise Exception(f"生成剧情总结失败: {e}")

            summary[ep['title']] = await do_summary()
        if save:
            summary_db.set("summary", summary)

    ## 生成回复
    compose_method = compose_card_story_summary_image if config.get('story_summary.output_as_image') \
                    else compose_card_story_summary_msg_list
    return await compose_method(
        ctx, card, card_thumbs, card_thumbs_cq,
        eps, summary,
    )

# 合成卡牌一览图片
async def compose_box_image(ctx: SekaiHandlerContext, qid: int, cards: dict, show_id: bool, show_box: bool, use_after_training=True, sort_by='default'):
    pcards = []
    profile = None
    pmsg = None
    if qid:
        profile, pmsg = await get_detailed_profile(ctx, qid, filter=get_detailed_profile_card_filter('userCards'), raise_exc=show_box)
        if profile:
            pcards = profile.userCards
        
    # collect card imgs
    async def get_card_full_thumbnail_nothrow(card):
        if pcard := find_by(pcards, 'cardId', card['id']):
            if sort_by == 'sl':
                return await get_card_full_thumbnail(ctx, card, pcard=pcard, level_label='slv')
            else:
                return await get_card_full_thumbnail(ctx, card, pcard=pcard)
        else:
            after_training = has_after_training(card) and use_after_training
            if only_has_after_training(card):
                after_training = True
            return await get_card_full_thumbnail(ctx, card, after_training)
    card_imgs = await batch_gather_with_progress(
        *[get_card_full_thumbnail_nothrow(card) for card in cards],
        progress_name=f"{ctx.region.upper()} box卡面加载",
    )

    # collect chara cards
    chara_cards = {}
    for card, img in zip(cards, card_imgs):
        if not img: continue
        card_item = card.copy()
        chara_id = card_item['characterId']
        if chara_id not in chara_cards:
            chara_cards[chara_id] = []
        card_item['img'] = img
        
        pcard = find_by(pcards, 'cardId', card_item['id'])
        card_item['has'] = pcard is not None
        if pcard:
            card_item['masterRank'] = pcard.get('masterRank', 0)
            card_item['skillLevel'] = pcard.get('skillLevel', 1)
            card_item['createdAt'] = pcard.get('createdAt', 0)
        else:
            card_item['masterRank'] = -1
            card_item['skillLevel'] = -1
            card_item['createdAt'] = -1

        if show_box and not card_item['has']:
            continue
        chara_cards[chara_id].append(card_item)

    # sort by chara id and rarity
    chara_cards = list(chara_cards.items())
    chara_cards.sort(key=lambda x: x[0])
    
    for i in range(len(chara_cards)):
        if sort_by == 'mr':
            chara_cards[i][1].sort(key=lambda x: (x.get('masterRank', -1), x['cardRarityType'], x.get('createdAt', -1)), reverse=True)
        elif sort_by == 'sl':
            chara_cards[i][1].sort(key=lambda x: (x.get('skillLevel', -1), x['cardRarityType'], x.get('createdAt', -1)), reverse=True)
        elif sort_by == 'time':
            chara_cards[i][1].sort(key=lambda x: (x.get('createdAt', -1), x['cardRarityType']), reverse=True)
        else:
            chara_cards[i][1].sort(key=lambda x: (x['cardRarityType'], x['releaseAt'], x['id']))

    # 计算最佳高度限制
    max_card_num = max([len(cards) for _, cards in chara_cards]) if chara_cards else 0
    best_height, best_value = 10000, 1e9
    for i in range(1, max_card_num + 1):
        # 计算优化目标：max(h,w)越小越好，空白越少越好
        max_height = 0
        total_width = 0
        for _, cards in chara_cards:
            max_height = max(max_height, min(len(cards), i))
        total, space = 0, 0
        for _, cards in chara_cards:
            width = math.ceil(len(cards) / i)
            total_width += width
            total += max_height * width
            space += max_height * width - len(cards)
        # value = max(total_width, max_height) * total / (total - space)
        value = max(total_width, max_height * 0.5) if total_width > 9 else max(total_width * 0.5, max_height)
        if value < best_value:
            best_height, best_value = i, value

    # 计算总宽度并决定绘制卡牌的大小
    total_width = 0
    for _, cards in chara_cards:
        width = max(1, math.ceil(len(cards) / best_height))
        total_width += width
    area = total_width * (best_height + 4)

    start_area, start_sz, start_sep = 9 * 5, 100, 8
    end_area, end_sz, end_sep = 26 * 50, 48, 4
    interp = min(1.0, max(0.0, (area - start_area) / (end_area - start_area)))
    sep = int(start_sep + (end_sep - start_sep) * interp)
    sz = int(start_sz + (end_sz - start_sz) * interp)

    # 绘制单张卡
    def draw_card(card):
        with Frame().set_content_align('rt'):
            ImageBox(card['img'], size=(sz, sz))
            supply_name = card['supply_show_name']
            if supply_name in ['期间限定', 'WL限定', '联动限定']:
                ImageBox(ctx.static_imgs.get(f"card/term_limited.png"), size=(int(sz*0.6), None))
            elif supply_name in ['CFes限定', 'BFes限定']:
                ImageBox(ctx.static_imgs.get(f"card/fes_limited.png"), size=(int(sz*0.6), None))
            if not card['has'] and profile:
                Spacer(w=sz, h=sz).set_bg(RoundRectBg(fill=(0,0,0,120), radius=2))
        if show_id:
            TextBox(f"{card['id']}", TextStyle(font=DEFAULT_FONT, size=12, color=BLACK)).set_w(sz)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            if qid:
                await get_detailed_profile_card(ctx, profile, pmsg)
            with HSplit().set_bg(roundrect_bg()).set_content_align('lt').set_item_align('lt').set_padding(16).set_sep(4):
                for chara_id, cards in chara_cards:
                    with VSplit().set_content_align('t').set_item_align('t').set_sep(sep):
                        ImageBox(get_chara_icon_by_chara_id(chara_id), size=(sz, sz))
                        chara_color = color_code_to_rgb((await ctx.md.game_character_units.find_by_id(chara_id))['colorCode'])
                        col_num = max(1, len(range(0, len(cards), best_height)))
                        row_num = max(1, min(best_height, len(cards)))
                        Spacer(w=sz * col_num + sep * (col_num - 1), h=sep).set_bg(FillBg(chara_color))
                        with Grid(row_count=row_num, vertical=row_num > col_num).set_content_align('lt').set_item_align('lt').set_sep(sep, sep):
                            for card in cards:
                                draw_card(card)

    add_watermark(canvas)
    return await canvas.get_img()

from datetime import datetime, timezone, timedelta
import math

async def compose_createdAt_image(ctx: SekaiHandlerContext, qid: int, cards: dict, show_id: bool, use_after_training=True, group_by_year=False):
    profile, pmsg = await get_detailed_profile(ctx, qid, filter=get_detailed_profile_card_filter('userCards'), raise_exc=True)
    pcards = profile.userCards if profile else []
    
    owned_cards = []
    for card in cards:
        pcard = find_by(pcards, 'cardId', card['id'])
        if pcard and 'createdAt' in pcard:
            card['createdAt'] = pcard['createdAt']
            card['pcard'] = pcard
            owned_cards.append(card)
            
    assert_and_reply(owned_cards, "查询不到符合条件的卡牌")

    async def get_thumb(card):
        return await get_card_full_thumbnail(ctx, card, pcard=card['pcard'])
    card_imgs = await batch_gather(*[get_thumb(card) for card in owned_cards])
    
    for card, img in zip(owned_cards, card_imgs):
        card['img'] = img

    sz = 100
    item_width = 280
    tz_utc_8 = timezone(timedelta(hours=8))
    style_note = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(0, 0, 0))

    def draw_time_card(card):
        dt = datetime.fromtimestamp(card['createdAt'] / 1000, tz=tz_utc_8)
        date_str = dt.strftime('%Y-%m-%d')
        time_str = dt.strftime('%H:%M:%S')
        
        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(12).set_w(item_width):
            with Frame().set_size((sz, sz)).set_content_align('rt'):
                ImageBox(card['img'], size=(sz, sz))
                supply_name = card.get('supply_show_name')
                if supply_name in ['期间限定', 'WL限定', '联动限定']:
                    ImageBox(ctx.static_imgs.get(f"card/term_limited.png"), size=(int(sz*0.6), None))
                elif supply_name in ['CFes限定', 'BFes限定']:
                    ImageBox(ctx.static_imgs.get(f"card/fes_limited.png"), size=(int(sz*0.6), None))
                    
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                if show_id:
                    TextBox(f"ID: {card['id']}", TextStyle(font=DEFAULT_FONT, size=14, color=(100, 100, 100, 255)))
                TextBox(date_str, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK))
                TextBox(time_str, TextStyle(font=DEFAULT_FONT, size=16, color=(80, 80, 80, 255)))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_detailed_profile_card(ctx, profile, pmsg)
            
            with VSplit().set_content_align('l').set_item_align('l').set_sep(16).set_item_bg(roundrect_bg()):
                msg = "考虑到布局排版，当前模式仅支持展示单角色卡牌的获取时间记录\n"
                msg += "若要查看其他角色的获取时间，请在指令中指定对应的角色名（如: miku、一歌）\n"
                msg += "若要切换到按年份排布视图，请加上相关参数，如“/卡牌一览 miku 获取时间 按年份”"
                TextBox(msg, style_note, use_real_line_count=True).set_padding(12)
            
            if group_by_year:
                years_dict = {}
                for card in owned_cards:
                    y = datetime.fromtimestamp(card['createdAt'] / 1000, tz=tz_utc_8).year
                    years_dict.setdefault(y, []).append(card)
                
                sorted_years = sorted(years_dict.keys(), reverse=True)
                
                with HSplit().set_content_align('lt').set_item_align('lt').set_bg(roundrect_bg()).set_padding(16).set_sep(24):
                    for year in sorted_years:
                        cards_in_year = sorted(years_dict[year], key=lambda x: x['createdAt'], reverse=True)
                        
                        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(12):
                            with HSplit().set_content_align('c').set_item_align('c').set_w(item_width).set_bg(roundrect_bg()).set_padding(8):
                                TextBox(f"— {year} 年 —", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK))
                            
                            for card in cards_in_year:
                                draw_time_card(card)
            else:
                owned_cards.sort(key=lambda x: x['createdAt'], reverse=True)
                cols = 3 if len(owned_cards) > 20 else 2 if len(owned_cards) > 8 else 1
                row_count = math.ceil(len(owned_cards) / cols)
                
                with HSplit().set_content_align('lt').set_item_align('lt').set_bg(roundrect_bg()).set_padding(16).set_sep(24):
                    for c in range(cols):
                        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(12):
                            for r in range(row_count):
                                idx = c * row_count + r
                                if idx < len(owned_cards):
                                    draw_time_card(owned_cards[idx])
                                else:
                                    Spacer(w=item_width, h=sz)

    add_watermark(canvas)
    return await canvas.get_img()


def _consume_keyword_once(text: str, keywords: list[str]) -> tuple[bool, str, str | None]:
    """
    从 text 中按关键词长度降序匹配并删除一个关键词。
    返回: (是否匹配, 删除后的文本, 命中的关键词)
    """
    for kw in sorted(keywords, key=len, reverse=True):
        if kw in text:
            return True, text.replace(kw, "", 1).strip(), kw
    return False, text, None


def _parse_box_sort(text: str) -> tuple[str, str]:
    """
    解析 /卡牌一览 的排序参数，返回(sort_by, new_text)。
    """
    sort_keywords = {
        "mr": ["专精排序", "专家排序", "专精等级排序", "专家等级排序", "大师等级排序", "mr排序"],
        "sl": ["slv排序", "技能排序", "技能等级排序"],
        "time": ["入手排序", "入手时间排序", "获取时间排序", "获得时间排序", "obat排序"],
    }
    for sort_by, keywords in sort_keywords.items():
        hit, text, _ = _consume_keyword_once(text, keywords)
        if hit:
            return sort_by, text
    return "default", text


def _parse_box_mode(text: str) -> tuple[bool, bool, str]:
    """
    解析 /卡牌一览 的模式参数，返回:
    (is_created_at_mode, group_by_year, new_text)
    """
    created_at_keywords = ["获取时间一览", "入手时间一览", "时间一览", "获取时间", "入手时间", "obdt", "obat"]
    is_created_at_mode, text, _ = _consume_keyword_once(text, created_at_keywords)

    group_by_year = False
    if is_created_at_mode:
        group_by_year_keywords = ["按年份", "按年", "year"]
        group_by_year, text, _ = _consume_keyword_once(text, group_by_year_keywords)

    return is_created_at_mode, group_by_year, text

# 获取指定ID的技能信息
async def get_skill_info(ctx: SekaiHandlerContext, sid: int, card: dict):
    skill = await ctx.md.skills.find_by_id(sid)
    assert_and_reply(skill, f"技能ID={sid}不存在")
    skill_type = skill['descriptionSpriteName']
    skill_detail = skill['description']
    # 格式化技能描述
    try:
        effects: Dict[int, SkillEffectInfo] = {}
        for effect in skill['skillEffects']:
            durations, value_type, values, values2 = [], None, [], []
            for detail in effect['skillEffectDetails']:
                durations.append(detail['activateEffectDuration'])
                value_type = detail['activateEffectValueType']
                values.append(detail['activateEffectValue'])
                values2.append(detail.get('activateEffectValue2'))
            effects[effect['id']] = SkillEffectInfo(
                id=effect['id'],
                type=effect['skillEffectType'],
                durations=durations,
                value_type=value_type,
                values=values,
                values2=values2,
                enhance_value=effect.get('skillEnhance', {}).get('activateEffectValue'),
                activate_rank=effect.get('activateCharacterRank'),
                judge_type=effect.get('activateNotesJudgmentType'),
                unit_count=effect.get('activateUnitCount'),
                cond_type=effect.get('conditionType'),
            )
        
        chara_name = await get_character_name_by_id(ctx, card['characterId']) 

        def keep_one_if_all_same(lst: List) -> List:
            if len(lst) == 0: return lst
            if len(set(lst)) == 1:
                return [lst[0]]
            return lst

        def choose_values2_if_exists(e: SkillEffectInfo) -> List[int]:
            if e.values2 and e.values2[0] is not None:
                return e.values2
            return e.values

        def do_format(s: str) -> str:
            # 按顺序匹配所有的 {{...}}
            while True:
                m = re.search(r"{{(.*?)}}", s)
                if not m: break
                key = m.group(1)
                replace = None
                try:
                    ids, op = key.split(';')
                    ids = [int(i) for i in ids.split(',')]
                    # d, v, e, m, c 单个 effect_id 情况
                    if len(ids) == 1:
                        id = ids[0]
                        match op:
                            # d: 作用时间
                            case "d": 
                                durations = keep_one_if_all_same(effects[id].durations)
                                replace = "/".join([str(d) for d in durations])
                            # v: 加成值
                            case "v": 
                                values = keep_one_if_all_same(effects[id].values)
                                replace = "/".join([str(v) for v in values])
                            # e: 增强？
                            case "e": 
                                replace = str(effects[id].enhance_value)
                            # m: 满编的时候的编成增强？
                            case "m": 
                                values = keep_one_if_all_same(effects[id].values)
                                replace = "/".join([str(v + effects[id].enhance_value * 5) for v in values])
                            # c: 角色名
                            case "c": 
                                replace = chara_name
                            # abort
                            case _: 
                                raise Exception()
                    
                    # r, s, v, u, o 多个 effect_id 情况
                    else:
                        assert len(ids) == 2
                        x, y = ids
                        match op:
                            # v: 加成相加
                            case 'v':
                                values = [xv + yv for xv, yv in zip(effects[x].values, effects[y].values)]
                                values = keep_one_if_all_same(values)
                                replace = "/".join([str(v) for v in values])
                            # r: 当前的角色等级加成
                            case 'r': 
                                replace = "..."
                            # s: 当前的角色等级加成 + 正常加成值
                            case 's': 
                                replace = "..."
                            # o: 满编的时候的最大编成增强 + 正常加成值
                            case 'o': 
                                values = [xv + yv for xv, yv in zip(choose_values2_if_exists(effects[x]), choose_values2_if_exists(effects[y]))]
                                values = keep_one_if_all_same(values)
                                replace = "/".join([str(v) for v in values])
                            # u: 满编的时候的最大编成增强
                            case 'u':
                                values = [xv + yv for xv, yv in zip(effects[x].values, effects[y].values)]
                                values = keep_one_if_all_same(values)
                                replace = "/".join([str(v) for v in values])
                            # abort
                            case _: 
                                raise Exception()

                except Exception as e:
                    logger.print_exc(f"格式化技能描述 {key} 失败")
                    replace = " ? "
                s = s.replace("{{" + key + "}}", replace)
            return s
        
        skill_detail = do_format(skill_detail)
        
    except Exception as e:
        logger.print_exc(f"技能描述格式化失败")

    return SkillInfo(skill_type, skill_detail)

# 合成卡牌详情
async def compose_card_detail_image(ctx: SekaiHandlerContext, card_id: int):
    card = await ctx.md.cards.find_by_id(card_id)
    assert_and_reply(card, f"卡牌ID={card_id}不存在")

    # ----------------------- 数据收集 ----------------------- #
    # 基础信息
    title = card['prefix']
    chara_name = await get_character_name_by_id(ctx, card['characterId'])
    release_time = datetime.fromtimestamp(card['releaseAt'] / 1000)
    supply_type_name = CARD_SUPPLIES_SHOW_NAMES.get(await get_card_supply_type(ctx, card_id), "常驻")
    if card['cardRarityType'] == 'rarity_birthday':
        supply_type_name = "生日"

    # 缩略图
    thumbs = []
    if not only_has_after_training(card):
        thumbs.append(await get_card_full_thumbnail(ctx, card_id, False))
    if has_after_training(card):
        thumbs.append(await get_card_full_thumbnail(ctx, card_id, True))
    
    # 团头、角色头像
    chara_id = card['characterId']
    unit = await get_unit_by_card_id(ctx, card_id)
    chara_icon = get_chara_icon_by_chara_id(chara_id)
    unit_logo = get_unit_logo(unit)

    # 卡面
    card_images = []
    if not only_has_after_training(card):
        card_images.append(await get_card_image(ctx, card_id, False))
    if has_after_training(card):
        card_images.append(await get_card_image(ctx, card_id, True))

    # 综合力
    power1, power2, power3 = 0, 0, 0
    card_params = card['cardParameters']
    if isinstance(card_params, list):   # 日服综合力数据格式
        for item in card_params:
            ptype = item['cardParameterType']
            match ptype:
                case 'param1': power1 = max(power1, item['power'])
                case 'param2': power2 = max(power2, item['power'])
                case 'param3': power3 = max(power3, item['power'])
    else:   # 国服综合力数据格式
        power1 = max(card_params['param1'])
        power2 = max(card_params['param2'])
        power3 = max(card_params['param3'])
    # 特训综合力
    if 'specialTrainingPower1BonusFixed' in card: power1 += card['specialTrainingPower1BonusFixed']
    if 'specialTrainingPower2BonusFixed' in card: power2 += card['specialTrainingPower2BonusFixed']
    if 'specialTrainingPower3BonusFixed' in card: power3 += card['specialTrainingPower3BonusFixed']
    power_total = power1 + power2 + power3
        
    # 技能
    SKILL_TRANS_PROMPT = "该文本是偶像抽卡游戏中卡牌的技能描述，如果角色名存在请保留不变"
    skill_name = card['cardSkillName']
    skill_info: SkillInfo = await get_skill_info(ctx, card['skillId'], card)
    skill_type_icon = ctx.static_imgs.get(f"skill_{skill_info.type}.png")
    skill_detail = skill_info.detail
    skill_detail_cn: str = None
    if ctx.region in NEED_TRANSLATE_REGIONS:
        for r in TRANSLATED_REGIONS:
            try:
                skill_info = await get_skill_info(SekaiHandlerContext.from_region(r), card['skillId'], card)
                skill_detail_cn = skill_info.detail
                break
            except:
                pass
        if not skill_detail_cn:
            skill_detail_cn = await translate_text(skill_detail, additional_info=SKILL_TRANS_PROMPT, default=None)
    if 'specialTrainingSkillId' in card:
        sp_skill_name = card['specialTrainingSkillName']
        sp_skill_info = await get_skill_info(ctx, card['specialTrainingSkillId'], card)
        sp_skill_type_icon = ctx.static_imgs.get(f"skill_{sp_skill_info.type}.png")
        sp_skill_detail = sp_skill_info.detail
        sp_skill_detail_cn: str = None
        if ctx.region in NEED_TRANSLATE_REGIONS:
            if r in TRANSLATED_REGIONS:
                try:
                    sp_skill_info = await get_skill_info(SekaiHandlerContext.from_region(r), card['specialTrainingSkillId'], card)
                    sp_skill_detail_cn = sp_skill_info.detail
                except:
                    pass
            if not sp_skill_detail_cn:
                sp_skill_detail_cn = await translate_text(sp_skill_detail, additional_info=SKILL_TRANS_PROMPT, default=None)

    # 关联活动
    event_card = await ctx.md.event_cards.find_by("cardId", card_id)
    event_detail = None
    if event_card:
        event_detail = await get_event_detail(ctx, event_card['eventId'], require_assets=['banner'])

    # 关联卡池
    from .gacha import get_gacha_banner, get_gacha_by_card_id
    gacha = await get_gacha_by_card_id(ctx, card_id)
    if gacha:
        gacha_id = gacha.id
        gacha_name = gacha.name
        gacha_start = gacha.start_at
        gacha_end = gacha.end_at
        gacha_banner_img = await get_gacha_banner(ctx, gacha_id)

    # 衣装
    cos3d_ids = await ctx.md.card_costume3ds.find_by("cardId", card_id, mode='all')
    cos3ds: list[Costume3D] = await ctx.md.costume3ds.collect_by_ids([cos3d['costume3dId'] for cos3d in cos3d_ids])
    cos3d_imgs = []
    for cos3d in cos3ds:
        asset_name = cos3d.assetbundleName
        cos3d_imgs.append(ctx.rip.img(f"thumbnail/costume_rip/{asset_name}.png"))
    cos3d_imgs = await batch_gather(*cos3d_imgs)

    # ----------------------- 绘图 ----------------------- #
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70))
    tip_style = TextStyle(font=DEFAULT_FONT, size=18, color=(0, 0, 0))

    with Canvas(bg=random_unit_bg(unit)).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align('lt').set_item_align('lt'):
            # 左侧: 卡面+关联活动+关联卡池+提示
            with VSplit().set_padding(0).set_sep(16).set_content_align('lt').set_item_align('lt').set_item_bg(roundrect_bg()):
                # 卡面
                with VSplit().set_padding(16).set_sep(8).set_content_align('lt').set_item_align('lt'):
                    for img in card_images:
                        ImageBox(img, size=(500, None), shadow=True)

                # 关联活动
                if event_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                        with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                            TextBox("当期活动", label_style)
                            TextBox(f"【{event_detail.eid}】{event_detail.name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align('lt').set_item_align('lt'):
                            ImageBox(event_detail.event_banner, size=(250, None))
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(6):
                                TextBox(f"开始时间: {event_detail.start_time.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {event_detail.end_time.strftime('%Y-%m-%d %H:%M')}",   small_style)
                                Spacer(h=4)
                                with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                    if event_detail.bonus_attr:
                                        ImageBox(get_attr_icon(event_detail.bonus_attr), size=(32, None))
                                    if event_detail.unit:
                                        ImageBox(get_unit_icon(event_detail.unit), size=(32, None))
                                    if event_detail.banner_cid:
                                        ImageBox(get_chara_icon_by_chara_id(event_detail.banner_cid), size=(32, None))

                # 关联卡池
                if gacha:
                    with VSplit().set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                        with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                            TextBox("当期卡池", label_style)
                            TextBox(f"【{gacha_id}】{gacha_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align('lt').set_item_align('lt'):
                            ImageBox(gacha_banner_img, size=(250, None))
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(6):
                                TextBox(f"开始时间: {gacha_start.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {gacha_end.strftime('%Y-%m-%d %H:%M')}",   small_style)

            
            # 右侧: 标题+限定类型+综合力+技能+发布时间+缩略图+衣装
            w = 600
            with VSplit().set_padding(0).set_sep(16).set_content_align('lt').set_item_align('lt').set_item_bg(roundrect_bg()):
                # 标题
                with HSplit().set_padding(16).set_sep(32).set_content_align('c').set_item_align('c').set_w(w):
                    ImageBox(unit_logo, size=(None, 64))
                    with VSplit().set_content_align('c').set_item_align('c').set_sep(12):
                        TextBox(title, title_style).set_w(w - 260).set_content_align('c')
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(8):
                            ImageBox(chara_icon, size=(None, 32))
                            TextBox(chara_name, title_style)

                with VSplit().set_padding(16).set_sep(8).set_item_bg(roundrect_bg()).set_content_align('l').set_item_align('l'):
                    # 卡牌ID 限定类型
                    with HSplit().set_padding(16).set_sep(8).set_content_align('l').set_item_align('l'):
                        TextBox("ID", label_style)
                        TextBox(f"{card_id} ({ctx.region.upper()})", text_style)
                        Spacer(w=32)
                        TextBox("限定类型", label_style)
                        TextBox(supply_type_name, text_style)

                    # 综合力
                    with HSplit().set_padding(16).set_sep(8).set_content_align('lb').set_item_align('lb'):
                        TextBox("综合力", label_style)
                        TextBox(f"{power_total} ({power1}/{power2}/{power3}) (满级0破无剧情)", text_style)

                    # 技能
                    with VSplit().set_padding(16).set_sep(8).set_content_align('l').set_item_align('l'):
                        with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                            TextBox("技能", label_style)
                            ImageBox(skill_type_icon, size=(32, 32))
                            TextBox(skill_name, text_style).set_w(w - 24 * 2 - 32 - 16)
                        TextBox(skill_detail, text_style, use_real_line_count=True).set_w(w)
                        if skill_detail_cn:
                            TextBox(skill_detail_cn.removesuffix("。"), text_style, use_real_line_count=True).set_w(w)

                    # 特训技能
                    if 'specialTrainingSkillId' in card:
                        with VSplit().set_padding(16).set_sep(8).set_content_align('l').set_item_align('l'):
                            with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                TextBox("特训后技能", label_style)
                                ImageBox(sp_skill_type_icon, size=(32, 32))
                                TextBox(sp_skill_name, text_style).set_w(w - 24 * 5 - 32 - 16)
                            TextBox(sp_skill_detail, text_style, use_real_line_count=True).set_w(w)
                            if sp_skill_detail_cn:
                                TextBox(sp_skill_detail_cn.removesuffix("。"), text_style, use_real_line_count=True).set_w(w)

                    # 发布时间
                    with HSplit().set_padding(16).set_sep(8).set_content_align('lb').set_item_align('lb'):
                        TextBox("发布时间", label_style)
                        TextBox(release_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    # 缩略图
                    with HSplit().set_padding(16).set_sep(16).set_content_align('l').set_item_align('l'):
                        TextBox("缩略图", label_style)
                        for img in thumbs:
                            ImageBox(img, size=(100, None), shadow=True)

                    # 衣装
                    if len(cos3d_imgs) > 0:
                        with HSplit().set_padding(16).set_sep(16).set_content_align('l').set_item_align('l'):
                            TextBox("衣装", label_style)
                            with Grid(col_count=5).set_sep(8, 8):
                                for img in cos3d_imgs:
                                    ImageBox(img, size=(80, None), shadow=True)

                    # 提示
                    with VSplit().set_padding(12).set_sep(6).set_content_align('l').set_item_align('l'):
                        TextBox(f"发送\"/查卡面 {card_id}\"获取卡面原图, 发送\"/卡面剧情 {card_id}\"获取AI剧情总结", tip_style)

    add_watermark(canvas)
    return await canvas.get_img()


# ======================= 指令处理 ======================= #

# 角色别名查询
pjsk_chara_alias = SekaiCmdHandler([
    "/pjsk chara alias",
    "/角色别名", '/查角色别名',
])
pjsk_chara_alias.check_cdrate(cd).check_wblist(gbl)
@pjsk_chara_alias.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    assert_and_reply(args, "请输入要查询的角色名或别名")
    cid = get_cid_by_nickname(args)
    assert_and_reply(cid, f"没有找到角色名或别名为\"{args}\"的角色")
    nicknames = get_nicknames_by_chara_id(cid)
    await ctx.asend_reply_msg(f"角色ID.{cid}的别名:\n{', '.join(nicknames)}")
    

# 卡牌查询
pjsk_card = SekaiCmdHandler([
    "/card", "/pjsk card", "/pjsk member", 
    "/查卡", "/查卡牌", "/卡牌列表", "/cards", "/pjsk cards",
])
pjsk_card.check_cdrate(cd).check_wblist(gbl)
@pjsk_card.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    card, chara_id = None, None
    cards = await ctx.md.cards.get()
    
    ## 尝试解析：单独查某张卡
    try: 
        card = await search_single_card(ctx, args)
    except Exception as e:
        if '找不到卡牌' in str(e):
            raise e
        card = None
    if card:
        logger.info(f"查询卡牌: id={card['id']}")
        return await ctx.asend_reply_msg(await get_image_cq(
            await compose_card_detail_image(ctx, card['id']),
            low_quality=True,
        ))
        
    ## 尝试解析：查多张卡
    res, args = await search_multi_cards(ctx, args, cards, contain_leak=True)
    box = False
    if 'box' in args:
        args = args.replace('box', '').strip()
        box = True
    assert_and_reply(not args, f"无法解析的参数:\"{args}\"")

    logger.info(f"搜索到{len(res)}个卡牌")

    qid = ctx.user_id if box else None
    return await ctx.asend_reply_msg(await get_image_cq(
        await compose_card_list_image(ctx, res, qid),
        low_quality=True,
    ))
        
        
# 卡面查询
pjsk_card_img = SekaiCmdHandler([
    "/pjsk card img",
    "/查卡面", "/卡面", 
])
pjsk_card_img.check_cdrate(cd).check_wblist(gbl)
@pjsk_card_img.handle()
async def _(ctx: SekaiHandlerContext):
    card = await search_single_card(ctx, ctx.get_args().strip())
    msg = ""
    if not only_has_after_training(card):
        msg += await get_image_cq(await get_card_image(ctx, card['id'], False, False))
    if has_after_training(card):
        msg += await get_image_cq(await get_card_image(ctx, card['id'], True, False))
    return await ctx.asend_reply_msg(msg)


# 卡牌剧情查询
pjsk_card_story = SekaiCmdHandler([
    "/pjsk card story",
    "/卡牌剧情", "/卡面剧情", "/卡剧情", '/卡牌故事', '/卡面故事', '/卡故事',
], regions=['jp'])
pjsk_card_story.check_cdrate(cd).check_wblist(gbl)
@pjsk_card_story.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    refresh = False
    save = True

    if 'refresh' in args:
        args = args.replace('refresh', '').strip()
        refresh = True

    model = get_model_preset("sekai.story_summary.card")
    if 'model:' in args:
        assert_and_reply(check_superuser(ctx.event), "仅超级用户可指定模型")
        model = args.split('model:')[1].strip()
        args = args.split('model:')[0].strip()
        refresh = True
        save = False

    card = await search_single_card(ctx, args)
    await ctx.block_region(str(card['id']))

    resp = await get_card_story_summary(ctx, card, refresh, model, save)
    if isinstance(resp, Image.Image):
        return await ctx.asend_reply_msg(await get_image_cq(resp, low_quality=True))
    else:
        return await ctx.asend_fold_msg(resp)


# 查询卡牌一览
pjsk_box = SekaiCmdHandler([
    "/pjsk box",
    "/卡牌一览", "/卡面一览", "/卡一览",
])
pjsk_box.check_cdrate(cd).check_wblist(gbl)
@pjsk_box.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()

    sort_by, args = _parse_box_sort(args)

    is_created_at_mode, group_by_year, args = _parse_box_mode(args)

    cards, args = await search_multi_cards(ctx, args, contain_leak=False)

    show_id = False
    if 'id' in args:
        show_id = True
        args = args.replace('id', '').strip()

    show_box = False
    if 'box' in args:
        show_box = True
        args = args.replace('box', '').strip()

    use_after_training = True
    if 'before' in args:
        use_after_training = False
        args = args.replace('before', '').strip()

    assert_and_reply(not args, f"无法解析的参数:\"{args}\"")
    assert_and_reply(cards, "没有找到符合条件的卡牌")

    if is_created_at_mode:
        unique_charas = set(card['characterId'] for card in cards)
        assert_and_reply(
            len(unique_charas) <= 1, 
            "考虑到布局，该功能仅支持查询单角色卡牌获取时间\n请加上角色参数（如: miku、一歌等）进行详细查询\n如果你确定已经加上了单个角色名称，请检查各参数之间是否用空格隔开"
        )
        img = await compose_createdAt_image(ctx, ctx.user_id, cards, show_id, use_after_training, group_by_year=group_by_year)
    else:
        img = await compose_box_image(ctx, ctx.user_id, cards, show_id, show_box, use_after_training, sort_by=sort_by)

    await ctx.asend_reply_msg(await get_image_cq(img, low_quality=True))


