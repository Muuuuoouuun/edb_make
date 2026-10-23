"""Disk-backed, bounded batches for the desktop problem-only gallery.

This store never creates an EDB, board assets, placements, or an editor session.
Only metadata survives between batches; source pages and full-size crops do not.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from gallery_recognition import LABEL_FIELDS, LABEL_REVIEW_FLAG, MAX_RECOGNITION_BATCH, label_is_confirmed

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
BATCH_PAGES = 12
CONTEXT_PAGES = 2
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}
EXPORT_LABELS = {"raw": "기본자르기", "transparent": "배경제거", "both": "둘다"}
EXPORT_MODES = set(EXPORT_LABELS)
EXPORT_VERSION = 1
EXPORT_NAMING_VERSION = 2


def gallery_image_filename(job: dict, problem: dict) -> str:
    """Use the printed number, never a generated sequence masquerading as one."""
    basename = re.split(r"[\\/]", job["sourceName"])[-1]
    stem = unicodedata.normalize("NFC", Path(basename).stem)
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem).strip(" .") or "자료"
    if problem.get("passageRole") == "passage_fragment":
        passage_range = problem.get("passageRange")
        label = (f"지문_{passage_range['start']:02d}~{passage_range['end']:02d}번"
            if passage_range else "지문")
    elif label_is_confirmed(problem):
        label = f"{problem['number']:02d}번"
    else:
        label = f"번호미확인_{Path(problem['imageFile']).stem.removeprefix('p')}"
    # Keep the complete source name unless the filesystem's byte limit requires
    # shortening it. Leave room for duplicate-name suffixes on every platform.
    suffix = f"_{label}.png"
    budget = max(1, 220 - len(suffix.encode("utf-8")))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .") or "자료"
    return stem + suffix


class GalleryStore:
    def __init__(self, root: Path):
        self.root = root

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", str(job_id)):
            raise ValueError("추출 작업을 찾을 수 없습니다.")
        return self.root / job_id

    def load(self, job_id: str) -> dict[str, Any]:
        def require(condition):
            if not condition:
                raise ValueError("invalid gallery record")

        try:
            job = json.loads((self.job_dir(job_id) / "manifest.json").read_text(encoding="utf-8"))
            # A damaged record must neither break every other source nor allow
            # persisted file names to escape this job's directory.
            require(isinstance(job, dict) and job["id"] == job_id)
            require(job["sourceFile"] in {f"source{suffix}" for suffix in SUPPORTED_SUFFIXES})
            require(isinstance(job["sourceName"], str))
            require(type(job["sourcePageCount"]) is int and job["sourcePageCount"] > 0)
            require(type(job["processedPages"]) is int and 0 <= job["processedPages"] <= job["sourcePageCount"])
            require(isinstance(job["createdAt"], (int, float)) and math.isfinite(job["createdAt"]))
            require(job["status"] in {"pending", "partial", "complete", "failed"})
            require(isinstance(job["problems"], list))
            seen = set()
            for problem in job["problems"]:
                require(isinstance(problem, dict))
                require(re.fullmatch(r"p[0-9]{6,}\.png", problem["imageFile"]))
                require(problem["id"] == f"{job_id}:{Path(problem['imageFile']).stem}")
                require(problem["id"] not in seen)
                seen.add(problem["id"])
                require(isinstance(problem["title"], str))
                require(problem["number"] is None or type(problem["number"]) is int)
                require(isinstance(problem.get("problemNumberSource", ""), str))
                require(isinstance(problem.get("passageRole", ""), str))
                require(type(problem.get("labelRecognitionAttempted", False)) is bool)
                passage_range = problem.get("passageRange")
                require(passage_range is None or isinstance(passage_range, dict) and
                    all(type(passage_range.get(key)) is int for key in ("start", "end")) and
                    0 < passage_range["start"] < passage_range["end"])
                require(isinstance(problem["pageNumbers"], list) and problem["pageNumbers"])
                require(all(type(page) is int and 1 <= page <= job["sourcePageCount"] for page in problem["pageNumbers"]))
                require(isinstance(problem["riskFlags"], list) and all(isinstance(flag, str) for flag in problem["riskFlags"]))
                require(isinstance(problem["regions"], list))
                require(all(type(problem[key]) is int and problem[key] > 0 for key in ("width", "height")))
            return job
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("저장된 추출 작업을 읽을 수 없습니다. 원본 자료를 다시 추가해 주세요.") from exc

    def save(self, job: dict[str, Any]) -> None:
        root = self.job_dir(job["id"])
        temporary = root / "manifest.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, root / "manifest.json")

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = []
        if self.root.exists():
            for path in self.root.glob("*/manifest.json"):
                try:
                    jobs.append(self.load(path.parent.name))
                except ValueError:
                    continue
        return sorted(jobs, key=lambda job: job["createdAt"], reverse=True)

    def upload(self, stream: BinaryIO, size: int, name: str) -> dict[str, Any]:
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("문제 추출은 PDF, PNG, JPG, WEBP, BMP 파일을 지원합니다.")
        if not 0 < size <= MAX_UPLOAD_BYTES:
            raise ValueError("파일은 512MB 이하로 올려 주세요.")
        job_id = uuid.uuid4().hex
        root = self.job_dir(job_id)
        root.mkdir(parents=True)
        source = root / f"source{suffix}"
        try:
            with source.open("wb") as target:
                remaining = size
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("파일 업로드가 중단됐습니다. 다시 올려 주세요.")
                    target.write(block)
                    remaining -= len(block)
            if suffix == ".pdf":
                import fitz

                try:
                    with fitz.open(source) as document:
                        if not document.is_pdf or document.needs_pass:
                            raise ValueError("암호가 없는 PDF를 올려 주세요.")
                        page_count = document.page_count
                except (fitz.FileDataError, RuntimeError) as exc:
                    raise ValueError("읽을 수 없는 PDF입니다. 원본 파일을 확인해 주세요.") from exc
            else:
                from PIL import Image

                try:
                    with Image.open(source) as image:
                        image.verify()
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    raise ValueError("이미지를 읽을 수 없습니다. 파일이나 해상도를 확인해 주세요.") from exc
                page_count = 1
            if not page_count:
                raise ValueError("내용이 없는 파일입니다.")
            job = {
                "id": job_id, "sourceName": Path(name.replace("\\", "/")).name[:200],
                "sourceFile": source.name, "sourcePageCount": page_count,
                "processedPages": 0, "createdAt": time.time(), "status": "pending",
                "problems": [], "error": None,
            }
            self.save(job)
            return job
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise

    def parse_next(self, job_id: str, expected_processed_pages: int | None = None) -> dict[str, Any]:
        from problem_parser import parse_problems

        job = self.load(job_id)
        start = job["processedPages"]
        count = job["sourcePageCount"]
        if expected_processed_pages is not None:
            if type(expected_processed_pages) is not int or not 0 <= expected_processed_pages <= count:
                raise ValueError("추출 진행 정보를 다시 불러와 주세요.")
            if expected_processed_pages < start:
                return job  # A retried request must not advance another batch.
            if expected_processed_pages > start:
                raise ValueError("추출 진행 정보를 다시 불러와 주세요.")
        if start >= count:
            return job
        root = self.job_dir(job_id)
        source = root / job["sourceFile"]
        end = min(count, start + BATCH_PAGES)
        # Context on both sides preserves ordinary cross-page passages. A crop
        # belongs to the batch containing the question's own page (which can
        # differ from a prepended shared passage), so overlap does not create
        # duplicates or drop later child questions. Crops reaching a context
        # edge are explicitly flagged rather than silently treated as complete.
        context_start = max(0, start - CONTEXT_PAGES)
        context_end = min(count, end + CONTEXT_PAGES)
        result = None
        new_paths: list[Path] = []
        try:
            with tempfile.TemporaryDirectory(prefix="batch-", dir=root) as temporary:
                work = Path(temporary)
                # Keep preprocessing caches inside this temporary batch too.
                if source.suffix == ".pdf":
                    import fitz

                    parse_source = work / "pages.pdf"
                    with fitz.open(source) as document, fitz.open() as batch:
                        batch.insert_pdf(document, from_page=context_start, to_page=context_end - 1)
                        batch.save(parse_source)
                else:
                    parse_source = work / source.name
                    try:
                        os.link(source, parse_source)
                    except OSError:
                        shutil.copyfile(source, parse_source)
                result = parse_problems(
                    parse_source, work_dir=work / "parse", ocr_mode="local",
                    render_board_assets=False, preserve_source_pixels=True,
                )
                page_numbers = {page.page_id: context_start + page.index + 1 for page in result.pages}
                added = []
                for problem in result.problems:
                    pages = sorted({page_numbers[region.page_id] for region in problem.regions})
                    if not pages:
                        continue
                    owner_page = page_numbers.get(problem.source_page_id, pages[0])
                    if not start < owner_page <= end:
                        continue
                    sequence = len(job["problems"]) + len(added) + 1
                    problem_id = f"{job_id}:p{sequence:06d}"
                    image_file = f"p{sequence:06d}.png"
                    target = root / image_file
                    new_paths.append(target)
                    problem.image.save(target, format="PNG")
                    flags = list(problem.risk_flags)
                    if ((context_start > 0 and pages[0] == context_start + 1)
                            or (context_end < count and pages[-1] == context_end)):
                        flags.append("batch_boundary_review")
                    record = {
                        "id": problem_id, "title": problem.title,
                        "number": problem.number, "pageNumbers": pages,
                        "problemNumberSource": problem.number_source,
                        "passageRole": problem.passage_role,
                        "passageRange": problem.passage_range,
                        "riskFlags": sorted(set(flags)), "imageFile": image_file,
                        "width": problem.image.width, "height": problem.image.height,
                        "regions": [{
                            "page": page_numbers[region.page_id],
                            "bbox": {key: getattr(region.bbox, key) for key in ("left", "top", "width", "height")},
                        } for region in problem.regions],
                    }
                    if not label_is_confirmed(record):
                        record["riskFlags"] = sorted(set([*record["riskFlags"], LABEL_REVIEW_FLAG]))
                    added.append(record)
                job["problems"].extend(added)
                job.update(processedPages=end, status="complete" if end == count else "partial", error=None)
                self.save(job)
        except Exception as exc:
            for path in new_paths:
                path.unlink(missing_ok=True)
            # Reload the last committed batch. Retrying cannot lose or duplicate
            # earlier crops, including when a later batch or disk write fails.
            job = self.load(job_id)
            job.update(status="failed", error=str(exc))
            self.save(job)
            raise
        finally:
            if result is not None:
                for page in result.pages:
                    page.image.close()
                for problem in result.problems:
                    problem.image.close()
                    if problem.board_image is not None:
                        problem.board_image.close()
        return job

    def recognize_labels(self, problem_ids: list[str], *, only_unattempted: bool = False) -> dict[str, Any]:
        from PIL import Image
        from gallery_recognition import local_label_backend, read_image_label

        if not isinstance(problem_ids, list) or not 0 < len(problem_ids) <= MAX_RECOGNITION_BATCH \
                or not all(isinstance(value, str) for value in problem_ids):
            raise ValueError(f"한 번에 {MAX_RECOGNITION_BATCH}개 이하의 문제를 인식할 수 있습니다.")
        jobs, records = {}, []
        # Validate the entire request before starting OCR or changing metadata.
        for problem_id in dict.fromkeys(problem_ids):
            job_id = problem_id.split(":", 1)[0]
            if job_id not in jobs:
                jobs[job_id] = self.load(job_id)
            record = next((item for item in jobs[job_id]["problems"] if item["id"] == problem_id), None)
            if record is None:
                raise ValueError("인식할 문제를 찾을 수 없습니다. 목록을 새로 불러오세요.")
            records.append((job_id, record))
        pending = [(job_id, record) for job_id, record in records if not label_is_confirmed(record)
            and not (only_unattempted and record.get("labelRecognitionAttempted"))]
        backend = local_label_backend() if pending else None
        recognized, errors = 0, 0
        for job_id, record in pending:
            record.pop("labelError", None)
            try:
                with Image.open(self.job_dir(job_id) / record["imageFile"]) as image:
                    label = read_image_label(image, backend)
                if label:
                    record.update(label)
                    record["riskFlags"] = [flag for flag in record["riskFlags"] if flag != LABEL_REVIEW_FLAG]
                    recognized += 1
                else:
                    record["riskFlags"] = sorted(set([*record["riskFlags"], LABEL_REVIEW_FLAG]))
            except (OSError, ValueError) as exc:
                errors += 1
                record["labelError"] = str(exc)
                record["riskFlags"] = sorted(set([*record["riskFlags"], LABEL_REVIEW_FLAG]))
            # Unknown text is a completed attempt too; opening the gallery must
            # not keep re-running OCR on an unreadable crop. Explicit retry is allowed.
            record["labelRecognitionAttempted"] = True
            # Save each completed image, so a later failure/retry loses no labels.
            self.save(jobs[job_id])
        return {"jobs": list(jobs.values()), "recognized": recognized,
            "unconfirmed": len(pending) - recognized, "errors": errors}

    def _transparent_image(self, source: Path) -> Path:
        from PIL import Image
        from image_reconstruction_backend import clean_problem_image_transparency

        stat = source.stat()
        stamp = f"{EXPORT_VERSION}:{stat.st_size}:{stat.st_mtime_ns}"
        digest = hashlib.sha256(stamp.encode()).hexdigest()[:16]
        target = source.with_name(f"{source.stem}-transparent-{digest}.png")
        if target.is_file():
            return target
        temporary = target.with_suffix(".tmp")
        try:
            with Image.open(source) as image:
                # Reuse the editor's paper-removal algorithm, retaining ink
                # colors and dimensions. Do not remove corner content or
                # upscale hundreds of crops solely to save transparent PNGs.
                cleaned, _ = clean_problem_image_transparency(image, remove_corner_page_artifacts=False)
                try:
                    cleaned.save(temporary, format="PNG")
                finally:
                    cleaned.close()
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def image_download_name(self, problem_id: str) -> str:
        job = self.load(problem_id.split(":", 1)[0])
        problem = next((item for item in job["problems"] if item["id"] == problem_id), None)
        if problem is None:
            raise ValueError("다운로드할 문제를 찾을 수 없습니다.")
        return gallery_image_filename(job, problem)

    def export_images(self, problem_ids: list[str], mode: str = "raw", *, single: bool = False) -> tuple[Path, int]:
        if not isinstance(mode, str) or mode not in EXPORT_MODES:
            raise ValueError("저장 옵션은 기본 자르기, 배경 제거, 둘 다 중에서 선택하세요.")
        selected = list(dict.fromkeys(problem_ids))
        if not selected:
            raise ValueError("다운로드할 문제를 선택하세요.")
        jobs: dict[str, dict[str, Any]] = {}
        records = []
        for problem_id in selected:
            job_id = str(problem_id).split(":", 1)[0]
            if job_id not in jobs:
                job = self.load(job_id)
                jobs[job_id] = {problem["id"]: (job, problem) for problem in job["problems"]}
            record = jobs[job_id].get(problem_id)
            if record is None:
                raise ValueError("선택한 문제를 찾을 수 없습니다. 목록을 새로 불러오세요.")
            records.append(record)
        if single and len(records) != 1:
            raise ValueError("개별 저장은 문제 하나를 선택하세요.")
        fingerprints = []
        for job, problem in records:
            source = self.job_dir(job["id"]) / problem["imageFile"]
            try:
                stat = source.stat()
            except FileNotFoundError as exc:
                raise ValueError("문제 이미지가 없습니다. 원본 자료를 다시 추출해 주세요.") from exc
            fingerprints.append((problem["id"], stat.st_size, stat.st_mtime_ns, job["sourceName"],
                {key: problem.get(key) for key in ("number", "riskFlags", *LABEL_FIELDS)}))
        if single and mode != "both":
            return (self._transparent_image(source) if mode == "transparent" else source), 1
        self.root.mkdir(parents=True, exist_ok=True)
        # Immutable crops let repeated saves reuse a completed archive. Partial
        # archives are never published, including after an interrupted process.
        digest = hashlib.sha256(json.dumps([EXPORT_VERSION, EXPORT_NAMING_VERSION, mode, single, fingerprints]).encode()).hexdigest()
        archive_name = Path(gallery_image_filename(*records[0])).with_suffix('.zip').name if single else f"문제_{EXPORT_LABELS[mode]}.zip"
        target = self.root / "exports" / digest / archive_name
        if target.is_file():
            return target, len(records)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
                manifest = []
                used_names = set()
                for job, problem in records:
                    original_name = gallery_image_filename(job, problem)
                    filename, duplicate = original_name, 1
                    while filename.casefold() in used_names:
                        duplicate += 1
                        filename = f"{Path(original_name).stem}_({duplicate}).png"
                    used_names.add(filename.casefold())
                    source = self.job_dir(job["id"]) / problem["imageFile"]
                    files = {}
                    if mode in {"raw", "both"}:
                        files["raw"] = f"raw_crops/{filename}"
                        archive.write(source, files["raw"])
                    if mode in {"transparent", "both"}:
                        files["transparent"] = f"transparent_images/{filename}"
                        archive.write(self._transparent_image(source), files["transparent"])
                    manifest.append({
                        **{key: problem[key] for key in ("id", "title", "number", "pageNumbers", "riskFlags", "regions")},
                        **{key: problem.get(key) for key in LABEL_FIELDS},
                        "sourceName": job["sourceName"], "mode": mode, "files": files,
                        "file": next(iter(files.values())),
                    })
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target, len(records)
