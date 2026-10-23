from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

from gallery_recognition import read_image_label, local_label_backend, MAX_RECOGNITION_BATCH
from ocr_backend import OCRLine, OCRResult
from structured_schema import Box
from test_problem_gallery import fake_parse, pdf_bytes
from problem_gallery import GalleryStore


def result_for(rows, confidence=.95):
    return OCRResult("\n".join(rows), confidence, [
        OCRLine(text, confidence, Box(left=10, top=10 + index * 45, width=500, height=30))
        for index, text in enumerate(rows)
    ], "tesseract")


class TestGalleryLabels(unittest.TestCase):
    def test_parser_keeps_shared_passage_role_and_child_number_evidence(self):
        from problem_parser import parse_problems
        from test_problem_parser import _cross_page_passage_fixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared, pages = _cross_page_passage_fixture(root)
            with mock.patch("problem_parser._leading_pages_copy", return_value=(root/"exam.pdf",2)), \
                    mock.patch("build_problem_board_edb.build_pages", return_value=(prepared,pages)):
                result = parse_problems(root/"exam.pdf",work_dir=root/"out")
            try:
                passages = [p for p in result.problems if p.passage_role == "passage_fragment"]
                children = [p for p in result.problems if p.passage_role == "child_question"]
                self.assertEqual(1,len(passages))
                self.assertIsNone(passages[0].number)
                self.assertEqual({"start":18,"end":21},passages[0].passage_range)
                self.assertEqual([18,19,20,21],[p.number for p in children])
                self.assertTrue(all(p.number_source == "text_prefix" for p in children))
            finally:
                for page in result.pages: page.image.close()
                for problem in result.problems: problem.image.close()

    def read(self, rows, confidence=.95):
        with Image.new("RGB", (800, 1000), "white") as image:
            return read_image_label(image, mock.Mock(ocr_image=mock.Mock(return_value=result_for(rows, confidence))))

    def test_printed_markers_and_passages_are_distinct(self):
        for text, number in [("123. 다음 식의 값을 구하시오.",123), ("Q 12) Which is correct?",12),
                ("제 15 번 다음 식을 계산하시오.",15), ("１２．다음 물음에 답하시오.",12)]:
            with self.subTest(text=text):
                label = self.read([text, "body text"])
                self.assertEqual(number, label["number"])
                self.assertEqual("", label["passageRole"])
        passage = self.read(["[12~15] 다음 글 을 읽 고 물 음 에 답 하시오.", "봄이 왔다."])
        self.assertIsNone(passage["number"])
        self.assertEqual("passage_fragment", passage["passageRole"])
        self.assertEqual({"start":12,"end":15}, passage["passageRange"])
        unnumbered_passage = self.read(["공통 지문", "봄이 왔다."])
        self.assertEqual("passage_fragment", unnumbered_passage["passageRole"])
        self.assertIsNone(unnumbered_passage["passageRange"])
        self.assertEqual(12, self.read(["12. 다음 글의 주제로 적절한 것은?", "[12~15] 다음 글을 읽고 물음에 답하시오."])["number"])

    def test_does_not_invent_labels_for_unknown_low_confidence_or_mixed_crops(self):
        for rows in [[], ["봄이 와서 나무에 잎이 돋았다."], ["① 사과 ② 바나나 ③ 귤"],
                ["1.5 + 2.7 = ?"], ["[12~15]"],
                ["[12~15] 다음 글을 읽고 물음에 답하시오.","봄이 왔다.","12. 적절한 것은?"]]:
            with self.subTest(rows=rows):
                self.assertIsNone(self.read(rows))
        self.assertIsNone(self.read(["123. 다음 물음에 답하시오."], confidence=.3))

    def test_word_boxes_are_grouped_into_header_before_matching(self):
        words = ["[12~15]", "다음", "글을", "읽고", "물음에", "답하시오."]
        result = OCRResult("\n".join(words), .98, [
            OCRLine(word,.98,Box(left=10+i*100,top=12+(i%2)*2,width=90,height=25))
            for i,word in enumerate(words)
        ], "tesseract")
        backend = mock.Mock(ocr_image=mock.Mock(return_value=result))
        with Image.new("RGB",(600,900),"white") as image:
            self.assertEqual("passage_fragment", read_image_label(image, backend)["passageRole"])

    def test_missing_engine_and_failed_ocr_are_explicit_and_work_is_bounded(self):
        with mock.patch("gallery_recognition.build_ocr_backend", return_value=mock.Mock(name="none")) as build:
            build.return_value.name = "none"
            with self.assertRaisesRegex(ValueError,"로컬 OCR"):
                local_label_backend()
        seen = []
        def fail(image):
            seen.append(image.size)
            return OCRResult("",None,metadata={"error":"timeout"})
        with Image.new("RGB",(2000,4000),"white") as image:
            with self.assertRaisesRegex(ValueError,"읽지 못했습니다"):
                read_image_label(image,mock.Mock(ocr_image=fail))
            self.assertEqual((2000,4000),image.size)
        self.assertLessEqual(seen[0][0],1800)
        self.assertLessEqual(seen[0][1],2400)

    def test_saved_metadata_updates_without_pixels_or_stale_zip_and_retries_skip_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GalleryStore(Path(directory))
            data = pdf_bytes(3)
            job = store.upload(io.BytesIO(data),len(data),"교재.pdf")
            with mock.patch("problem_parser.parse_problems",side_effect=fake_parse):
                job = store.parse_next(job["id"])
            for record in job["problems"]:
                record["problemNumberSource"] = "inferred_sequence"
                record["riskFlags"] = ["number_unconfirmed","crop_review"]
            store.save(job)
            ids = [record["id"] for record in job["problems"]]
            images = {record["imageFile"]:(store.job_dir(job["id"])/record["imageFile"]).read_bytes() for record in job["problems"]}
            old_zip,_ = store.export_images(ids)
            label={"number":123,"problemNumberSource":"ocr_top_left","passageRole":"","passageRange":None}
            passage={"number":None,"problemNumberSource":"ocr_passage_header","passageRole":"passage_fragment","passageRange":{"start":12,"end":15}}
            with mock.patch("gallery_recognition.local_label_backend"), mock.patch("gallery_recognition.read_image_label",side_effect=[label,passage,ValueError("OCR timeout")]) as read:
                result=store.recognize_labels(ids)
                self.assertEqual((2,1,1),(result["recognized"],result["unconfirmed"],result["errors"]))
                self.assertEqual(3,read.call_count)
            saved=store.load(job["id"])
            self.assertEqual([123,None,3],[p["number"] for p in saved["problems"]])
            self.assertEqual(["crop_review"],saved["problems"][0]["riskFlags"])
            self.assertEqual(images,{p["imageFile"]:(store.job_dir(job["id"])/p["imageFile"]).read_bytes() for p in saved["problems"]})
            new_zip,_=store.export_images(ids)
            self.assertNotEqual(old_zip,new_zip)
            with zipfile.ZipFile(new_zip) as archive:
                metadata=json.loads(archive.read("manifest.json"))
                self.assertEqual("passage_fragment",metadata[1]["passageRole"])
                self.assertEqual(123,metadata[0]["number"])
            with mock.patch("gallery_recognition.local_label_backend"), mock.patch("gallery_recognition.read_image_label",return_value=label) as read:
                result=store.recognize_labels(ids)
                self.assertEqual(1,read.call_count)
                self.assertEqual(1,result["recognized"])
            with mock.patch("gallery_recognition.local_label_backend",side_effect=AssertionError("no duplicate OCR")):
                self.assertEqual(0,store.recognize_labels(ids)["unconfirmed"])
            before=store.load(job["id"])
            for invalid in [None,[],[True],ids*(MAX_RECOGNITION_BATCH+1),[ids[0],job["id"]+":missing"]]:
                with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                    store.recognize_labels(invalid)
            self.assertEqual(before,store.load(job["id"]))

    def test_automatic_attempts_are_persisted_but_explicit_retry_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GalleryStore(Path(directory))
            data = pdf_bytes(1)
            job = store.upload(io.BytesIO(data),len(data),"교재.pdf")
            with mock.patch("problem_parser.parse_problems",side_effect=fake_parse):
                job = store.parse_next(job["id"])
            job["problems"][0]["problemNumberSource"] = ""
            store.save(job)
            ids = [job["problems"][0]["id"]]
            with mock.patch("gallery_recognition.local_label_backend"), mock.patch("gallery_recognition.read_image_label",return_value=None) as read:
                self.assertEqual(1,store.recognize_labels(ids,only_unattempted=True)["unconfirmed"])
                self.assertTrue(store.load(job["id"])["problems"][0]["labelRecognitionAttempted"])
                # A fresh store models another tab or an app restart.
                self.assertEqual(0,GalleryStore(store.root).recognize_labels(ids,only_unattempted=True)["unconfirmed"])
                self.assertEqual(1,read.call_count)
                self.assertEqual(1,store.recognize_labels(ids)["unconfirmed"])
                self.assertEqual(2,read.call_count)


if __name__ == "__main__":
    unittest.main()
