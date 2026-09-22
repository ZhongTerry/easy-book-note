"""Novel-oriented search adapter backed exclusively by a SearXNG instance."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from pypinyin import Style, lazy_pinyin

from config import (
    SEARXNG_CACHE_TTL, SEARXNG_TIMEOUT, SEARXNG_URL,
    SEARXNG_VERIFY_LIMIT, SEARXNG_VERIFY_WORKERS,
)
from shared import info, warn


ProgressCallback = Callable[[int | None, str, list[dict[str, Any]] | None], None]
CandidateVerifier = Callable[[dict[str, Any]], dict[str, Any]]


class SearxngNovelSearch:
    """Fetch, clean, merge, and rank novel candidates from SearXNG."""

    _NOISE_DOMAINS = {
        'baidu.com', 'bilibili.com', 'zhihu.com', 'douban.com', 'weibo.com',
        'wikipedia.org', 'baike.com', 'tieba.baidu.com', 'wenku.baidu.com',
        'dushu.baidu.com', 'taobao.com', 'tmall.com', 'jd.com', 'amazon.',
        'qq.com', '163.com', 'sina.com.cn', 'xiaohongshu.com',
    }
    _NOISE_TERMS = {
        '下载', 'txt下载', 'epub下载', '全集下载', '漫画', '动漫', '电视剧',
        '电影', '视频', '百科', '论坛', '攻略', '游戏', '有声', 'mp3',
    }
    _TRACKING_KEYS = {'fbclid', 'gclid', 'ref', 'source', 'from'}
    _SEARCH_PATH_MARKERS = (
        '/bookquery', '/search', '/so/', '/query',
    )
    _SEARCH_HOSTS = {'sogou.com'}

    def __init__(self, base_url: str = SEARXNG_URL, timeout: float = SEARXNG_TIMEOUT,
                 cache_ttl: int = SEARXNG_CACHE_TTL,
                 verify_limit: int = SEARXNG_VERIFY_LIMIT,
                 verify_workers: int = SEARXNG_VERIFY_WORKERS):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.verify_limit = max(0, verify_limit)
        self.verify_workers = max(1, verify_workers)
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._cache_lock = threading.Lock()

    def search(self, keyword: str, callback: ProgressCallback | None = None,
               verifier: CandidateVerifier | None = None) -> list[dict[str, Any]]:
        keyword = self._normalize_keyword(keyword)
        if not keyword:
            return []

        cached = self._read_cache(keyword)
        if cached is not None:
            if callback:
                callback(100, f'已使用 SearXNG 搜索缓存，找到 {len(cached)} 条结果', cached)
            return cached

        queries = self._build_queries(keyword)
        if callback:
            callback(5, f'正在通过 SearXNG 检索 {len(queries)} 组小说关键词...', None)

        raw_results: list[tuple[int, dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=len(queries)) as executor:
            futures = {
                executor.submit(self._request, query): index
                for index, query in enumerate(queries)
            }
            completed = 0
            for future in as_completed(futures):
                query_index = futures[future]
                completed += 1
                try:
                    entries = future.result()
                    raw_results.extend((query_index, entry) for entry in entries)
                    message = f'SearXNG 已完成第 {completed}/{len(queries)} 组检索，获得 {len(entries)} 条候选'
                except requests.RequestException as exc:
                    warn('Search', f'SearXNG request failed: {type(exc).__name__}')
                    message = f'SearXNG 第 {completed}/{len(queries)} 组检索暂不可用'
                except (TypeError, ValueError) as exc:
                    warn('Search', f'SearXNG response rejected: {type(exc).__name__}')
                    message = f'SearXNG 第 {completed}/{len(queries)} 组结果格式异常'
                if callback:
                    callback(10 + int(completed / len(queries) * 70), message, None)

        results = self._normalize_and_rank(keyword, raw_results)
        if verifier and results:
            results = self._verify_candidates(keyword, results, verifier, callback)
        self._write_cache(keyword, results)
        if callback:
            callback(100, f'SearXNG 聚合完成，共 {len(results)} 条可读候选', results)
        info('Search', f'SearXNG novel search completed: {keyword}, {len(results)} candidates')
        return results

    def _verify_candidates(self, keyword: str, results: list[dict[str, Any]], verifier: CandidateVerifier,
                           callback: ProgressCallback | None) -> list[dict[str, Any]]:
        candidates = results[:self.verify_limit]
        if not candidates:
            return results
        if callback:
            callback(82, f'正在验证前 {len(candidates)} 个候选书源...', None)

        verified_by_url: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(self.verify_workers, len(candidates))) as executor:
            # The verifier needs the original requested book name to distinguish a
            # real catalog from a site's own search-results page. Keep that input
            # private so it never leaks into the result sent to the browser.
            futures = {
                executor.submit(verifier, {**item, '_search_keyword': keyword}): item
                for item in candidates
            }
            completed = 0
            for future in as_completed(futures):
                original = futures[future]
                completed += 1
                try:
                    verification = future.result()
                    if not isinstance(verification, dict):
                        verification = {'status': 'unavailable', 'status_detail': '验证未返回有效结果'}
                except Exception as exc:
                    warn('Search', f'Candidate verification failed: {type(exc).__name__}')
                    verification = {'status': 'unavailable', 'status_detail': '书源验证失败'}
                updated = dict(original)
                updated.update(verification)
                verified_by_url[original['url']] = updated
                if callback:
                    callback(82 + int(completed / len(candidates) * 15),
                             f'已验证 {completed}/{len(candidates)} 个候选书源', None)

        merged = [verified_by_url.get(item['url'], item) for item in results]
        merged.sort(key=lambda item: (self._verification_rank(item.get('status')), -item['match_score'], item['title']))
        return merged

    @staticmethod
    def _verification_rank(status: Any) -> int:
        return {
            'ready_catalog': 0,
            'ready_chapter': 1,
            'unverified': 2,
            'blocked': 3,
            'unavailable': 4,
            'unsafe': 5,
        }.get(str(status), 4)

    def _request(self, query: str) -> list[dict[str, Any]]:
        response = requests.get(
            f'{self.base_url}/search',
            params={
                'q': query,
                'format': 'json',
                'categories': 'general',
                'language': 'zh-CN',
                'safesearch': 0,
            },
            headers={'Accept': 'application/json', 'User-Agent': 'NoteDB/1.0'},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get('results', []) if isinstance(payload, dict) else []
        return results if isinstance(results, list) else []

    @staticmethod
    def _normalize_keyword(keyword: str) -> str:
        keyword = re.sub(r'\s+', ' ', str(keyword or '')).strip()
        keyword = re.sub(r'^(?:小说|搜书)\s*[:：]?\s*', '', keyword, flags=re.I)
        return keyword[:80]

    def _build_queries(self, keyword: str) -> list[str]:
        exact = keyword.strip('"“”')
        queries = [f'"{exact}" 小说 目录', f'{exact} 小说 在线阅读']
        if '作者' in exact or re.search(r'\s+[-/\u4f5c\u8005]\s+', exact):
            queries.append(exact)
        return list(dict.fromkeys(queries))

    def _normalize_and_rank(self, keyword: str,
                            raw_results: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for query_index, raw in raw_results:
            item = self._normalize_result(keyword, raw, query_index)
            if not item:
                continue
            existing = merged.get(item['_canonical_url'])
            if not existing or item['_score'] > existing['_score']:
                merged[item['_canonical_url']] = item

        results = sorted(merged.values(), key=lambda item: (-item['_score'], item['title']))
        for item in results:
            item.pop('_score', None)
            item.pop('_canonical_url', None)
        return results[:20]

    def _normalize_result(self, keyword: str, raw: dict[str, Any], query_index: int) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        url = self._canonicalize_url(raw.get('url', ''))
        title = self._clean_text(raw.get('title', ''))
        description = self._clean_text(raw.get('content', ''))
        if not url or not title or self._is_noise(url, title, description):
            return None

        engines = raw.get('engines') or [raw.get('engine', '')]
        engines = [str(engine) for engine in engines if engine]
        engine_label = ', '.join(engines[:2]) or 'unknown'
        score = self._score(keyword, title, description, url, query_index, raw.get('score'))
        return {
            'title': title,
            'url': url,
            'description': description[:240],
            'source': f'SearXNG / {engine_label}',
            'engine': engines,
            'suggested_key': self._suggested_key(keyword),
            'match_score': round(score),
            'status': 'unverified',
            '_canonical_url': url,
            '_score': score,
        }

    def _is_noise(self, url: str, title: str, description: str) -> bool:
        parsed = urlsplit(url)
        host = parsed.netloc.lower().removeprefix('www.')
        if any(domain in host for domain in self._NOISE_DOMAINS):
            return True
        path = '/' + parsed.path.lower().lstrip('/')
        if host in self._SEARCH_HOSTS or any(marker in path for marker in self._SEARCH_PATH_MARKERS):
            return True
        text = f'{title} {description}'.lower()
        return any(term in text for term in self._NOISE_TERMS)

    @staticmethod
    def _clean_text(value: Any) -> str:
        return re.sub(r'\s+', ' ', str(value or '')).strip()

    def _score(self, keyword: str, title: str, description: str, url: str,
               query_index: int, upstream_score: Any) -> float:
        needle = self._compact(keyword)
        haystack = self._compact(title)
        score = 0.0
        if needle and needle in haystack:
            score += 70
        elif needle:
            score += SequenceMatcher(None, needle, haystack).ratio() * 45
        if needle and needle in self._compact(description):
            score += 12
        path = urlsplit(url).path.lower()
        if any(marker in path for marker in ('book', 'novel', 'shu', 'xs', 'read', 'chapter')):
            score += 8
        if query_index == 0:
            score += 5
        try:
            score += min(float(upstream_score or 0), 1) * 5
        except (TypeError, ValueError):
            pass
        return score

    @staticmethod
    def _compact(value: str) -> str:
        return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', value).lower()

    def _canonicalize_url(self, value: Any) -> str:
        try:
            parsed = urlsplit(str(value or '').strip())
        except ValueError:
            return ''
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            return ''
        query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                 if key.lower() not in self._TRACKING_KEYS and not key.lower().startswith('utm_')]
        path = parsed.path.rstrip('/') or '/'
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query), ''))

    @staticmethod
    def _suggested_key(keyword: str) -> str:
        clean = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', keyword)
        try:
            key = ''.join(lazy_pinyin(clean, style=Style.FIRST_LETTER)).lower()
        except Exception:
            key = clean.lower()
        return key[:15] or 'book'

    def _read_cache(self, keyword: str) -> list[dict[str, Any]] | None:
        with self._cache_lock:
            cached = self._cache.get(keyword)
            if not cached or time.monotonic() - cached[0] >= self.cache_ttl:
                self._cache.pop(keyword, None)
                return None
            return [dict(item) for item in cached[1]]

    def _write_cache(self, keyword: str, results: list[dict[str, Any]]) -> None:
        with self._cache_lock:
            self._cache[keyword] = (time.monotonic(), [dict(item) for item in results])
