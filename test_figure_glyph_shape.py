from io import BytesIO
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

import app_server
import build_problem_board_edb as board
import image_reconstruction_backend as backend
from edb_builder import ImageRecordSpec, build_edb, build_image_record
from inspect_edb import extract_images, parse_edb


def shaded_figure(*, light_letter=False, fill=190):
    image=Image.new('RGB',(1024,600),'white');draw=ImageDraw.Draw(image)
    shade=35 if light_letter else fill;ink=255 if light_letter else 0
    draw.rectangle((110,90,205,172),fill=(shade,)*3)
    draw.polygon([(140,111),(156,111),(166,117),(166,126),(161,130),(168,135),(168,146),(157,151),(140,151)],fill=(ink,)*3)
    holes=[(148,117,156,124),(148,136,158,145)]
    for box in holes:draw.rectangle(box,fill=(shade,)*3)
    return image,holes,(143,131)


def encode_restored_shared(tmp_path,image,*,crop_format='v1',media='off'):
    path=tmp_path/'neutral.png';image.save(path)
    payload={'id':'neutral-shared','title':'지문 14~17','subject':'unknown','sourceFileName':'a19f725.pdf',
             'sourcePageId':'page-005','imagePath':path.as_uri(),'boardRenderPath':path.as_uri(),
             'bbox':{'left':0,'top':0,'width':image.width,'height':image.height},'processingStep':'s2',
             'preserveMediaMode':media,'passageRole':'passage_fragment','passageRange':{'start':14,'end':17},
             'sourceSegments':[{'sourcePageId':'page-005'},{'sourcePageId':'page-006'}]}
    entry=app_server._problems_to_entries([payload])[0]
    assert entry.subject.value=='unknown'
    assert board._explicit_shared_passage_prefers_text(entry.subject,entry.passage_role,entry.passage_range)
    placement=SimpleNamespace(metadata={'crop_path':str(path),'board_render_path':str(path)},start_y_pages=0,snapped_next_start_y_pages=3)
    encoded=board._build_image_only_record_image(placement,entry,dark_board=True,board_theme='charcoal',crop_format=crop_format,target_image_width_px=0,continuous_flow=False)
    record=build_image_record(ImageRecordSpec(1,encoded.image_bytes,encoded.secondary_bytes,0,0,0.5,0.5))
    destination=tmp_path/'actual.edb';destination.write_bytes(build_edb([record],header_flag=0,page_count_hint=50))
    parsed=parse_edb(destination);files=extract_images(parsed,destination,tmp_path/'decoded')
    with Image.open(files[0]) as decoded:actual=decoded.convert('RGBA')
    assert actual.size==image.size
    return actual


@pytest.mark.parametrize('crop_format',['v1','v2'])
@pytest.mark.parametrize('media',['off','auto'])
@pytest.mark.parametrize('fill',[150,190,220])
def test_native_edb_dark_glyph_counters_keep_contrast_on_gray_fill(tmp_path,crop_format,media,fill):
    source,holes,stroke=shaded_figure(fill=fill)
    actual=encode_restored_shared(tmp_path,source,crop_format=crop_format,media=media)
    alpha=np.asarray(actual)[...,3]
    stroke_alpha=int(alpha[stroke[1],stroke[0]])
    assert stroke_alpha>=240
    for left,top,right,bottom in holes:
        counter=alpha[top+2:bottom-1,left+2:right-1]
        assert counter.size>0
        assert int(np.median(counter))<=stroke_alpha-60
    # Standalone original paper remains transparent.
    assert actual.getpixel((20,20))[3]==0


@pytest.mark.parametrize('crop_format',['v1','v2'])
@pytest.mark.parametrize('media',['off','auto'])
def test_native_edb_white_glyph_on_dark_fill_remains_readable(tmp_path,crop_format,media):
    source,holes,stroke=shaded_figure(light_letter=True)
    actual=encode_restored_shared(tmp_path,source,crop_format=crop_format,media=media)
    alpha=np.asarray(actual)[...,3];stroke_alpha=int(alpha[stroke[1],stroke[0]])
    # On a dark source patch, the source white letter becomes negative ink.
    # Its two dark counters must remain distinct from that letter.
    assert stroke_alpha<40
    for left,top,right,bottom in holes:
        assert int(np.median(alpha[top+2:bottom-1,left+2:right-1]))>=stroke_alpha+160


def test_standalone_gray_letter_and_thin_rules_are_not_faded_as_fill():
    source=Image.new('RGB',(300,180),'white');draw=ImageDraw.Draw(source)
    draw.rectangle((30,30,40,90),fill=(180,180,180));draw.line((60,50,200,50),fill=(180,180,180),width=2)
    cleaned,_=backend.clean_problem_image_transparency(source,remove_corner_page_artifacts=False)
    assert cleaned.getpixel((35,60))[3]==255
    assert cleaned.getpixel((120,50))[3]==255


def test_gray_counter_islands_outside_an_accepted_fill_are_unchanged():
    source=Image.new('RGB',(200,160),'white');draw=ImageDraw.Draw(source)
    draw.rectangle((40,40,90,110),fill='black');draw.rectangle((52,52,70,70),fill=(190,190,190))
    cleaned,_=backend.clean_problem_image_transparency(source,remove_corner_page_artifacts=False)
    assert cleaned.getpixel((60,60))[3]==255


@pytest.mark.parametrize('crop_format',['v1','v2'])
@pytest.mark.parametrize('media',['off','auto'])
@pytest.mark.parametrize('outlined',[False,True])
def test_gray_glyph_in_white_bubble_inside_gray_fill_is_not_a_counter(tmp_path,crop_format,media,outlined):
    source=Image.new('RGB',(1024,600),'white');draw=ImageDraw.Draw(source)
    draw.rectangle((100,80,400,300),fill=(190,190,190))
    draw.rectangle((170,130,330,250),fill='white',outline='black' if outlined else None,width=5)
    draw.rectangle((220,160,240,220),fill=(190,190,190))
    actual=encode_restored_shared(tmp_path,source,crop_format=crop_format,media=media)
    assert actual.getpixel((230,190))[3]>=240
    assert actual.getpixel((200,190))[3]==0


def test_backend_source_change_invalidates_old_filled_counter_cache(tmp_path,monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    source,holes,_=shaded_figure();path=tmp_path/'source.png';source.save(path)
    real_read=Path.read_bytes;revision={'value':b'legacy-backend'}
    def read_code(candidate):
        if candidate.name=='image_reconstruction_backend.py':return revision['value']
        return real_read(candidate)
    chalk=board._resolve_chalk_color('charcoal')
    repaired=board._extract_problem_cutout(board._enhance_problem_crop(source,text_priority=True),chalk_color=chalk,text_priority=True)
    legacy=repaired.copy();alpha=legacy.getchannel('A');draw=ImageDraw.Draw(alpha)
    for box in holes:draw.rectangle(box,fill=255)
    legacy.putalpha(alpha)
    monkeypatch.setattr(board,'_CHALK_CUTOUT_CACHE_SALT',None)
    with patch.object(Path,'read_bytes',read_code):
        with patch.object(board,'_extract_problem_cutout',return_value=legacy):
            old=board._cached_chalk_cutout(path,source,chalk_color=chalk,text_priority=True)
        revision['value']=b'repaired-backend';board._CHALK_CUTOUT_CACHE_SALT=None
        with patch.object(board,'_extract_problem_cutout',return_value=repaired) as rebuild:
            new=board._cached_chalk_cutout(path,source,chalk_color=chalk,text_priority=True)
        assert rebuild.call_count==1
    assert old.tobytes()==legacy.tobytes()
    assert new.tobytes()==repaired.tobytes()
    assert len(list((tmp_path/board.CHALK_CUTOUT_CACHE_DIRNAME).glob('*.png')))==2


def test_initial_render_and_publish_reuse_repaired_figure_counters(tmp_path):
    from unittest.mock import patch
    source,holes,stroke=shaded_figure();path=tmp_path/'source.png';source.save(path)
    task=board._ProblemAssetTask(source_image=source,bounds=board.Box(0,0,*source.size),crop_path=path,
        board_render_path=tmp_path/'render.png',chalk_color=board._resolve_chalk_color('charcoal'),text_priority=True)
    rendered=board._build_problem_board_asset(source,task)
    with patch.object(board,'_extract_problem_cutout',side_effect=AssertionError('publish rebuilt cached cutout')):
        published=board._cached_chalk_cutout(path,source,chalk_color=task.chalk_color,text_priority=True)
    assert rendered.tobytes()==published.tobytes()
    for left,top,right,bottom in holes:
        assert int(np.median(np.asarray(published)[top+2:bottom-1,left+2:right-1,3]))<128


def _source_projected_shape(source, actual, bounds):
    """Frozen 0.8 source-projected silhouette gate, including both CC directions."""
    import cv2
    original = np.asarray(source.crop(bounds).convert('RGB'))
    alpha = np.asarray(actual.crop(bounds).convert('RGBA'))[..., 3]
    strength = 255 - original.min(axis=2)
    _, source_mask = cv2.threshold(strength, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, actual_mask = cv2.threshold(alpha, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    assert source_mask.std() and actual_mask.std()
    x, y, width, height = cv2.boundingRect(source_mask)
    left, top = max(0, x - 1), max(0, y - 1)
    right, bottom = min(source_mask.shape[1], x + width + 1), min(source_mask.shape[0], y + height + 1)
    source_mask = source_mask[top:bottom, left:right]
    actual_mask = actual_mask[top:bottom, left:right]
    score = float(cv2.matchTemplate(cv2.GaussianBlur(actual_mask, (3, 3), 0),
                                   cv2.GaussianBlur(source_mask, (3, 3), 0),
                                   cv2.TM_CCOEFF_NORMED)[0, 0])
    assert score >= .8
    for mask, counterpart in ((source_mask, actual_mask), (actual_mask, source_mask)):
        support = cv2.dilate((counterpart > 0).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for component in range(1, count):
            assert support[labels == component].mean() >= .9
    return score


@pytest.mark.parametrize('crop_format', ['v1', 'v2'])
@pytest.mark.parametrize('media', ['off', 'auto'])
@pytest.mark.parametrize('ink', [(0, 0, 0), (85, 85, 85)])
@pytest.mark.parametrize('background', [(180, 190, 216), (190, 190, 190)])
def test_native_shaded_table_antialias_keeps_letter_shape_at_narrow_baseline_gap(
        tmp_path, crop_format, media, ink, background):
    source = Image.new('RGB', (1024, 600), 'white')
    draw = ImageDraw.Draw(source)
    draw.rectangle((100, 80, 720, 142), fill=background)
    font = ImageFont.load_default(size=19)
    # Only a narrow strip of fill remains below the serif/rounded glyphs.
    # Uniform seed windows alone cannot reach all of that strip.
    draw.text((140, 118), 'Opaque B8 glyphs 1.2 ?', font=font, fill=ink)
    draw.line((100, 143, 720, 143), fill='black', width=2)
    actual = encode_restored_shared(tmp_path, source, crop_format=crop_format, media=media)
    _source_projected_shape(source, actual, (138, 118, 345, 142))


@pytest.mark.parametrize('outlined', [False, True])
@pytest.mark.parametrize('white_margin', [1, 2, 4])
def test_small_white_bubble_preserves_gray_stroke_with_narrow_white_margin(tmp_path, outlined, white_margin):
    source = Image.new('RGB', (1024, 600), 'white')
    draw = ImageDraw.Draw(source)
    draw.rectangle((100, 80, 400, 300), fill=(190, 190, 190))
    glyph = (220, 160, 230, 210)
    bubble = (glyph[0] - white_margin, glyph[1] - white_margin,
              glyph[2] + white_margin, glyph[3] + white_margin)
    if outlined:
        draw.rectangle((bubble[0] - 2, bubble[1] - 2, bubble[2] + 2, bubble[3] + 2), fill='black')
    draw.rectangle(bubble, fill='white')
    draw.rectangle(glyph, fill=(190, 190, 190))
    actual = encode_restored_shared(tmp_path, source)
    assert actual.getpixel((225, 185))[3] >= 240
    assert actual.getpixel((glyph[0] - 1, 185))[3] == 0


def test_component_labels_keep_a_one_pixel_gap_and_join_true_diagonals():
    mask = np.array([[1, 0, 1], [1, 0, 1]], dtype=bool)
    labels, count = backend._label_mask_components(mask)
    assert count == 2
    assert labels[0, 0] != labels[1, 2]
    labels, count = backend._label_mask_components(np.array([[1, 0], [0, 1]], dtype=bool))
    assert count == 1
    assert labels[0, 0] == labels[1, 1]


@pytest.mark.parametrize('crop_format', ['v1', 'v2'])
@pytest.mark.parametrize('media', ['off', 'auto'])
@pytest.mark.parametrize('fill', [200, 220])
def test_native_thin_outline_large_counter_does_not_become_opaque(tmp_path, crop_format, media, fill):
    # A large counter dominates a thin glyph's area. Median-brightness alone
    # must not mistake its sharpened white fringe for a real white bubble.
    scale = 4
    source = Image.new('RGB', (1024 * scale, 600 * scale), 'white')
    draw = ImageDraw.Draw(source)
    draw.rectangle((100 * scale, 80 * scale, 400 * scale, 300 * scale), fill=(fill,) * 3)
    draw.ellipse((180 * scale, 130 * scale, 220 * scale, 180 * scale), outline='black', width=2 * scale)
    source = source.resize((1024, 600), Image.Resampling.LANCZOS)
    actual = encode_restored_shared(tmp_path, source, crop_format=crop_format, media=media)
    _source_projected_shape(source, actual, (176, 126, 225, 185))
    assert actual.getpixel((200, 155))[3] < 100


def test_original_paper_evidence_is_bound_to_cached_crop_pixels(tmp_path, monkeypatch):
    paper = Image.new('RGB', (1024, 600), 'white')
    draw = ImageDraw.Draw(paper)
    draw.rectangle((100, 80, 400, 300), fill=(190,) * 3)
    draw.rectangle((217, 157, 233, 213), fill='black')
    draw.rectangle((219, 159, 231, 211), fill='white')
    draw.rectangle((220, 160, 230, 210), fill=(190,) * 3)
    counter = paper.copy()
    pixels = counter.load()
    for y in range(159, 212):
        for x in range(219, 232):
            if pixels[x, y] == (255, 255, 255):
                pixels[x, y] = (190,) * 3
    # Identical processed white pixels may be original bubble paper in one
    # source or a sharpening halo around a gray counter in another source.
    monkeypatch.setattr(board, '_enhance_problem_crop', lambda *_args, **_kwargs: paper.copy())
    path = tmp_path / 'original.png'
    chalk = board._resolve_chalk_color('charcoal')
    paper.save(path)
    bubble_result = board._cached_chalk_cutout(path, paper, chalk_color=chalk, text_priority=True)
    counter.save(path)
    counter_result = board._cached_chalk_cutout(path, counter, chalk_color=chalk, text_priority=True)
    assert bubble_result.getpixel((225, 185))[3] >= 240
    assert counter_result.getpixel((225, 185))[3] < 100
    assert len(list((tmp_path / board.CHALK_CUTOUT_CACHE_DIRNAME).glob('*.png'))) == 2


def test_misaligned_paper_reference_uses_the_legacy_conservative_fallback():
    source, _holes, _stroke = shaded_figure()
    processed = board._enhance_problem_crop(source, text_priority=True)
    expected, _ = backend.clean_problem_image_transparency(processed, remove_corner_page_artifacts=False)
    actual, _ = backend.clean_problem_image_transparency(processed, remove_corner_page_artifacts=False,
                                                        paper_reference_image=Image.new('RGB', (1, 1), 'white'))
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize('crop_format', ['v1', 'v2'])
@pytest.mark.parametrize('media', ['off', 'auto'])
def test_native_gradient_wedge_glyph_next_to_baseline_keeps_its_counter(tmp_path, crop_format, media):
    scale = 4
    width, height = 1024 * scale, 600 * scale
    source = Image.new('RGB', (width, height), 'white')
    wedge = Image.new('L', source.size, 0)
    ImageDraw.Draw(wedge).polygon([(100 * scale, 220 * scale), (100 * scale, 180 * scale),
                                  (250 * scale, 150 * scale), (400 * scale, 220 * scale)], fill=255)
    gradient = np.repeat(np.rint(np.linspace(248, 190, width))[None, :], height, axis=0).astype(np.uint8)
    source.paste(Image.fromarray(gradient).convert('RGB'), mask=wedge)
    draw = ImageDraw.Draw(source)
    draw.line((80 * scale, 220 * scale, 430 * scale, 220 * scale), fill='black', width=2 * scale)
    draw.line((230 * scale, 90 * scale, 400 * scale, 220 * scale), fill='black', width=2 * scale)
    draw.ellipse((340 * scale, 196 * scale, 355 * scale, 218 * scale), outline='black', width=scale)
    draw.line((341 * scale, 207 * scale, 354 * scale, 207 * scale), fill='black', width=scale)
    source = source.resize((1024, 600), Image.Resampling.LANCZOS)
    actual = encode_restored_shared(tmp_path, source, crop_format=crop_format, media=media)
    _source_projected_shape(source, actual, (337, 193, 358, 221))
    assert actual.getpixel((348, 202))[3] < 150
