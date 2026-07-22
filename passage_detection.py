from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


_DIGITS = r"[0-9０-９]{1,3}"
_BRACKET_RANGE_RE = re.compile(
    rf"^\s*[\[［（(<](?P<start>{_DIGITS})\s*[~\-〜－]\s*(?P<end>{_DIGITS})\s*(?:번)?[\]］）)>]"
)
_KOREAN_SUFFIX_RANGE_RE = re.compile(
    rf"^\s*(?:제\s*)?(?P<start>{_DIGITS})\s*(?:번\s*)?"
    rf"(?:[~\-〜－]|부터|에서)\s*(?:제\s*)?(?P<end>{_DIGITS})\s*번(?:까지)?"
)
_COMPACT_RANGE_RE = re.compile(
    rf"^\s*(?:(?:문항|문제|questions?)\s*)?"
    rf"(?P<start>{_DIGITS})\s*[~\-\u2010-\u2015]\s*(?P<end>{_DIGITS})\s*(?:번)?",
    re.IGNORECASE,
)

_KOREAN_MATERIAL_NOUNS = (
    r"글|자료|대화|담화|발표|토의|토론|작문|초고|건의문|작품|"
    r"도표|표|그림|실험|보기|지문"
)
_KOREAN_MATERIAL_RE = re.compile(
    rf"(?:다음|아래|위의?|윗)(?:은|는)?\s*.{{0,36}}?(?:{_KOREAN_MATERIAL_NOUNS})|"
    rf"(?:{_KOREAN_MATERIAL_NOUNS})\s*(?:을|를|에|에서|의|이(?:다|고)|이다)",
    re.IGNORECASE,
)
_KOREAN_TASK_RE = re.compile(
    r"(?:읽|보|듣|살펴|참고).{0,28}(?:물음|문항|문제|답|고르)|"
    r"(?:물음|문항|문제).{0,20}(?:답|고르)",
    re.IGNORECASE,
)
_ENGLISH_MATERIAL_RE = re.compile(
    r"(?:following\s+)?(?:passage|text|article|conversation|dialogue|speech|chart|graph)|"
    r"(?:read|refer\s+to|look\s+at|listen\s+to)\s+the\s+following",
    re.IGNORECASE,
)
_ENGLISH_TASK_RE = re.compile(
    r"(?:read|refer\s+to|look\s+at|listen\s+to).{0,48}(?:answer|question)|"
    r"(?:answer).{0,24}(?:question)|"
    r"\bquestions?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PassageRangeHeader:
    start: int
    end: int
    normalized_text: str
    cue_language: str
    confidence: float


def normalize_passage_header_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def shared_passage_cue_language(value: Any) -> str | None:
    text = normalize_passage_header_text(value)
    if not text:
        return None
    if _KOREAN_MATERIAL_RE.search(text) and _KOREAN_TASK_RE.search(text):
        return "ko"
    if _ENGLISH_MATERIAL_RE.search(text) and _ENGLISH_TASK_RE.search(text):
        return "en"
    return None


def parse_passage_range_candidate(
    value: Any,
    *,
    max_span: int = 12,
) -> tuple[int, int, str] | None:
    text = normalize_passage_header_text(value)
    if not text:
        return None
    match = (
        _BRACKET_RANGE_RE.match(text)
        or _KOREAN_SUFFIX_RANGE_RE.match(text)
        or _COMPACT_RANGE_RE.match(text)
    )
    if not match:
        return None
    try:
        start = int(unicodedata.normalize("NFKC", match.group("start")))
        end = int(unicodedata.normalize("NFKC", match.group("end")))
    except (TypeError, ValueError):
        return None
    if start <= 0 or end <= start or end - start > max(1, int(max_span)):
        return None
    return start, end, text


def passage_header_text_looks_corrupted(value: Any) -> bool:
    text = normalize_passage_header_text(value)
    if not text:
        return False
    candidate = parse_passage_range_candidate(text)
    if candidate is None:
        return False
    _start, _end, normalized = candidate
    visible = re.sub(r"[\s\[\]()<>{}0-9~\-.,]", "", normalized)
    if len(visible) < 4:
        return False
    replacement_count = sum(character in {"·", "�", "□", "■"} for character in visible)
    return replacement_count / len(visible) >= 0.35


def parse_shared_passage_range_header(
    value: Any,
    *,
    max_span: int = 12,
) -> PassageRangeHeader | None:
    text = normalize_passage_header_text(value)
    if not text:
        return None
    candidate = parse_passage_range_candidate(text, max_span=max_span)
    if candidate is None:
        return None
    cue_language = shared_passage_cue_language(text)
    if cue_language is None:
        return None
    start, end, _normalized = candidate
    return PassageRangeHeader(
        start=start,
        end=end,
        normalized_text=text,
        cue_language=cue_language,
        confidence=0.98,
    )


def extract_shared_passage_range(value: Any, *, max_span: int = 12) -> tuple[int, int] | None:
    header = parse_shared_passage_range_header(value, max_span=max_span)
    return (header.start, header.end) if header is not None else None
