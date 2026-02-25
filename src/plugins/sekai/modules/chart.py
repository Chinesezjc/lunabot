from ...utils import *
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from .music import *
from .deck import get_musicmetas_json
from src.pjsekai import scores as pjsekai_scores

# ======================= 处理逻辑 ======================= #

CHART_CACHE_PATH = SEKAI_ASSET_DIR + "/chart/{region}/{mid}_{diff}.png"
CHART_ASSET_DIR = f"{SEKAI_ASSET_DIR}/chart_asset"
CHART_CSS_DIR = "src/pjsekai/scores/css"

NOTE_SIZES = {
    'easy': 2.0,
    'normal': 1.5,
    'hard': 1.25,
    'expert': 1.0,
    'master': 0.875,
    'append': 0.875,
}

# 生成谱面图片
async def generate_music_chart(
    ctx: SekaiHandlerContext, 
    music_id: int,
    difficulty: str,
    need_reply: bool = True,
    random_clip_length_rate: float = None,
    style_sheet: str = 'black',
    use_cache: bool = True,
    refresh: bool = False,
    skill: bool = False
) -> Image.Image:
    if use_cache:
        await ctx.block_region(f"chart_{music_id}_{difficulty}_{skill}")
    # 带技能与不带技能的缓存分开
    cache_path_suffix = difficulty if not skill else f"{difficulty}_skill"
    cache_path = CHART_CACHE_PATH.format(region=ctx.region, mid=music_id, diff=cache_path_suffix)
    create_parent_folder(cache_path)
    if use_cache and not refresh and os.path.exists(cache_path):
        return open_image(cache_path)

    # 获取信息
    music = await ctx.md.musics.find_by_id(music_id)
    assert_and_reply(music, f'曲目 {music_id} 不存在')

    music_title = music['title']
    cn_title = await get_music_trans_title(music_id, 'cn', None)
    if cn_title:
        music_title += f'({cn_title})'

    if music['composer'] == music['arranger']:
        artist = music['composer']
    elif music['composer'] in music['arranger'] or music['composer'] == '-':
        artist = music['arranger']
    elif music['arranger'] in music['composer'] or music['arranger'] == '-':
        artist = music['composer']
    else:
        artist = '%s / %s' % (music['composer'], music['arranger'])
    playlevel = '?'
    if diff_info := await get_music_diff_info(ctx, music_id):
        playlevel = diff_info.level.get(difficulty, '?')

    logger.info(f'生成谱面图片 mid={music_id} {difficulty}')
    if need_reply:
        # desc = "谱面图片" if not skill else "技能预览图片"
        desc = "谱面图片"
        await ctx.asend_reply_msg(f'正在生成【{ctx.region.upper()}-{music_id}】{music_title} - {difficulty.upper()} {playlevel} 的{desc}...')

    asset_name = music['assetbundleName']
    jacket = await ctx.rip.img(f"music/jacket/{asset_name}_rip/{asset_name}.png")
    jacket = get_image_b64(jacket)

    note_host = os.path.abspath(f'{CHART_ASSET_DIR}/notes')

    sus_path = await ctx.rip.get_asset_cache_path(f"music/music_score/{music_id:04d}_01_rip/{difficulty}", allow_error=False)
    
    # music_meta 显示技能时同时可以显示技能的加成效果和fever的效果
    music_meta = None
    if skill:
        musicmetas_json = get_musicmetas_json(ctx.region)
        music_metas = find_by(await musicmetas_json.get(), "music_id", music_id, mode='all')
        if music_metas:
            music_meta = find_by(music_metas, "difficulty", difficulty)
        # assert_and_reply(music_meta, f'歌曲{music_id}难度{difficulty}暂无技能信息')

    with TempFilePath('svg') as svg_path:
        def get_svg(style_sheet):
            score = pjsekai_scores.Score.open(sus_path, encoding='UTF-8')

            if random_clip_length_rate is not None:
                clip_len = int(len(score.notes) * random_clip_length_rate)
                clip_start = random.randint(0, len(score.notes) - clip_len)
                start_note_bar = score.notes[clip_start].bar
                score.notes = score.notes[clip_start: clip_start + clip_len]
                for note in score.notes:
                    note.bar -= start_note_bar
                score.events = []
                score._init_notes()
                score._init_events()    

            score.meta = pjsekai_scores.score.Meta(
                title=f"[{ctx.region.upper()}-{music_id}] {music_title}",
                artist=artist,
                difficulty=difficulty,
                playlevel=str(playlevel),
                jacket=jacket,
                songid=str(music_id),
            )
            style_sheet = Path(f'{CHART_CSS_DIR}/{style_sheet}.css').read_text()
            drawing = pjsekai_scores.Drawing(
                score=score,
                style_sheet=style_sheet,
                note_host=f'file://{note_host}',
                skill=skill,
                music_meta=music_meta,
                target_segment_seconds=config.get('chart.target_segment_seconds'),
            )
            drawing.svg().saveas(svg_path)
        await run_in_pool(get_svg, style_sheet)

        # 渲染svg
        img = await download_and_convert_svg(f"file://{os.path.abspath(svg_path)}")
        if random_clip_length_rate:
            img = img.crop((0, 0, img.size[0], img.size[1] - 260))

        MAX_RES = config.get('chart.max_resolution')
        if img.size[0] * img.size[1] > MAX_RES[0] * MAX_RES[1]:
            img = resize_keep_ratio(img, max_size=MAX_RES[0] * MAX_RES[1], mode='wxh')
        logger.info(f'生成 mid={music_id} {difficulty} 谱面图片完成')

        if use_cache:
            img.save(cache_path)
        return img


# ======================= 指令处理 ======================= #

# 谱面查询
pjsk_chart = SekaiCmdHandler([
    "/pjsk chart",
    "/谱面查询", "/铺面查询", "/谱面预览", "/铺面预览", "/谱面", "/铺面", "/查谱面", "/查铺面", "/查谱",
    "/技能预览", 
])
pjsk_chart.check_cdrate(cd).check_wblist(gbl)
@pjsk_chart.handle()
async def _(ctx: SekaiHandlerContext):
    query = ctx.get_args().strip()
    assert_and_reply(query, MUSIC_SEARCH_HELP)
    
    refresh = False
    if 'refresh' in query:
        refresh = True
        query = query.replace('refresh', '').strip()
    
    skill = True
    diff, query = extract_diff(query)
    ret = await search_music(ctx, query, MusicSearchOptions(diff=diff))

    mid, title = ret.music['id'], ret.music['title']

    msg = ""
    try:
        msg = await get_image_cq(
            await generate_music_chart(
                ctx, mid, diff, 
                refresh=refresh, 
                use_cache=True,
                style_sheet=config.get('chart.style_sheet_name'),
                skill=skill
            ),
            low_quality=True,
        )
    except Exception as e:
        logger.print_exc(f"获取 mid={mid} {diff} 的谱面失败")
        return await ctx.asend_reply_msg(f"获取指定曲目\"{title}\"难度{diff}的谱面失败: {e}")
        
    msg += ret.candidate_msg
    return await ctx.asend_reply_msg(msg.strip())

