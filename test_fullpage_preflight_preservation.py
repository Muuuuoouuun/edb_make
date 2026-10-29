from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

import build_problem_board_edb as board


def page_problem(tmp_path, **overrides):
    path = tmp_path / 'preserved-page.png'
    image = Image.new('RGB', (500, 800), 'white')
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, 0, 799), fill='black', width=3)
    draw.line((0, 780, 499, 780), fill='black', width=3)
    draw.text((60, 80), 'Source text and original page frame', fill='black')
    draw.text((350, 790), '37', fill='black')
    image.save(path)
    return {'id': 'source-page-1', 'imagePath': path.as_uri(), 'processingStep': 's2',
            'inputIntent': 'page-as-is', 'forceFullPageBounds': True,
            'placementMode': 'continuous-page-as-is', 'riskFlags': [], **overrides}


def test_preserved_full_page_frame_is_not_a_crop_artifact(tmp_path):
    problem = page_problem(tmp_path)
    with Image.open(tmp_path / 'preserved-page.png') as image:
        assert board._problem_image_page_chrome_artifact_stats(image)['hasArtifact']
    with patch.object(board, '_problem_image_page_chrome_artifact_stats', side_effect=AssertionError('unnecessary full-frame scan')):
        result = board._classin_handoff_preflight({'problems': [problem]})
    assert result['issues'] == []
    assert result['checkedProblemCount'] == 1


@pytest.mark.parametrize('overrides', [
    {'inputIntent': 'multi-problem'},
    {'forceFullPageBounds': False},
    {'forceFullPageBounds': 'true'},
    {'placementMode': 'single-problem'},
    {'placementMode': {}},
    {'fitWidthConverted': True},
    {'processingStep': 's3'},
])
def test_ordinary_or_unvalidated_page_still_reports_actual_frame(tmp_path, overrides):
    result = board._classin_page_chrome_artifact_issues([page_problem(tmp_path, **overrides)])
    assert [issue['type'] for issue in result] == [
        'step3_page_chrome_artifact' if overrides.get('processingStep') == 's3' else 'step2_page_chrome_artifact_rate'
    ]


def test_full_page_missing_image_still_fails_preflight(tmp_path):
    problem = page_problem(tmp_path, imagePath=(tmp_path / 'missing.png').as_uri())
    result = board._classin_handoff_preflight({'problems': [problem]})
    assert [issue['type'] for issue in result['issues']] == ['missing_problem_image']


def test_full_page_empty_source_still_warns(tmp_path):
    problem = page_problem(tmp_path)
    Image.new('RGB', (500, 800), 'white').save(tmp_path / 'preserved-page.png')
    result = board._classin_handoff_preflight({'problems': [problem]})
    assert [issue['type'] for issue in result['issues']] == ['low_ink_problem_image']


def test_snake_case_validated_page_preserves_frame(tmp_path):
    problem = page_problem(tmp_path)
    for camel, snake in [('inputIntent', 'input_intent'), ('forceFullPageBounds', 'force_full_page_bounds'), ('placementMode', 'placement_mode')]:
        problem[snake] = problem.pop(camel)
    assert board._classin_page_chrome_artifact_issues([problem]) == []
