from io import BytesIO
from pathlib import Path
import gzip
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image, ImageDraw

import build_problem_board_edb as board
from inspect_edb import parse_edb, OUTER_PREFIX_LEN


def original_page(number=37, size=(1024, 1536)):
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    for y in range(80, size[1] // 2, 40):
        draw.text((60, y), f'Original source page {number}: retain all content.', fill='black')
    draw.rectangle((size[0]-42, size[1]-24, size[0]-1, size[1]-1), outline='black', width=2)
    draw.text((size[0]-33, size[1]-20), str(number), fill='black')
    return image


def footer_ink(image):
    source = np.asarray(image.convert('RGB'))
    ink = np.any(source < 100, axis=2)
    roi = np.zeros_like(ink); roi[-24:, -42:] = True
    return ink & roi


@pytest.mark.parametrize('crop_format', ['v1', 'v2', 'auto'])
@pytest.mark.parametrize('preserve_media_mode', ['off', 'auto'])
def test_final_edb_retains_wholepage_footer_dimensions_and_order(tmp_path, crop_format, preserve_media_mode):
    sources=[];originals=[]
    for number in (37, 38):
        image=original_page(number);path=tmp_path/f'page-{number}.png';image.save(path)
        sources.append(path);originals.append(image)
    source_bytes=[p.read_bytes() for p in sources]
    result=board.run_problem_export(
        sources, output_dir=tmp_path/'out', input_intent='page-as-is', record_mode='image-only',
        processing_step='s2', crop_format=crop_format, preserve_media_mode=preserve_media_mode,
        ocr='none', detect_perspective=False, skip_crop=True, skip_deskew=True,
        trim_page_whitespace=False, max_dimension=None, export_edb=True, dark_board=True,
    )
    session=result['ui_session'];by_id={p['id']:p for p in session['problems']}
    page_numbers={p['id']:p['pageNumber'] for p in session['pages']}
    observed=[]
    for part in result['edb_parts']:
        parsed=parse_edb(Path(part['edbPath']));inner=gzip.decompress(Path(part['edbPath']).read_bytes()[OUTER_PREFIX_LEN:])
        assert parsed.page_count_hint <= 50
        for pid,record in zip(part['problemIds'],parsed.records):
            primary=record.embedded_images[0];start=record.offset+primary.offset
            with Image.open(BytesIO(inner[start:start+primary.length])) as decoded:
                image=decoded.convert('RGBA')
            original=originals[len(observed)]
            assert image.size == original.size
            mask=footer_ink(original);assert int(mask.sum()) > 100
            assert np.all(np.asarray(image)[...,3][mask] > 16)
            all_source_ink=np.any(np.asarray(original)<100,axis=2)
            assert np.all(np.asarray(image)[...,3][all_source_ink] > 16)
            observed.append(page_numbers[by_id[pid]['sourcePageId']])
    assert observed == [1,1]  # Separate input images each retain their first source page.
    assert [by_id[pid]['sourceFileName'] for part in result['edb_parts'] for pid in part['problemIds']] == [p.name for p in sources]
    assert [p.read_bytes() for p in sources] == source_bytes


@pytest.mark.parametrize('first_preserves', [False, True])
def test_corner_cleanup_cache_is_segregated_in_both_orders(tmp_path, first_preserves):
    source=tmp_path/'source.png';image=original_page();image.save(source)
    variants={}
    for preserve in (first_preserves, not first_preserves):
        variants[preserve]=board._cached_chalk_cutout(source,image,chalk_color=(248,249,246),text_priority=False,preserve_page_content=preserve)
    mask=footer_ink(image)
    assert np.all(np.asarray(variants[True])[...,3][mask] > 16)
    assert not np.any(np.asarray(variants[False])[...,3][mask] > 16)
    with patch.object(board,'_extract_problem_cutout',side_effect=AssertionError('cache policy collision or miss')):
        for preserve in (False,True):
            cached=board._cached_chalk_cutout(source,image,chalk_color=(248,249,246),text_priority=False,preserve_page_content=preserve)
            assert cached.tobytes() == variants[preserve].tobytes()


def test_initial_page_render_keeps_footer_and_seeds_distinct_cache(tmp_path):
    path=tmp_path/'source.png';image=original_page();image.save(path)
    task=board._ProblemAssetTask(
        source_image=image, bounds=board.Box(left=0,top=0,width=image.width,height=image.height),
        crop_path=path, board_render_path=tmp_path/'board.png', chalk_color=(248,249,246),
        preserve_page_content=True,
    )
    rendered=board._build_problem_board_asset(image,task)
    assert np.all(np.asarray(rendered)[...,3][footer_ink(image)] > 16)
    with patch.object(board,'_extract_problem_cutout',side_effect=AssertionError('initial page cache not reusable')):
        cached=board._cached_chalk_cutout(path,image,chalk_color=task.chalk_color,text_priority=False,preserve_page_content=True)
    assert cached.tobytes() == rendered.tobytes()
