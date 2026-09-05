import re
from difflib import SequenceMatcher
from typing import Any


_CHAPTER_PREFIX = re.compile(
    r'^(?:第\s*)?[0-9零〇一二两三四五六七八九十百千万]+\s*[章节回幕卷话篇]\s*',
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r'[\s\u3000\-—_·•:：,，。．!！?？~～\[\]【】()（）<>《》"\']+')


def normalize_match_title(value: str | None) -> str:
    text = _CHAPTER_PREFIX.sub('', str(value or ''))
    return _PUNCTUATION.sub('', text).lower()


def find_chapter_match(
    chapters: list[dict[str, Any]],
    target_id: int | None,
    target_title: str | None,
) -> dict[str, Any] | None:
    """Return only a sufficiently reliable cross-source chapter match."""
    target_id = target_id if isinstance(target_id, int) and target_id > 0 else None
    target_name = normalize_match_title(target_title)
    candidates = []

    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict) or not chapter.get('url'):
            continue
        chapter_id = chapter.get('id')
        chapter_name = normalize_match_title(chapter.get('title') or chapter.get('raw_title') or chapter.get('name'))
        similarity = SequenceMatcher(None, target_name, chapter_name).ratio() if target_name and chapter_name else 0.0
        id_matches = target_id is not None and chapter_id == target_id

        if id_matches and (len(target_name) < 2 or similarity >= 0.55):
            score = 0.7 + similarity * 0.3
            strategy = 'id_and_title' if target_name else 'id_only'
        elif not target_id and len(target_name) >= 2 and similarity >= 0.9:
            score = similarity
            strategy = 'title_only'
        elif target_id and len(target_name) >= 3 and similarity >= 0.94:
            # A near-identical title can bridge sources whose numbering differs.
            score = similarity
            strategy = 'high_similarity_title'
        else:
            continue
        candidates.append((score, -index, strategy, chapter, similarity))

    if not candidates:
        return None
    score, _, strategy, chapter, similarity = max(candidates, key=lambda item: (item[0], item[1]))
    return {
        **chapter,
        'match_confidence': round(score, 3),
        'match_similarity': round(similarity, 3),
        'match_strategy': strategy,
    }
