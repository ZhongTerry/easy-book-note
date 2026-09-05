import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .chapter_numbers import is_chapter_title, normalize_chapter_title, parse_chapter_number
from .models import Evidence, PageType, RecognitionResult


_BLOCKED_PATTERNS = (
    '验证码', '安全验证', '访问过于频繁', '访问异常', '请完成验证',
    'checking your browser', 'captcha', 'cloudflare',
)
_CHALLENGE_TEXT_MARKERS = (
    'just a moment', 'enable javascript and cookies to continue',
    'verify you are human', 'performing security verification',
    'attention required', 'ddos protection',
)
_CHALLENGE_HTML_MARKERS = (
    'cf-chl-', 'challenge-platform', 'turnstile', 'hcaptcha', 'g-recaptcha',
)
_NOISE_PATTERNS = (
    '一秒记住', '最新网址', '最新章节', '加入书签', '加入收藏', '投推荐票',
    '手机阅读', 'txt下载', '广告', '备用网址', '上一章', '下一章',
)
_STRONG_NOISE_PATTERNS = (
    '一秒记住', '最新网址', '请收藏本站', '加入书签', '加入收藏', '投推荐票',
    '手机阅读', 'txt下载', '备用网址', '本章未完', '本站首发',
)
_PREVIOUS_LABELS = ('上一章', '上章', 'previous chapter', 'prev')
_NEXT_LABELS = ('下一章', '下章', 'next chapter', 'next')
_TOC_LABELS = ('目录', '章节目录', '返回目录', 'contents', 'toc')
_PAGINATION_LABEL = re.compile(
    r'(?:下一?页|下页|继续阅读|next\s*page|page\s*next|\bnext\b|\d+\s*/\s*\d+)',
    re.I,
)
_VOLUME_TITLE = re.compile(
    r'^(?:第\s*[\d零〇一二两三四五六七八九十百千万]+\s*卷|卷\s*\d+|volume\s*\d+)',
    re.I,
)
_CONTENT_HINT = re.compile(r'(?:content|chapter|article|read|text|showtxt|booktext|nr1)', re.I)
_NAV_HINT = re.compile(r'(?:nav|menu|header|footer|recommend|hot)', re.I)
_CONTENT_NOISE_HINT = re.compile(
    r'(?:advert|advertisement|recommend|related|share|copyright|footer|(?:^|[-_\s])ad(?:[-_\s]|$))',
    re.I,
)


class RecognitionEngine:
    """Deterministic, explainable recognition for crawler payloads and HTML."""

    def analyze_html(self, html: str | bytes, base_url: str) -> RecognitionResult:
        soup = BeautifulSoup(html or '', 'html.parser')
        page_text = soup.get_text(' ', strip=True)
        blocked = self._is_blocked(page_text, str(soup))
        if blocked:
            return RecognitionResult(
                page_type=PageType.BLOCKED,
                confidence=0.99,
                warnings=['source_blocked_or_challenged'],
                evidence=[Evidence('blocked_marker', 0.99, blocked)],
            )

        title = self._extract_title(soup)
        chapters, toc_evidence = self._extract_chapters(soup, base_url)
        content, content_evidence = self._extract_content(soup)
        prev_url, next_url, next_page_url, toc_url, nav_evidence = self._extract_navigation(soup, base_url)

        toc_score = sum(item.score for item in toc_evidence)
        content_score = sum(item.score for item in content_evidence)
        evidence = toc_evidence + content_evidence + nav_evidence
        if len(chapters) >= 3 and toc_score >= content_score - 0.1:
            return RecognitionResult(
                page_type=PageType.TOC,
                confidence=min(0.99, 0.45 + toc_score / 2),
                title=title,
                chapters=chapters,
                evidence=evidence,
            )
        if content:
            return RecognitionResult(
                page_type=PageType.CHAPTER,
                confidence=min(0.99, 0.35 + content_score / 2),
                title=title,
                content=content,
                prev_url=prev_url,
                next_url=next_url,
                next_page_url=next_page_url,
                toc_url=toc_url,
                evidence=evidence,
            )
        return RecognitionResult(
            title=title,
            prev_url=prev_url,
            next_url=next_url,
            next_page_url=next_page_url,
            toc_url=toc_url,
            warnings=['no_reliable_content_or_toc'],
            evidence=evidence,
        )

    def normalize_payload(
        self,
        payload: dict[str, Any] | None,
        url: str = '',
        declared_type: PageType | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        result = dict(payload)
        title = str(result.get('title') or result.get('chapter_title') or '').strip()
        raw_content = result.get('content')
        content = self._normalize_content(raw_content)
        chapters = self._normalize_chapters(result.get('chapters'), url)

        page_type = declared_type or self._classify_payload(result, content, chapters)
        confidence = self._payload_confidence(page_type, content, chapters, result)
        existing_recognition = result.get('recognition')
        existing_recognition = existing_recognition if isinstance(existing_recognition, dict) else {}
        warnings = existing_recognition.get('warnings')
        warnings = [str(item) for item in warnings] if isinstance(warnings, list) else []
        result['title'] = title
        if content:
            result['content'] = content
        if chapters:
            result['chapters'] = chapters
        result['page_type'] = page_type.value
        result['recognition_confidence'] = confidence
        result['recognition'] = {
            'page_type': page_type.value,
            'confidence': confidence,
            'warnings': warnings,
            'evidence': self._payload_evidence(content, chapters, result),
        }
        return result

    def _classify_payload(
        self,
        payload: dict[str, Any],
        content: list[str],
        chapters: list[dict[str, Any]],
    ) -> PageType:
        declared = payload.get('page_type')
        if declared in {page_type.value for page_type in PageType}:
            return PageType(declared)
        if len(chapters) >= 3 and len(''.join(content)) < 500:
            return PageType.TOC
        if content:
            return PageType.CHAPTER
        return PageType.UNKNOWN

    def _payload_confidence(
        self,
        page_type: PageType,
        content: list[str],
        chapters: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> float:
        if page_type is PageType.BLOCKED:
            return 0.99
        if page_type is PageType.TOC:
            return round(min(0.98, 0.45 + len(chapters) * 0.06), 3)
        if page_type is PageType.CHAPTER:
            nav_bonus = 0.1 if any(payload.get(key) for key in ('next', 'next_url', 'prev', 'prev_url')) else 0
            return round(min(0.98, 0.5 + min(len(''.join(content)), 4000) / 10000 + nav_bonus), 3)
        return 0.1

    def _payload_evidence(
        self,
        content: list[str],
        chapters: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        evidence = []
        if content:
            evidence.append({'code': 'normalized_content', 'score': min(1.0, len(''.join(content)) / 2000)})
        if chapters:
            evidence.append({'code': 'normalized_chapters', 'score': min(1.0, len(chapters) / 10)})
        if any(payload.get(key) for key in ('next', 'next_url', 'prev', 'prev_url')):
            evidence.append({'code': 'navigation_present', 'score': 0.2})
        return evidence

    def _extract_title(self, soup: BeautifulSoup) -> str:
        candidates: list[tuple[int, str]] = []
        for tag in soup.select('h1, h2, h3, [class*=chapter-title], [class*=chapter_title], [class~=title]'):
            text = normalize_chapter_title(tag.get_text(' ', strip=True))
            if not 2 <= len(text) <= 200:
                continue
            descriptor = ' '.join(tag.get('class', [])) + ' ' + (tag.get('id') or '')
            score = 1
            if re.search(r'(?:chapter|title|标题|章节)', descriptor, re.I):
                score += 3
            if is_chapter_title(text):
                score += 4
            candidates.append((score, text))
        if soup.title:
            text = normalize_chapter_title(soup.title.get_text(' ', strip=True).split('_')[0])
            if 2 <= len(text) <= 200:
                candidates.append((5 if is_chapter_title(text) else 2, text))
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
        return ''

    def _extract_content(self, soup: BeautifulSoup) -> tuple[list[str], list[Evidence]]:
        candidates: list[tuple[float, Tag]] = []
        for tag in self._content_candidates(soup):
            text = tag.get_text(' ', strip=True)
            if len(text) < 20:
                continue
            links = len(tag.find_all('a'))
            link_text_length = sum(len(anchor.get_text(' ', strip=True)) for anchor in tag.find_all('a'))
            link_density = link_text_length / max(len(text), 1)
            if link_density > 0.3:
                continue
            paragraph_count = len(tag.find_all('p'))
            score = min(len(text) / 1200, 0.65) + min(paragraph_count / 4, 0.25)
            if _CONTENT_HINT.search(' '.join(tag.get('class', [])) + ' ' + (tag.get('id') or '')):
                score += 0.2
            candidates.append((score, tag))
        if not candidates:
            return [], []
        score, best = max(candidates, key=lambda item: item[0])
        raw_lines = self._lines_from_tag(best)
        lines = self._normalize_content(raw_lines)
        if len(''.join(lines)) < 20:
            return [], []
        evidence = [Evidence('content_container', min(score, 1.0), best.name or 'unknown')]
        removed_noise = sum(1 for line in raw_lines if self._is_noise(str(line)))
        if removed_noise:
            evidence.append(Evidence('content_noise_removed', min(0.2, removed_noise * 0.04), str(removed_noise)))
        return lines, evidence

    def _content_candidates(self, soup: BeautifulSoup) -> Iterable[Tag]:
        seen: set[int] = set()
        for tag in soup.select('[id], [class], article, main'):
            if not isinstance(tag, Tag) or id(tag) in seen:
                continue
            seen.add(id(tag))
            descriptor = ' '.join(tag.get('class', [])) + ' ' + (tag.get('id') or '')
            if _NAV_HINT.search(descriptor):
                continue
            yield tag
        if soup.body:
            yield soup.body

    def _lines_from_tag(self, tag: Tag) -> list[str]:
        paragraphs = []
        for node in tag.find_all('p'):
            descriptors = []
            for parent in [node, *node.parents]:
                if not isinstance(parent, Tag):
                    continue
                descriptors.append(' '.join(parent.get('class', [])) + ' ' + (parent.get('id') or ''))
                if parent is tag:
                    break
            if any(_CONTENT_NOISE_HINT.search(descriptor) for descriptor in descriptors):
                continue
            paragraphs.append(node.get_text(' ', strip=True))
        if len(paragraphs) >= 2:
            return paragraphs
        return tag.get_text('\n', strip=True).splitlines()

    def _normalize_content(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_lines = value.splitlines()
        elif isinstance(value, list):
            raw_lines = value
        else:
            return []
        lines = []
        for raw_line in raw_lines:
            line = ' '.join(str(raw_line).replace('\xa0', ' ').split())
            if len(line) < 2 or self._is_noise(line):
                continue
            if not lines or line != lines[-1]:
                lines.append(line)
        return lines

    def _extract_chapters(
        self,
        soup: BeautifulSoup,
        base_url: str,
    ) -> tuple[list[dict[str, Any]], list[Evidence]]:
        best: list[dict[str, Any]] = []
        best_ratio = 0.0
        for container in soup.find_all(['dl', 'ol', 'ul', 'tbody', 'section', 'div']):
            descriptor = ' '.join(container.get('class', [])) + ' ' + (container.get('id') or '')
            if _NAV_HINT.search(descriptor):
                continue
            anchors = container.find_all('a', href=True)
            if len(anchors) < 3:
                continue
            chapters = self._chapter_links(anchors, base_url)
            ratio = len(chapters) / len(anchors)
            if len(chapters) > len(best) or (len(chapters) == len(best) and ratio > best_ratio):
                best, best_ratio = chapters, ratio
        if len(best) < 3:
            return [], []
        return best, [
            Evidence('chapter_link_cluster', min(0.65, len(best) / 12), str(len(best))),
            Evidence('chapter_link_ratio', min(0.35, best_ratio * 0.35), f'{best_ratio:.2f}'),
        ]

    def _chapter_links(self, anchors: Iterable[Tag], base_url: str) -> list[dict[str, Any]]:
        chapters = []
        seen_urls = set()
        for index, anchor in enumerate(anchors):
            title = normalize_chapter_title(anchor.get_text(' ', strip=True))
            href = anchor.get('href')
            if not href or not title or not is_chapter_title(title) or self._is_noise(title):
                continue
            full_url = urljoin(base_url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            chapter_id = parse_chapter_number(title)
            chapter = {
                'id': chapter_id if chapter_id is not None else -1,
                'title': title,
                'name': title,
                'url': full_url,
                'origin_idx': index,
            }
            volume = self._volume_for_anchor(anchor)
            if volume:
                chapter['volume'] = volume
            chapters.append(chapter)
        return chapters

    def _volume_for_anchor(self, anchor: Tag) -> str:
        """Find the closest preceding volume heading without coupling to a site layout."""
        for heading in anchor.find_all_previous(['h1', 'h2', 'h3', 'h4', 'dt', 'strong', 'b']):
            text = normalize_chapter_title(heading.get_text(' ', strip=True))
            if _VOLUME_TITLE.match(text):
                return text
        return ''

    def _normalize_chapters(self, raw_chapters: Any, base_url: str) -> list[dict[str, Any]]:
        if not isinstance(raw_chapters, list):
            return []
        normalized = []
        seen_urls = set()
        for index, item in enumerate(raw_chapters):
            if not isinstance(item, dict):
                continue
            title = normalize_chapter_title(item.get('title') or item.get('raw_title') or item.get('name'))
            url = item.get('url')
            if not title or not url:
                continue
            url = urljoin(base_url, str(url))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            chapter_id = item.get('id')
            if not isinstance(chapter_id, int) or chapter_id <= 0:
                chapter_id = parse_chapter_number(title) or -1
            chapter = {
                **item,
                'id': chapter_id,
                'title': title,
                'name': item.get('name') or title,
                'url': url,
                'origin_idx': item.get('origin_idx', index),
            }
            volume = ' '.join(str(item.get('volume') or '').split())
            if volume:
                chapter['volume'] = volume
            normalized.append(chapter)
        return normalized

    def _extract_navigation(self, soup: BeautifulSoup, base_url: str) -> tuple[str | None, str | None, str | None, str | None, list[Evidence]]:
        previous = next_url = next_page_url = toc_url = None
        for anchor in soup.find_all('a', href=True):
            label = ' '.join(anchor.get_text(' ', strip=True).lower().split())
            href = urljoin(base_url, anchor['href'])
            if not previous and any(marker in label for marker in _PREVIOUS_LABELS):
                previous = href
            elif not next_page_url and self._is_next_page_link(label, href, base_url):
                next_page_url = href
            elif not next_url and any(marker in label for marker in _NEXT_LABELS):
                next_url = href
            elif not toc_url and any(marker in label for marker in _TOC_LABELS):
                toc_url = href
        evidence = []
        if previous or next_url:
            evidence.append(Evidence('chapter_navigation', 0.18, 'prev_or_next'))
        if next_page_url:
            evidence.append(Evidence('pagination_navigation', 0.16, 'next_page'))
        if toc_url:
            evidence.append(Evidence('toc_navigation', 0.1, 'toc_link'))
        return previous, next_url, next_page_url, toc_url, evidence

    def _is_next_page_link(self, label: str, href: str, base_url: str) -> bool:
        """Recognize chapter continuations without treating arbitrary next links as pages."""
        if not _PAGINATION_LABEL.search(label):
            return False
        if re.search(r'(?:下一?页|下页|next\s*page|page\s*next|继续阅读|\d+\s*/\s*\d+)', label, re.I):
            return True

        current = urlsplit(base_url)
        candidate = urlsplit(href)
        if current.netloc != candidate.netloc or current.path.rsplit('/', 1)[0] != candidate.path.rsplit('/', 1)[0]:
            return False
        current_name = current.path.rsplit('/', 1)[-1]
        candidate_name = candidate.path.rsplit('/', 1)[-1]
        current_stem = re.sub(r'\.[^.]+$', '', current_name)
        candidate_stem = re.sub(r'\.[^.]+$', '', candidate_name)
        return bool(re.match(rf'^{re.escape(current_stem)}(?:[_-](?:page)?\d+)$', candidate_stem, re.I))

    def _is_noise(self, text: str) -> bool:
        lowered = text.lower()
        if len(text) < 240 and any(pattern.lower() in lowered for pattern in _STRONG_NOISE_PATTERNS):
            return True
        return len(text) < 80 and any(pattern.lower() in lowered for pattern in _NOISE_PATTERNS)

    def _is_blocked(self, text: str, html: str = '') -> str | None:
        lowered = text.lower()
        direct_match = next((pattern for pattern in _BLOCKED_PATTERNS if pattern.lower() in lowered), None)
        if direct_match:
            return direct_match

        challenge_text = next((marker for marker in _CHALLENGE_TEXT_MARKERS if marker in lowered), None)
        if not challenge_text:
            return None
        html_lowered = html.lower()
        challenge_html = next((marker for marker in _CHALLENGE_HTML_MARKERS if marker in html_lowered), None)
        # A challenge title plus its browser-verification copy is sufficient;
        # an HTML marker makes the result unambiguous for custom challenge pages.
        if challenge_html or 'just a moment' in lowered or 'attention required' in lowered:
            return challenge_html or challenge_text
        return None
