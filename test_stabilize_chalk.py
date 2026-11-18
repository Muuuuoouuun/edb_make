import numpy as np
from PIL import Image, ImageDraw

import image_reconstruction_backend as backend


def _page(paper, *, box=None, frame=None, ink=35):
    image = Image.new('RGB', (420, 320), paper)
    draw = ImageDraw.Draw(image)
    for x in range(30, 390, 14):
        draw.rectangle((x, 20, x + 3, 50), fill=(ink,) * 3)  # glyph strokes on paper
    if box:
        draw.rectangle((40, 90, 380, 290), fill=(box,) * 3)
        for x in range(70, 350, 14):
            draw.rectangle((x, 150, x + 3, 190), fill=(ink,) * 3)
    if frame:
        draw.rectangle((40, 90, 380, 290), outline=(frame,) * 3, width=12)
        for x in range(80, 340, 14):
            draw.rectangle((x, 160, x + 3, 200), fill=(ink,) * 3)
    return image


def _alpha(image, **kwargs):
    cleaned, _ = backend.clean_problem_image_transparency(image, remove_corner_page_artifacts=False, **kwargs)
    return np.asarray(cleaned)[..., 3]


def test_opencv_labels_match_the_python_fallback_exactly(monkeypatch):
    mask = np.random.default_rng(7).random((300, 500)) < 0.5
    fast, fast_count = backend._label_mask_components(mask)
    monkeypatch.setattr(backend, 'cv2', None)
    slow, slow_count = backend._label_mask_components(mask)
    assert fast_count == slow_count
    assert np.array_equal(fast, slow)
    assert backend._label_mask_components(np.zeros((0, 4), dtype=bool))[1] == 0


def test_white_paper_cutout_is_identical_with_and_without_opencv(monkeypatch):
    source = _page((255, 255, 255), box=190)
    expected = _alpha(source, paper_reference_image=source)
    monkeypatch.setattr(backend, 'cv2', None)
    assert np.array_equal(_alpha(source, paper_reference_image=source), expected)


def test_text_on_gray_paper_keeps_full_strength_with_paper_reference():
    source = _page((212, 212, 210), box=180)
    plain = _alpha(source)
    referenced = _alpha(source, paper_reference_image=source)
    assert plain[35, 31] == 255 and referenced[35, 31] == 255
    assert np.array_equal(referenced, plain)


def test_off_white_paper_reference_does_not_dim_print_inside_a_gray_frame():
    for paper in ((238, 238, 238), (246, 242, 228)):
        source = _page(paper, frame=180, ink=110)
        plain = _alpha(source)
        referenced = _alpha(source, paper_reference_image=source)
        assert referenced[180, 81] == 255
        assert np.array_equal(referenced, plain)
