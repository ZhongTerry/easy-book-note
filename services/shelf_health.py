"""书架健康体检与批量换源。

体检：对书架上每本书的目录/书源 URL 做指纹探测，按站点聚合出
"可达 / 已死 / 存疑 / 不可达"，并持久化最近一次报告。
批量换源：对死亡书籍用现有搜书 + 章节级验证（search_alternative_sources）
生成换源建议，用户确认后逐本应用——不做任何未确认的自动跳转。
"""
import json
import os
import time
from urllib.parse import urlsplit

from managers import USER_DATA_DIR, db_connection, debug, info
import managers

_REPORT_FILE = os.path.join(USER_DATA_DIR, 'shelf_health.json')
_DEAD_CODES = (404, 410)
_SUSPECT_CODES = (401, 403, 429, 503)


def _probe_factory(crawler):
    """生产探测：指纹单次请求，不跟随重定向，按状态码分级。"""
    def probe(url):
        resp = crawler._fetch_page_no_redirect(url)
        if resp is None:
            raise OSError('fetch failed')
        return {'ok': resp.status_code == 200, 'http': resp.status_code}
    return probe


def _books_of(username):
    books = []
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT book_key, content FROM books_v2 WHERE username=? ORDER BY book_key',
            (username,),
        ).fetchall()
    for key, content in rows:
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            continue
        value = data.get('value') or {}
        books.append({
            'book_key': key,
            'url': value.get('url') or '',
            'title': value.get('title') or '',
            'last_read_index': value.get('last_read_index') or 0,
            'last_read_title': value.get('last_read_title') or '',
            'toc_url': (data.get('update_info') or {}).get('toc_url') or '',
        })
    return books


def _classify(http):
    if http in _DEAD_CODES:
        return 'dead'
    if http in _SUSPECT_CODES:
        return 'suspect'
    if http == 200:
        return 'reachable'
    return 'suspect'


def run_health_check(username, probe=None, progress_cb=None):
    """体检全部书籍并持久化报告。probe(url)->{'ok','http'}，抛异常=不可达。"""
    books = _books_of(username)
    results = []
    total = len(books)

    for i, book in enumerate(books):
        if progress_cb:
            try:
                progress_cb(progress=int(i / max(1, total) * 90),
                            msg=f'探测 {book["title"] or book["book_key"]}')
            except Exception:
                pass
        entry = {
            'book_key': book['book_key'],
            'title': book['title'] or book['book_key'],
            'domain': '',
            'probe_url': '',
            'status': 'no_url',
            'http': None,
        }
        target = book['toc_url'] or book['url']
        if target:
            try:
                entry['domain'] = urlsplit(target).hostname or ''
            except Exception:
                entry['domain'] = ''
            entry['probe_url'] = target
            try:
                verdict = probe(target)
                entry['http'] = verdict.get('http')
                entry['status'] = _classify(verdict.get('http'))
            except Exception as exc:
                entry['status'] = 'unreachable'
                entry['http'] = None
                entry['error'] = f'{type(exc).__name__}'
        results.append(entry)

    domains = {}
    for r in results:
        if not r['domain']:
            continue
        bucket = domains.setdefault(
            r['domain'], {'domain': r['domain'], 'reachable': 0, 'dead': 0,
                          'suspect': 0, 'unreachable': 0})
        if r['status'] in bucket:
            bucket[r['status']] += 1

    summary = {'total': total}
    for state in ('reachable', 'dead', 'suspect', 'unreachable', 'no_url'):
        summary[state] = sum(1 for r in results if r['status'] == state)

    report = {
        'checked_at': int(time.time()),
        'summary': summary,
        'domains': sorted(domains.values(),
                          key=lambda d: -(d['dead'] + d['suspect'] + d['unreachable'])),
        'results': results,
    }
    try:
        tmp = _REPORT_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False)
        os.replace(tmp, _REPORT_FILE)
    except Exception as exc:
        debug("ShelfHealth", f"报告持久化失败: {exc}")

    info("ShelfHealth", f"体检完成: {summary['total']} 本 "
                        f"(可达 {summary['reachable']} / 死亡 {summary['dead']} / "
                        f"存疑 {summary['suspect']} / 不可达 {summary['unreachable']})")
    if progress_cb:
        try:
            progress_cb(progress=100, msg='体检完成')
        except Exception:
            pass
    return report


def run_health_check_task(username, crawler, callback=None, **_ignored):
    """task_manager 任务入口。"""
    return run_health_check(username, probe=_probe_factory(crawler),
                            progress_cb=callback)


def load_latest_report():
    try:
        with open(_REPORT_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def dead_books_from_report(report=None):
    report = report if report is not None else load_latest_report()
    if not report:
        return []
    return [r for r in report['results'] if r['status'] == 'dead']


# ---------- 批量换源 ----------

def plan_source_switches(username, book_keys, crawler, progress_cb=None):
    """为指定书籍生成换源建议（候选已通过章节级验证，但应用需用户确认）。"""
    books = {b['book_key']: b for b in _books_of(username)}
    suggestions = []
    for i, key in enumerate(book_keys):
        book = books.get(key)
        if not book:
            suggestions.append({'book_key': key, 'status': 'error', 'reason': '书籍不存在'})
            continue
        if progress_cb:
            try:
                progress_cb(progress=int(i / max(1, len(book_keys)) * 90),
                            msg=f'搜索新源: {book["title"] or key}')
            except Exception:
                pass
        title = book['title']
        target_id = book['last_read_index']
        if not title:
            suggestions.append({'book_key': key, 'status': 'error', 'reason': '缺少书名，无法搜索'})
            continue
        if not target_id:
            suggestions.append({'book_key': key, 'title': title, 'status': 'error',
                                'reason': '无法识别目标章节号'})
            continue
        try:
            candidates = crawler.search_alternative_sources(title, target_id)
        except Exception as exc:
            candidates = []
            debug("ShelfHealth", f"换源搜索异常 {key}: {exc}")
        suggestions.append({
            'book_key': key,
            'title': title,
            'target_id': target_id,
            'old_url': book['url'],
            'status': 'found' if candidates else 'not_found',
            'candidates': candidates,
            'suggested': candidates[0] if candidates else None,
        })
        time.sleep(1)  # 对搜索后端的礼貌间隔

    summary = {
        'requested': len(book_keys),
        'found': sum(1 for s in suggestions if s['status'] == 'found'),
        'not_found': sum(1 for s in suggestions if s['status'] == 'not_found'),
        'error': sum(1 for s in suggestions if s['status'] == 'error'),
    }
    info("ShelfHealth", f"换源建议生成: 找到 {summary['found']}/{summary['requested']}")
    if progress_cb:
        try:
            progress_cb(progress=100, msg='建议生成完成')
        except Exception:
            pass
    return {'suggestions': suggestions, 'summary': summary}


def plan_source_switches_task(username, book_keys, crawler, callback=None, **_ignored):
    return plan_source_switches(username, book_keys, crawler, progress_cb=callback)


def apply_switches(username, switches):
    """应用用户确认的换源：清旧缓存 → 更新书源 URL。逐本返回结果。"""
    results = []
    for switch in switches or []:
        key = (switch or {}).get('book_key')
        new_url = (switch or {}).get('new_url') or ''
        entry = {'book_key': key, 'status': 'error'}
        try:
            from shared import is_safe_url
            book = managers.db.get_raw_book(username, key)
            if not book:
                entry['reason'] = '书籍不存在'
            elif not is_safe_url(new_url):
                entry['reason'] = '新地址未通过安全校验'
            else:
                old_url = (book.get('value') or {}).get('url')
                if old_url:
                    managers.cache.delete(old_url)
                managers.db.update(key, new_url, username=username)
                entry.update(status='applied', new_url=new_url)
        except Exception as exc:
            entry['reason'] = f'{type(exc).__name__}: {exc}'
        results.append(entry)
    return {'results': results,
            'applied': sum(1 for r in results if r['status'] == 'applied')}
