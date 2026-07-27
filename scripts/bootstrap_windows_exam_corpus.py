#!/usr/bin/env python3
"""Download a private Windows-only corpus of Korean public exam PDFs.

The downloaded PDFs are copyrighted source material.  This script stores them
only in an explicitly selected local directory and writes a private catalog
beside them.  Neither the PDFs nor the generated catalog belong in Git.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from passage_detection import extract_shared_passage_range


KICE_LIST_URL = (
    "https://www.suneung.re.kr/boardCnts/list.do"
    "?boardID=1500234&m=0403&s=suneung"
)
KICE_DOWNLOAD_URL = "https://www.suneung.re.kr/boardCnts/fileDown.do?fileSeq={file_seq}"
HORAENG_ORIGIN = "https://horaeng.com"
USER_AGENT = "ClassInEDBMVP-Windows-Quality-Corpus/1.0"
SUBJECT_LABELS = {
    "korean": "국어",
    "math": "수학",
    "english": "영어",
}
EXPECTED_QUESTION_MAX = {
    "korean": 45,
    "math": 30,
    "english": 45,
}
HORAENG_POST_RE = re.compile(
    r"(?P<year>20\d{2})년\s+(?P<month>\d{1,2})월\s+"
    r"고(?P<grade>[12])\s+모의고사\s+문제"
)


class CorpusBootstrapError(RuntimeError):
    pass


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._anchors: list[tuple[str, list[str]]] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        self._anchors.append((href, []))

    def handle_data(self, data: str) -> None:
        for _href, parts in self._anchors:
            parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._anchors:
            return
        href, parts = self._anchors.pop()
        text = unicodedata.normalize("NFC", " ".join("".join(parts).split()))
        self.links.append((href, text))


@dataclass(frozen=True)
class ExamAsset:
    case_id: str
    provider: str
    source_page_url: str
    download_url: str
    subject: str
    level: str
    year: int
    month: int | None
    expected_question_max: int


def _request(url: str, *, timeout: float = 60.0) -> bytes:
    request_url = quote(url, safe=":/?&=%#+")
    request = Request(
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        raise CorpusBootstrapError(f"empty response from {url}")
    return payload


def _decode_html(payload: bytes) -> str:
    for encoding in ("utf-8", "cp949"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _anchor_links(page_html: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(page_html)
    return parser.links


def _horaeng_category_url(grade: int, year: int) -> str:
    category = quote(f"고{grade} 모의고사")
    year_slug = quote(f"{year}년-고{grade}")
    return f"{HORAENG_ORIGIN}/category/{category}/{year_slug}"


def extract_horaeng_post_links(
    page_html: str,
    *,
    grade: int,
    year: int,
    months: set[int],
) -> dict[int, str]:
    posts: dict[int, str] = {}
    for href, text in _anchor_links(page_html):
        match = HORAENG_POST_RE.search(text)
        if not match:
            continue
        if int(match.group("grade")) != grade or int(match.group("year")) != year:
            continue
        month = int(match.group("month"))
        if month not in months:
            continue
        absolute = urljoin(HORAENG_ORIGIN, href)
        existing = posts.get(month)
        if existing and existing != absolute:
            raise CorpusBootstrapError(
                f"multiple Horaeng posts found for {year} grade {grade} month {month}"
            )
        posts[month] = absolute
    missing = sorted(months - posts.keys())
    if missing:
        raise CorpusBootstrapError(
            f"Horaeng category is missing {year} grade {grade} months {missing}"
        )
    return posts


def extract_horaeng_problem_pdfs(
    page_html: str,
    *,
    subjects: Iterable[str],
) -> dict[str, str]:
    requested = set(subjects)
    found: dict[str, str] = {}
    for href, text in _anchor_links(page_html):
        normalized_href = urljoin(HORAENG_ORIGIN, href)
        path = urlparse(normalized_href).path.lower()
        if not path.endswith(".pdf"):
            continue
        normalized_text = unicodedata.normalize("NFC", text)
        if "문제" not in normalized_text or any(
            excluded in normalized_text for excluded in ("정답", "해설", "대본")
        ):
            continue
        for subject, label in SUBJECT_LABELS.items():
            if subject in requested and label in normalized_text:
                existing = found.get(subject)
                if existing and existing != normalized_href:
                    raise CorpusBootstrapError(
                        f"multiple problem PDFs found for subject {subject}"
                    )
                found[subject] = normalized_href
    missing = sorted(requested - found.keys())
    if missing:
        raise CorpusBootstrapError(f"Horaeng post is missing problem PDFs for {missing}")
    return found


def _strip_tags(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def extract_kice_assets(
    page_html: str,
    *,
    years: set[int],
    subjects: Iterable[str],
) -> list[ExamAsset]:
    requested_subjects = set(subjects)
    assets: list[ExamAsset] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, flags=re.IGNORECASE | re.DOTALL)
    for row in rows:
        row_text = _strip_tags(row)
        row_match = re.search(
            r"\b(?P<seq>\d{7})\s+(?P<year>20\d{2})\s+"
            r"(?P<subject>국어|수학|영어)\s+문제",
            row_text,
        )
        if not row_match:
            continue
        year = int(row_match.group("year"))
        if year not in years:
            continue
        subject_label = row_match.group("subject")
        subject = next(
            key for key, value in SUBJECT_LABELS.items() if value == subject_label
        )
        if subject not in requested_subjects:
            continue
        files = re.findall(
            r"fn_fileDown\('([^']+)'\).*?title='([^']+)'",
            row,
            flags=re.IGNORECASE | re.DOTALL,
        )
        problem_files = [
            (file_seq, unicodedata.normalize("NFC", title))
            for file_seq, title in files
            if title.lower().endswith(".pdf")
            and "문제지" in title
            and "짝수형" not in title
        ]
        if not problem_files:
            raise CorpusBootstrapError(
                f"KICE row {row_match.group('seq')} has no usable problem PDF"
            )
        preferred = next(
            (
                item
                for item in problem_files
                if "홀수형" in item[1] or ("홀수형" not in item[1] and len(problem_files) == 1)
            ),
            problem_files[0],
        )
        file_seq, _title = preferred
        assets.append(
            ExamAsset(
                case_id=f"kice-{year}-{subject}",
                provider="kice",
                source_page_url=(
                    "https://www.suneung.re.kr/boardCnts/view.do"
                    f"?boardID=1500234&boardSeq={row_match.group('seq')}"
                    "&lev=0&m=0403&s=suneung"
                ),
                download_url=KICE_DOWNLOAD_URL.format(file_seq=file_seq),
                subject=subject,
                level="csat",
                year=year,
                month=11,
                expected_question_max=EXPECTED_QUESTION_MAX[subject],
            )
        )
    return assets


def discover_assets(
    *,
    horaeng_year: int,
    grades: set[int],
    months: set[int],
    kice_years: set[int],
    subjects: set[str],
) -> list[ExamAsset]:
    assets: list[ExamAsset] = []
    for grade in sorted(grades):
        category_url = _horaeng_category_url(grade, horaeng_year)
        category_html = _decode_html(_request(category_url))
        posts = extract_horaeng_post_links(
            category_html,
            grade=grade,
            year=horaeng_year,
            months=months,
        )
        for month, post_url in sorted(posts.items()):
            post_html = _decode_html(_request(post_url))
            pdfs = extract_horaeng_problem_pdfs(post_html, subjects=subjects)
            for subject, download_url in sorted(pdfs.items()):
                assets.append(
                    ExamAsset(
                        case_id=f"horaeng-{horaeng_year}-g{grade}-m{month:02d}-{subject}",
                        provider="horaeng",
                        source_page_url=post_url,
                        download_url=download_url,
                        subject=subject,
                        level=f"high{grade}",
                        year=horaeng_year,
                        month=month,
                        expected_question_max=EXPECTED_QUESTION_MAX[subject],
                    )
                )

    remaining_years = set(kice_years)
    page_number = 1
    while remaining_years and page_number <= 10:
        separator = "&" if "?" in KICE_LIST_URL else "?"
        page_url = f"{KICE_LIST_URL}{separator}page={page_number}"
        page_html = _decode_html(_request(page_url))
        page_assets = extract_kice_assets(
            page_html,
            years=remaining_years,
            subjects=subjects,
        )
        assets.extend(page_assets)
        seen_by_year: dict[int, set[str]] = {}
        for asset in assets:
            if asset.provider == "kice":
                seen_by_year.setdefault(asset.year, set()).add(asset.subject)
        remaining_years = {
            year for year in remaining_years if seen_by_year.get(year) != subjects
        }
        page_number += 1
    if remaining_years:
        raise CorpusBootstrapError(
            f"KICE pages did not contain every requested subject for years {sorted(remaining_years)}"
        )

    unique: dict[str, ExamAsset] = {}
    for asset in assets:
        if asset.case_id in unique and unique[asset.case_id] != asset:
            raise CorpusBootstrapError(f"conflicting duplicate case id {asset.case_id}")
        unique[asset.case_id] = asset
    return [unique[case_id] for case_id in sorted(unique)]


def extract_printed_ranges(text: str, *, question_max: int) -> list[list[int]]:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    ranges: set[tuple[int, int]] = set()
    for index in range(len(lines)):
        for line_count in range(1, 4):
            joined = " ".join(lines[index : index + line_count])
            passage_range = extract_shared_passage_range(joined)
            if passage_range is None:
                continue
            start, end = passage_range
            if 1 <= start < end <= question_max:
                ranges.add((start, end))
            break
    return [[start, end] for start, end in sorted(ranges)]


def _inspect_pdf(payload: bytes, *, subject: str) -> tuple[int, list[list[int]]]:
    if not payload.startswith(b"%PDF"):
        raise CorpusBootstrapError("downloaded payload is not a PDF")
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - release environment includes PyMuPDF.
        raise CorpusBootstrapError("PyMuPDF is required to inspect exam PDFs") from exc
    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
        page_count = document.page_count
        if page_count <= 0:
            raise CorpusBootstrapError("downloaded PDF contains no pages")
        full_text = "\n".join(page.get_text("text") for page in document)
    except (RuntimeError, ValueError) as exc:
        raise CorpusBootstrapError(f"downloaded PDF cannot be opened: {exc}") from exc
    passage_ranges = (
        extract_printed_ranges(
            full_text,
            question_max=EXPECTED_QUESTION_MAX[subject],
        )
        if subject in {"korean", "english"}
        else []
    )
    return page_count, passage_ranges


def _download_asset(asset: ExamAsset, output_root: Path) -> dict[str, Any]:
    sources_dir = output_root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    destination = sources_dir / f"{asset.case_id}.pdf"
    payload = _request(asset.download_url, timeout=120.0)
    page_count, passage_ranges = _inspect_pdf(payload, subject=asset.subject)
    digest = hashlib.sha256(payload).hexdigest()
    if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        temporary = destination.with_suffix(".pdf.part")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    record = asdict(asset)
    record.update(
        {
            "local_path": str(destination.resolve()),
            "sha256": digest,
            "size_bytes": len(payload),
            "page_count": page_count,
            "expected": {
                "question_numbers": list(range(1, asset.expected_question_max + 1)),
                "passage_ranges": passage_ranges,
            },
            "tags": [
                "pdf",
                "real-exam",
                "windows",
                asset.provider,
                asset.level,
                asset.subject,
            ],
        }
    )
    return record


def _write_catalog(output_root: Path, cases: list[dict[str, Any]]) -> Path:
    catalog = {
        "schema_version": 1,
        "corpus_id": "windows-real-exams-v1",
        "description": (
            "Private Windows recognition corpus. Copyrighted PDFs must not be "
            "committed, redistributed, or published."
        ),
        "platform": "windows",
        "score_gate": {"average_min": 90.0, "case_min": 87.0},
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
        description="Build a private Windows corpus from KICE and Horaeng exam PDFs."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--horaeng-year", type=int, default=2025)
    parser.add_argument("--grade", type=int, action="append", default=[])
    parser.add_argument("--month", type=int, action="append", default=[])
    parser.add_argument("--kice-year", type=int, action="append", default=[])
    parser.add_argument(
        "--subject",
        choices=sorted(SUBJECT_LABELS),
        action="append",
        default=[],
    )
    args = parser.parse_args(argv)
    grades = set(args.grade or [1, 2])
    months = set(args.month or [3, 6, 9, 10])
    kice_years = set(args.kice_year or [2025, 2026])
    subjects = set(args.subject or SUBJECT_LABELS)
    if not grades <= {1, 2}:
        parser.error("--grade must be 1 or 2")
    if not months or any(month < 1 or month > 12 for month in months):
        parser.error("--month must be between 1 and 12")
    try:
        assets = discover_assets(
            horaeng_year=args.horaeng_year,
            grades=grades,
            months=months,
            kice_years=kice_years,
            subjects=subjects,
        )
        expected_count = len(grades) * len(months) * len(subjects) + len(
            kice_years
        ) * len(subjects)
        if len(assets) != expected_count:
            raise CorpusBootstrapError(
                f"expected {expected_count} unique PDFs, discovered {len(assets)}"
            )
        cases: list[dict[str, Any]] = []
        for index, asset in enumerate(assets, start=1):
            print(f"[windows-corpus] {index}/{len(assets)} {asset.case_id}")
            cases.append(_download_asset(asset, args.output_root.resolve()))
        catalog_path = _write_catalog(args.output_root.resolve(), cases)
    except (CorpusBootstrapError, OSError, ValueError) as exc:
        print(f"[windows-corpus] ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"[windows-corpus] OK: {len(cases)} private PDFs -> {catalog_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
