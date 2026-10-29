from pathlib import Path
from urllib.parse import unquote, urlparse
import hashlib
import fitz
from PIL import Image
import pytest
from build_problem_board_edb import run_problem_export


def make_source(path, label):
    path.parent.mkdir(parents=True)
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=420)
        page.insert_text((25, 40), '[1~2] Read the passage and answer the questions.', fontsize=9)
        for n in range(5):
            page.insert_text((25, 75+n*18), f'{label} shared body sentence {n+1}.', fontsize=10)
        page.insert_text((25, 240), '1. First question', fontsize=10)
        page.insert_text((25, 310), '2. Second question', fontsize=10)
        doc.save(path)


@pytest.mark.parametrize('intent,target,expected', [
    ('page-as-is', 'all', 2), ('multi-problem', 'shared-passages', 2),
])
def test_same_basename_sources_keep_distinct_page_ids_and_pixels(tmp_path, intent, target, expected):
    sources = [tmp_path/'a'/'exam.pdf', tmp_path/'b'/'exam.pdf']
    for source,label in zip(sources, ['ALPHA', 'BETA']):make_source(source,label)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]
    output = tmp_path/'output'
    run_problem_export(sources, output_dir=output, subject_name='unknown', ocr='none',
                       pdf_dpi=72, detect_perspective=False, skip_deskew=True, skip_crop=True,
                       export_edb=True, input_intent=intent, content_target=target,
                       ai_fallback='off', record_mode='image-only', processing_step='raw')
    import json
    session = json.loads((output/'ui_session.json').read_text())
    pages = session['pages'];problems=session['problems']
    assert len(pages)==2 and len({p['id'] for p in pages})==2
    assert len(problems)==expected and len({p['id'] for p in problems})==expected
    assert problems[0]['sourcePageId'] != problems[1]['sourcePageId']
    actual=[]
    for problem in problems:
        path=Path(unquote(urlparse(problem['sourceImagePath']).path))
        with Image.open(path) as image:actual.append(hashlib.sha256(image.tobytes()).hexdigest())
    assert actual[0]!=actual[1], 'one source page replaced the other source pixels'
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]==before
    assert all(Path(p['edbPath']).is_file() for p in session['edb_parts'])
