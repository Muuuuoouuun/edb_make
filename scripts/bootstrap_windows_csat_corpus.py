#!/usr/bin/env python3
"""Build a private Windows corpus covering every current CSAT subject.

KICE publishes Korean, mathematics, English, and Korean history as PDFs.
Social/science/vocational inquiry and second-language/Classical Chinese papers
are ZIP archives containing one PDF per subject.  Copyrighted source files are
kept only under the explicitly selected local output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.bootstrap_windows_exam_corpus import (  # noqa: E402
    CorpusBootstrapError,
    ExamAsset,
    KICE_DOWNLOAD_URL,
    KICE_LIST_URL,
    _decode_html,
    _request,
    _strip_tags,
    expected_question_numbers,
    extract_printed_ranges,
)


@dataclass(frozen=True)
class SubjectSpec:
    subject: str
    display_name: str
    area: str
    area_label: str
    pipeline_subject: str
    expected_question_max: int
    form_page_count: int
    archive_member: bool = False


def _spec(
    subject: str,
    display_name: str,
    area: str,
    area_label: str,
    pipeline_subject: str,
    expected_question_max: int,
    form_page_count: int,
    *,
    archive_member: bool = False,
) -> SubjectSpec:
    return SubjectSpec(
        subject=subject,
        display_name=display_name,
        area=area,
        area_label=area_label,
        pipeline_subject=pipeline_subject,
        expected_question_max=expected_question_max,
        form_page_count=form_page_count,
        archive_member=archive_member,
    )


CSAT_SUBJECT_SPECS: tuple[SubjectSpec, ...] = (
    _spec("korean", "국어", "korean", "국어", "korean", 45, 20),
    _spec("math", "수학", "math", "수학", "math", 30, 20),
    _spec("english", "영어", "english", "영어", "english", 45, 8),
    _spec(
        "korean-history",
        "한국사",
        "korean-history",
        "한국사",
        "social",
        20,
        4,
    ),
    _spec("social-life-ethics", "생활과 윤리", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-ethics-thought", "윤리와 사상", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-korean-geography", "한국지리", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-world-geography", "세계지리", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-east-asian-history", "동아시아사", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-world-history", "세계사", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-economics", "경제", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-politics-law", "정치와 법", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("social-society-culture", "사회·문화", "social", "사회탐구", "social", 20, 4, archive_member=True),
    _spec("science-physics-1", "물리학Ⅰ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-chemistry-1", "화학Ⅰ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-life-science-1", "생명과학Ⅰ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-earth-science-1", "지구과학Ⅰ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-physics-2", "물리학Ⅱ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-chemistry-2", "화학Ⅱ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-life-science-2", "생명과학Ⅱ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec("science-earth-science-2", "지구과학Ⅱ", "science", "과학탐구", "science", 20, 4, archive_member=True),
    _spec(
        "vocational-successful-worklife",
        "성공적인 직업생활",
        "vocational",
        "직업탐구",
        "unknown",
        20,
        4,
        archive_member=True,
    ),
    _spec(
        "vocational-agricultural-basics",
        "농업 기초 기술",
        "vocational",
        "직업탐구",
        "unknown",
        20,
        4,
        archive_member=True,
    ),
    _spec(
        "vocational-industry-general",
        "공업 일반",
        "vocational",
        "직업탐구",
        "unknown",
        20,
        4,
        archive_member=True,
    ),
    _spec(
        "vocational-commercial-economics",
        "상업 경제",
        "vocational",
        "직업탐구",
        "unknown",
        20,
        4,
        archive_member=True,
    ),
    _spec(
        "vocational-fisheries-shipping-basics",
        "수산·해운 산업 기초",
        "vocational",
        "직업탐구",
        "unknown",
        20,
        4,
        archive_member=True,
    ),
    _spec(
        "vocational-human-development",
        "인간 발달",
        "vocational",
        "직업탐구",
        "unknown",
        20,
        4,
        archive_member=True,
    ),
    _spec("foreign-german-1", "독일어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-french-1", "프랑스어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-spanish-1", "스페인어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-chinese-1", "중국어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-japanese-1", "일본어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-russian-1", "러시아어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-arabic-1", "아랍어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec("foreign-vietnamese-1", "베트남어Ⅰ", "foreign", "제2외국어/한문", "unknown", 30, 4, archive_member=True),
    _spec(
        "foreign-classical-chinese-1",
        "한문Ⅰ",
        "foreign",
        "제2외국어/한문",
        "unknown",
        30,
        4,
        archive_member=True,
    ),
)

SPECS_BY_AREA: dict[str, tuple[SubjectSpec, ...]] = {
    area: tuple(spec for spec in CSAT_SUBJECT_SPECS if spec.area == area)
    for area in {spec.area for spec in CSAT_SUBJECT_SPECS}
}
AREA_LABEL_TO_KEY = {
    specs[0].area_label: area for area, specs in SPECS_BY_AREA.items()
}


@dataclass(frozen=True)
class KiceAreaAsset:
    year: int
    area: str
    area_label: str
    board_seq: str
    source_page_url: str
    file_seq: str
    file_name: str

    @property
    def download_url(self) -> str:
        return KICE_DOWNLOAD_URL.format(file_seq=self.file_seq)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", html.unescape(value)).strip()


def _problem_file(files: Iterable[tuple[str, str]], *, archive: bool) -> tuple[str, str]:
    suffix = ".zip" if archive else ".pdf"
    candidates = [
        (file_seq, _normalize(title))
        for file_seq, title in files
        if _normalize(title).lower().endswith(suffix)
        and ("문제지" in _normalize(title) or "_문제" in _normalize(title))
        and "정답" not in _normalize(title)
        and "해설" not in _normalize(title)
    ]
    if not candidates:
        raise CorpusBootstrapError(f"no {suffix} problem file in KICE row")
    preferred = next(
        (
            item
            for item in candidates
            if "홀수형" in item[1] or "짝수형" not in item[1]
        ),
        candidates[0],
    )
    return preferred


def extract_kice_area_assets(
    page_html: str,
    *,
    years: set[int],
) -> list[KiceAreaAsset]:
    assets: list[KiceAreaAsset] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, flags=re.IGNORECASE | re.DOTALL)
    area_pattern = "|".join(re.escape(label) for label in sorted(AREA_LABEL_TO_KEY, key=len, reverse=True))
    for row in rows:
        row_text = _strip_tags(row)
        match = re.search(
            rf"\b(?P<seq>\d{{7}})\s+(?P<year>20\d{{2}})\s+(?P<area>{area_pattern})\s+문제",
            row_text,
        )
        if not match:
            continue
        year = int(match.group("year"))
        if year not in years:
            continue
        area_label = _normalize(match.group("area"))
        area = AREA_LABEL_TO_KEY[area_label]
        files = re.findall(
            r"fn_fileDown\('([^']+)'\).*?title='([^']+)'",
            row,
            flags=re.IGNORECASE | re.DOTALL,
        )
        file_seq, file_name = _problem_file(
            files,
            archive=any(spec.archive_member for spec in SPECS_BY_AREA[area]),
        )
        board_seq = match.group("seq")
        assets.append(
            KiceAreaAsset(
                year=year,
                area=area,
                area_label=area_label,
                board_seq=board_seq,
                source_page_url=(
                    "https://www.suneung.re.kr/boardCnts/view.do"
                    f"?boardID=1500234&boardSeq={board_seq}&lev=0&m=0403&s=suneung"
                ),
                file_seq=file_seq,
                file_name=file_name,
            )
        )
    return assets


def discover_kice_area_assets(years: set[int]) -> list[KiceAreaAsset]:
    wanted = {(year, area) for year in years for area in SPECS_BY_AREA}
    found: dict[tuple[int, str], KiceAreaAsset] = {}
    for page_number in range(1, 11):
        separator = "&" if "?" in KICE_LIST_URL else "?"
        page_url = f"{KICE_LIST_URL}{separator}page={page_number}"
        page_html = _decode_html(_request(page_url))
        for asset in extract_kice_area_assets(page_html, years=years):
            key = (asset.year, asset.area)
            existing = found.get(key)
            if existing and existing != asset:
                raise CorpusBootstrapError(f"conflicting KICE area row for {key}")
            found[key] = asset
        if wanted <= found.keys():
            break
    missing = sorted(wanted - found.keys())
    if missing:
        raise CorpusBootstrapError(f"KICE list is missing CSAT areas: {missing}")
    return [found[key] for key in sorted(found)]


def _pdf_info(payload: bytes, spec: SubjectSpec) -> tuple[int, list[list[int]]]:
    if not payload.startswith(b"%PDF"):
        raise CorpusBootstrapError(f"{spec.subject} payload is not a PDF")
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - release environment includes PyMuPDF.
        raise CorpusBootstrapError("PyMuPDF is required to inspect exam PDFs") from exc
    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
        page_count = document.page_count
        text = "\n".join(page.get_text("text") for page in document)
    except (RuntimeError, ValueError) as exc:
        raise CorpusBootstrapError(f"{spec.subject} PDF cannot be opened: {exc}") from exc
    if page_count <= 0 or page_count % spec.form_page_count:
        raise CorpusBootstrapError(
            f"{spec.subject} has {page_count} pages; expected a positive multiple "
            f"of {spec.form_page_count}"
        )
    passages = (
        extract_printed_ranges(text, question_max=spec.expected_question_max)
        if spec.subject == "korean"
        else []
    )
    return page_count, passages


def expected_questions(spec: SubjectSpec, *, page_count: int) -> tuple[list[int], int]:
    if spec.subject in {"korean", "math", "english"}:
        asset = ExamAsset(
            case_id=f"kice-{spec.subject}",
            provider="kice",
            source_page_url="",
            download_url="",
            subject=spec.subject,
            level="csat",
            year=0,
            month=11,
            expected_question_max=spec.expected_question_max,
        )
        return expected_question_numbers(asset, page_count=page_count)
    if page_count <= 0 or page_count % spec.form_page_count:
        raise CorpusBootstrapError(
            f"{spec.subject} has unexpected page count {page_count}"
        )
    form_count = page_count // spec.form_page_count
    return list(range(1, spec.expected_question_max + 1)) * form_count, form_count


def _archive_subject_payloads(
    payload: bytes,
    specs: Iterable[SubjectSpec],
) -> dict[str, tuple[str, bytes]]:
    wanted = {_normalize(spec.display_name): spec for spec in specs}
    found: dict[str, tuple[str, bytes]] = {}
    try:
        with ZipFile(BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                    continue
                name = _normalize(Path(info.filename).name)
                stem = re.sub(r"^\d+[\s_.-]*", "", Path(name).stem)
                stem = re.sub(r"[\s_]*문제(?:지)?$", "", stem).strip()
                spec = wanted.get(_normalize(stem))
                if spec is None:
                    continue
                if spec.subject in found:
                    raise CorpusBootstrapError(
                        f"archive contains duplicate PDF for {spec.subject}"
                    )
                found[spec.subject] = (name, archive.read(info))
    except BadZipFile as exc:
        raise CorpusBootstrapError("KICE problem archive is not a valid ZIP") from exc
    missing = sorted(spec.subject for spec in specs if spec.subject not in found)
    if missing:
        raise CorpusBootstrapError(f"KICE problem archive is missing subjects: {missing}")
    return found


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_pdf(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = _sha256(payload)
    if destination.is_file() and _sha256(destination.read_bytes()) == digest:
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(destination)


def _case_record(
    *,
    area_asset: KiceAreaAsset,
    spec: SubjectSpec,
    payload: bytes,
    local_path: Path,
    archive_member_name: str | None,
    container_sha256: str,
) -> dict[str, Any]:
    page_count, passage_ranges = _pdf_info(payload, spec)
    question_numbers, form_count = expected_questions(spec, page_count=page_count)
    record = {
        "case_id": f"kice-{area_asset.year}-{spec.subject}",
        "provider": "kice",
        "source_page_url": area_asset.source_page_url,
        "download_url": area_asset.download_url,
        "subject": spec.subject,
        "subject_display_name": spec.display_name,
        "subject_area": spec.area,
        "subject_area_display_name": spec.area_label,
        "pipeline_subject": spec.pipeline_subject,
        "level": "csat",
        "year": area_asset.year,
        "month": 11,
        "local_path": str(local_path.resolve()),
        "sha256": _sha256(payload),
        "container_sha256": container_sha256,
        "container_file_name": area_asset.file_name,
        "archive_member_name": archive_member_name,
        "size_bytes": len(payload),
        "page_count": page_count,
        "official_form_count": form_count,
        "processing_ms_max": 10_000 + page_count * 2_000,
        "whole_processing_ms_max": 15_000 + page_count * 1_000,
        "resolution_gate": {
            "source_page_min_width": 1400,
            "source_page_min_height": 1900,
            "problem_crop_min_width": 320,
            "problem_crop_min_height": 64,
            # Tight whitespace trimming legitimately reduces a crop below
            # its source bbox while preserving a high-resolution 780px+
            # question image. Eighty percent still catches real downscaling.
            "problem_bbox_scale_min": 0.80,
        },
        "expected": {
            "question_numbers": question_numbers,
            "passage_ranges": passage_ranges,
            "source_page_count": page_count,
            "whole_page_count": page_count,
        },
        "tags": [
            "pdf",
            "real-exam",
            "windows",
            "kice",
            "csat",
            "all-subjects",
            spec.area,
            spec.subject,
        ],
    }
    return record


def download_cases(
    assets: Iterable[KiceAreaAsset],
    *,
    output_root: Path,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    sources = output_root / "sources"
    for asset in assets:
        specs = SPECS_BY_AREA[asset.area]
        container_payload = _request(asset.download_url, timeout=120.0)
        container_sha256 = _sha256(container_payload)
        if any(spec.archive_member for spec in specs):
            member_payloads = _archive_subject_payloads(container_payload, specs)
            for spec in specs:
                member_name, payload = member_payloads[spec.subject]
                destination = sources / f"kice-{asset.year}-{spec.subject}.pdf"
                _write_pdf(destination, payload)
                cases.append(
                    _case_record(
                        area_asset=asset,
                        spec=spec,
                        payload=payload,
                        local_path=destination,
                        archive_member_name=member_name,
                        container_sha256=container_sha256,
                    )
                )
        else:
            if len(specs) != 1:
                raise CorpusBootstrapError(f"direct area {asset.area} has multiple subject specs")
            spec = specs[0]
            destination = sources / f"kice-{asset.year}-{spec.subject}.pdf"
            _write_pdf(destination, container_payload)
            cases.append(
                _case_record(
                    area_asset=asset,
                    spec=spec,
                    payload=container_payload,
                    local_path=destination,
                    archive_member_name=None,
                    container_sha256=container_sha256,
                )
            )
    return sorted(cases, key=lambda case: str(case["case_id"]))


def build_catalog(
    *,
    output_root: Path,
    cases: list[dict[str, Any]],
    years: set[int],
) -> Path:
    expected_subjects = {spec.subject for spec in CSAT_SUBJECT_SPECS}
    for year in years:
        actual = {str(case["subject"]) for case in cases if int(case["year"]) == year}
        if actual != expected_subjects:
            raise CorpusBootstrapError(
                f"{year} subject coverage mismatch: "
                f"missing={sorted(expected_subjects - actual)}, "
                f"extra={sorted(actual - expected_subjects)}"
            )
    catalog = {
        "schema_version": 2,
        "corpus_id": "windows-csat-all-subjects-v2",
        "description": (
            "Private Windows corpus covering every KICE CSAT subject. "
            "Copyrighted PDFs must not be committed, redistributed, or published."
        ),
        "platform": "windows",
        "years": sorted(years),
        "subject_count_per_year": len(expected_subjects),
        "required_subjects": sorted(expected_subjects),
        "score_gate": {"average_min": 93.0, "case_min": 89.0},
        "dimension_gate": {
            "input_page_recall_min": 1.0,
            "whole_page_recall_min": 1.0,
            "source_resolution_valid_rate_min": 1.0,
            "problem_resolution_valid_rate_min": 1.0,
            "whole_resolution_valid_rate_min": 1.0,
            "completion_rate_min": 1.0,
            "within_time_budget_required": True,
        },
        "cases": cases,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    catalog_path = output_root / "catalog.json"
    temporary = output_root / "catalog.json.part"
    temporary.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(catalog_path)
    return catalog_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a private Windows corpus covering every current CSAT subject."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--kice-year", type=int, action="append", default=[])
    args = parser.parse_args(argv)
    years = set(args.kice_year or [2025, 2026])
    if not years or any(year < 2005 or year > 2100 for year in years):
        parser.error("--kice-year must be a plausible CSAT year")
    try:
        assets = discover_kice_area_assets(years)
        cases = download_cases(assets, output_root=args.output_root.resolve())
        expected_count = len(years) * len(CSAT_SUBJECT_SPECS)
        if len(cases) != expected_count:
            raise CorpusBootstrapError(
                f"expected {expected_count} subject PDFs, built {len(cases)}"
            )
        catalog_path = build_catalog(
            output_root=args.output_root.resolve(),
            cases=cases,
            years=years,
        )
    except (CorpusBootstrapError, OSError, ValueError) as exc:
        print(f"[windows-csat-corpus] ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"[windows-csat-corpus] OK: {len(cases)} private PDFs, "
        f"{len(CSAT_SUBJECT_SPECS)} subjects/year -> {catalog_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
