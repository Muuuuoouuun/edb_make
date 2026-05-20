import sys
import logging
import traceback
logging.basicConfig(level=logging.DEBUG)
from preprocess import prepare_source_pages
from segment import segment_page
from pathlib import Path
from PIL import Image

from structured_schema import Subject

try:
    img = Image.new('RGB', (1000, 1000), color='white')
    img.save('dummy.jpg')
    pages = prepare_source_pages('dummy.jpg')
    seg = segment_page(pages[0], page_id='page1', subject=Subject.MATH)
    print('Segment success:', len(seg.blocks))
except Exception as e:
    traceback.print_exc()
