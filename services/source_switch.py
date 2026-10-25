"""换源服务：书名解析、候选源章节级验证、应用换源。

以读者视角设计：列表只展示"真实包含当前章节"的源（搜索后立即逐个验证），
点击即切、进度保留；书名解析不再依赖书单——书籍自带标题优先。
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from managers import cache, db_connection, debug, info, warn
from services.searxng_search import SearxngSearchError
from recognition.matching import find_chapter_match
from recognition.chapter_numbers import parse_chapter_number

_ASCII_ONLY = re.compile(r'^[a-zA-Z0-9_\-\s]+$')
_VERIFY_WORKERS = 6
_VERIFY_CANDIDATES = 6
_FAST_MODE_TIMEOUT = 5


def _looks_like_book_name(name):
    """可搜索的书名：含中文/较长的混合串；纯拼音 key 视为不可用。"""
    return bool(name) and not _ASCII_ONLY.match(name.strip())


def resolve_book_name(book_key, sample_url, manual_book_name='', crawler=None,
                      username=None):
    """多级解析真实书名：手动输入 > 书籍自带标题 > 书单 > 现场抓取。

    返回 (book_name 或 None, source 描述)。现场抓取是最后手段。
    """
    manual = (manual_book_name or '').strip()
    if manual and _looks_like_book_name(manual):
        return manual, 'manual'

    try:
        with db_connection() as conn:
            row = conn.execute(
                'SELECT content FROM books_v2 WHERE book_key=?', (book_key,)
            ).fetchone()
        if row:
            content = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            value = (content or {}).get('value') or {}
            meta = (content or {}).get('meta') or {}
            for name in (value.get('title'), meta.get('title'), value.get('book_name')):
                if _looks_like_book_name(name):
                    return name.strip(), 'book_record'
    except Exception as exc:
        debug("SourceSwitch", f"读取书籍标题失败: {exc}")

    try:
        import managers as _managers
        for list_data in _managers.booklist_manager.load(username).values():
            for b in list_data.get('books', []):
                if b.get('key') == book_key and _looks_like_book_name(b.get('title')):
                    return b['title'].strip(), 'booklist'
    except Exception as exc:
        debug("SourceSwitch", f"书单反查失败: {exc}")

    if crawler is not None and sample_url:
        try:
            info("SourceSwitch", f"本地无书名，现场抓取当前页: {sample_url}")
            payload = crawler.run(sample_url)
            name = (payload or {}).get('book_name') or ''
            if _looks_like_book_name(name):
                return name.strip(), 'crawled'
        except Exception as exc:
            debug("SourceSwitch", f"现场抓取书名失败: {exc}")
    return None, 'unresolved'


def find_sources(crawler, sample_url, book_key, chapter_title,
                 manual_book_name='', force=False, progress_cb=None,
                 username=None):
    """搜索并逐个验证候选源：只返回真实包含目标章节的源。

    验证 = fast_mode 抓目录 + find_chapter_match（章节号+标题双重证据），
    与通用引擎同一套匹配可靠性标准。当前源站不出现在结果里。
    """
    from spider_core import searcher

    book_name, name_source = resolve_book_name(
        book_key, sample_url, manual_book_name=manual_book_name, crawler=crawler,
        username=username)
    if not book_name:
        raise LookupError('无法识别书名：请在输入框手动填写中文书名后重新搜索')

    chapter_id = parse_chapter_number(chapter_title)
    if not chapter_id or chapter_id <= 0:
        raise LookupError(f'无法识别目标章节号（标题: {chapter_title or "空"}）')

    def report(progress, msg):
        if progress_cb:
            try:
                progress_cb(progress=progress, msg=msg)
            except Exception:
                pass

    report(10, f'书名: 《{book_name}》 目标: 第 {chapter_id} 章')
    try:
        if force:
            candidates = searcher.search_bing(book_name)
        else:
            candidates = searcher.search_bing_cached(book_name)
    except SearxngSearchError:
        raise  # 搜索后端不可用需要独立诊断码，不得降级成普通失败
    except Exception as exc:
        raise LookupError(f'搜索服务不可用: {exc}') from exc

    sample_domain = (urlparse(sample_url).hostname or '').lower() if sample_url else ''
    candidates = [
        c for c in (candidates or [])[:_VERIFY_CANDIDATES]
        if (urlparse(c.get('url') or '').hostname or '').lower() != sample_domain
    ]
    if not candidates:
        return {'book_name': book_name, 'name_source': name_source,
                'chapter_id': chapter_id, 'sources': [], 'searched': 0}

    report(30, f'搜索到 {len(candidates)} 个候选，正在逐个验证章节…')
    sources = []

    def verify(candidate):
        toc_url = candidate.get('url') or ''
        if not toc_url:
            return None
        toc = crawler.get_toc(toc_url, fast_mode=True)
        if not toc or not toc.get('chapters'):
            return None
        match = find_chapter_match(toc['chapters'], chapter_id, chapter_title)
        if not match:
            return None
        return {
            'source': urlparse(toc_url).hostname or toc_url,
            'url': match.get('url') or '',
            'title': match.get('title') or match.get('name') or '',
            'toc_url': toc_url,
            'confidence': round(float(match.get('match_confidence') or 0), 3),
            'strategy': match.get('match_strategy') or '',
        }

    with ThreadPoolExecutor(max_workers=_VERIFY_WORKERS) as executor:
        futures = {executor.submit(verify, c): c for c in candidates}
        done = 0
        for future in as_completed(futures):
            done += 1
            report(30 + int(done / len(candidates) * 60),
                   f'验证中… {done}/{len(candidates)}')
            try:
                found = future.result()
            except Exception as exc:
                debug("SourceSwitch", f"候选验证失败: {type(exc).__name__}: {exc}")
                continue
            if found and found['url']:
                sources.append(found)

    sources.sort(key=lambda s: (-s['confidence'], s['source']))
    info("SourceSwitch", f"《{book_name}》第 {chapter_id} 章: "
                         f"{len(candidates)} 候选 -> {len(sources)} 个验证通过")
    report(100, f'验证完成：{len(sources)} 个可用源')
    return {'book_name': book_name, 'name_source': name_source,
            'chapter_id': chapter_id, 'sources': sources,
            'searched': len(candidates)}


def apply_switch(username, book_key, new_url, crawler, expected_id=None):
    """应用换源：加载新章节验证后更新书源，清理旧缓存，进度保留。"""
    from shared import is_safe_url

    if not new_url or not is_safe_url(new_url):
        return {'status': 'error', 'message': '新地址未通过安全校验'}
    book = None
    try:
        with db_connection() as conn:
            row = conn.execute(
                'SELECT content FROM books_v2 WHERE book_key=? AND username=?',
                (book_key, username),
            ).fetchone()
        book = json.loads(row[0]) if row and isinstance(row[0], str) else row
    except Exception as exc:
        debug("SourceSwitch", f"读取书籍失败: {exc}")
    if not book:
        return {'status': 'error', 'message': '书籍不存在'}

    # 新章节先抓一遍：既是验证（必须是章节页），也让重读直接命中临时缓存
    payload = crawler.run(new_url)
    page_type = (payload or {}).get('page_type') or ''
    title = (payload or {}).get('title') or ''
    if page_type != 'chapter' or not (payload or {}).get('content'):
        return {'status': 'error',
                'message': '新源该地址不是可用章节页，换源未生效（未做任何修改）'}

    old_url = (book.get('value') or {}).get('url')
    if old_url:
        cache.delete(old_url)
    import managers as _managers
    _managers.db.update(book_key, new_url, username=username)

    note = ''
    if expected_id:
        new_id = parse_chapter_number(title)
        if new_id and new_id != expected_id:
            note = f'注意: 新源章节序号为 {new_id}，与目标 {expected_id} 不同'
            warn("SourceSwitch", f"{book_key}: {note}")

    info("SourceSwitch", f"换源完成: {book_key} -> {new_url} ({title})")
    return {'status': 'applied', 'new_url': new_url, 'chapter_title': title,
            'source': urlparse(new_url).hostname or '', 'note': note}
