from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PageType(str, Enum):
    CHAPTER = 'chapter'
    TOC = 'toc'
    BLOCKED = 'blocked'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Evidence:
    code: str
    score: float
    detail: str = ''

    def as_dict(self) -> dict[str, Any]:
        return {'code': self.code, 'score': self.score, 'detail': self.detail}


@dataclass
class RecognitionResult:
    page_type: PageType = PageType.UNKNOWN
    confidence: float = 0.0
    title: str = ''
    content: list[str] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    prev_url: str | None = None
    next_url: str | None = None
    # Kept separate from next_url so a crawler can join a split chapter before
    # advancing to the following chapter.
    next_page_url: str | None = None
    toc_url: str | None = None
    warnings: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            'page_type': self.page_type.value,
            'confidence': round(self.confidence, 3),
            'warnings': self.warnings,
            'evidence': [item.as_dict() for item in self.evidence],
        }

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'title': self.title,
            'page_type': self.page_type.value,
            'recognition': self.metadata(),
            'recognition_confidence': round(self.confidence, 3),
        }
        if self.content:
            payload['content'] = self.content
        if self.chapters:
            payload['chapters'] = self.chapters
        if self.prev_url:
            payload['prev'] = self.prev_url
        if self.next_url:
            payload['next'] = self.next_url
        if self.toc_url:
            payload['toc_url'] = self.toc_url
        return payload
