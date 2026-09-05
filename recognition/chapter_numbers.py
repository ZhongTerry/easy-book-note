import re


_CHINESE_DIGITS = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}
_CHINESE_UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000}
_CHAPTER_PATTERN = re.compile(
    r'(?:第\s*)?(\d+|[零〇一二两三四五六七八九十百千万]+)\s*(?:章|节|回|幕|卷)\b',
    re.IGNORECASE,
)
_ENGLISH_CHAPTER_PATTERN = re.compile(r'\b(?:chapter|ch\.?)[\s._-]*(\d+)\b', re.IGNORECASE)
_LEADING_NUMBER_PATTERN = re.compile(r'^\s*(\d{1,7})(?:\s*[.、:：_-]|\s+)')
_PAGINATION_SUFFIX = re.compile(
    r'\s*(?:[\(（\[]\s*(?:page\s*)?\d+\s*(?:/|of)\s*\d+\s*[\)）\]]|[-\s]+(?:page\s*)?\d+\s*(?:/|of)\s*\d+)\s*$',
    re.IGNORECASE,
)


def chinese_number_to_int(value: str) -> int | None:
    if not value or any(char not in _CHINESE_DIGITS and char not in _CHINESE_UNITS for char in value):
        return None
    if all(char in _CHINESE_DIGITS for char in value):
        return int(''.join(str(_CHINESE_DIGITS[char]) for char in value))

    total = 0
    section = 0
    current = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS[char]
        if unit == 10000:
            section = (section + current) * unit
            total += section
            section = 0
            current = 0
        else:
            section += (current or 1) * unit
            current = 0
    return total + section + current


def parse_chapter_number(title: str | None) -> int | None:
    if not title:
        return None
    text = ' '.join(str(title).split())
    match = _CHAPTER_PATTERN.search(text) or _ENGLISH_CHAPTER_PATTERN.search(text)
    if match:
        value = match.group(1)
        return int(value) if value.isdigit() else chinese_number_to_int(value)
    leading = _LEADING_NUMBER_PATTERN.search(text)
    return int(leading.group(1)) if leading else None


def normalize_chapter_title(title: str | None) -> str:
    text = ' '.join(str(title or '').replace('\xa0', ' ').split())
    text = re.sub(r'^[\[【(（]\s*', '', text).strip()
    return _PAGINATION_SUFFIX.sub('', text).strip()


def is_chapter_title(title: str | None) -> bool:
    text = normalize_chapter_title(title)
    return bool(parse_chapter_number(text) is not None or re.search(r'[章节回幕]', text))
