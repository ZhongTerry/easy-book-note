"""站点规则存储与生命周期：site_rules 表的 CRUD、使用计数与自动降级。

规则是数据：入库前由 rule_generator 做静态校验与自动验收，本模块只管
存取与统计；应用侧（spider_core）按 domain 取 active 规则并回报结果。
"""
import json
import sqlite3
from urllib.parse import urljoin, urlsplit

from managers import db_connection, debug, info, warn

_DEMOTE_AFTER_CONSECUTIVE_FAILURES = 5


def fetch_rule_page(url, crawler, source_url):
    """规则层取页：指纹请求（curl_cffi），每一跳重定向都做同源与 SSRF 校验。

    指纹客户端失败时退回普通客户端再试一次；任何一跳离开源站域名或
    未通过安全校验即返回 None，由调用方回退通用引擎。
    """
    import requests as plain_requests
    from shared import is_safe_url
    origin = urlsplit(source_url).netloc.lower()
    current = url
    for _ in range(4):
        parsed = urlsplit(current)
        if (parsed.scheme not in ('http', 'https')
                or parsed.netloc.lower() != origin
                or not is_safe_url(current)):
            return None
        response = crawler._fetch_page_no_redirect(current)
        if response is None:
            try:
                response = plain_requests.get(
                    current, timeout=20, allow_redirects=False,
                    proxies=getattr(crawler, 'proxies', None) or None,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
            except Exception:
                return None
        location = response.headers.get('Location') or ''
        if response.status_code in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            continue
        if response.status_code != 200:
            return None
        return crawler._decode_page_response(response)
    return None


def _ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS site_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        domain TEXT NOT NULL,
                        template_key TEXT NOT NULL DEFAULT 'default',
                        rule_json TEXT NOT NULL,
                        confidence REAL,
                        accept_score REAL,
                        status TEXT NOT NULL DEFAULT 'active',
                        sample_url TEXT,
                        hits INTEGER DEFAULT 0,
                        successes INTEGER DEFAULT 0,
                        failures INTEGER DEFAULT 0,
                        consecutive_failures INTEGER DEFAULT 0,
                        last_used_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(domain, template_key)
                    )''')


def upsert_rule(domain, rule, accept_score, confidence=None,
                sample_url=None, status='active'):
    """写入或替换一个域名的规则；返回规则 id。"""
    if status == 'active' and accept_score is not None and accept_score < 0.85:
        status = 'unverified'
    payload = json.dumps(rule, ensure_ascii=False)
    with db_connection() as conn:
        _ensure_table(conn)
        conn.execute(
            '''INSERT INTO site_rules
                   (domain, template_key, rule_json, confidence, accept_score,
                    status, sample_url, consecutive_failures)
               VALUES (?, 'default', ?, ?, ?, ?, ?, 0)
               ON CONFLICT(domain, template_key) DO UPDATE SET
                   rule_json=excluded.rule_json,
                   confidence=excluded.confidence,
                   accept_score=excluded.accept_score,
                   status=excluded.status,
                   sample_url=excluded.sample_url,
                   consecutive_failures=0,
                   updated_at=CURRENT_TIMESTAMP''',
            (domain, payload, confidence, accept_score, status, sample_url),
        )
        row = conn.execute(
            'SELECT id FROM site_rules WHERE domain=? AND template_key=?',
            (domain, 'default'),
        ).fetchone()
    info("SiteRules", f"规则已入库: {domain} (score={accept_score}, status={status})")
    return row[0] if row else None


def get_active_rule(domain):
    """返回 (rule_dict, rule_row) 或 None；只取 status='active'。"""
    try:
        with db_connection() as conn:
            _ensure_table(conn)
            row = conn.execute(
                "SELECT id, rule_json, accept_score FROM site_rules "
                "WHERE domain=? AND template_key='default' AND status='active'",
                (domain,),
            ).fetchone()
    except Exception as e:
        debug("SiteRules", f"读取规则失败: {e}")
        return None
    if not row:
        return None
    try:
        return json.loads(row[1]), row
    except Exception as e:
        debug("SiteRules", f"规则 JSON 损坏: {domain}: {e}")
        return None


def record_rule_usage(rule_id, success, domain=None):
    """回报一次规则应用结果；连续失败达到阈值自动降级。"""
    try:
        with db_connection() as conn:
            _ensure_table(conn)
            if success:
                conn.execute(
                    '''UPDATE site_rules SET hits=hits+1, successes=successes+1,
                           consecutive_failures=0, last_used_at=CURRENT_TIMESTAMP
                       WHERE id=?''',
                    (rule_id,),
                )
            else:
                conn.execute(
                    '''UPDATE site_rules SET hits=hits+1, failures=failures+1,
                           consecutive_failures=consecutive_failures+1,
                           last_used_at=CURRENT_TIMESTAMP
                       WHERE id=?''',
                    (rule_id,),
                )
                row = conn.execute(
                    'SELECT consecutive_failures FROM site_rules WHERE id=?',
                    (rule_id,),
                ).fetchone()
                if row and row[0] >= _DEMOTE_AFTER_CONSECUTIVE_FAILURES:
                    conn.execute(
                        "UPDATE site_rules SET status='failed' WHERE id=?", (rule_id,),
                    )
                    warn("SiteRules",
                         f"规则连续失败 {_DEMOTE_AFTER_CONSECUTIVE_FAILURES} 次，已停用: {domain or rule_id}")
    except Exception as e:
        debug("SiteRules", f"记录规则使用失败: {e}")


def list_rules():
    try:
        with db_connection() as conn:
            _ensure_table(conn)
            rows = conn.execute(
                '''SELECT id, domain, template_key, accept_score, confidence, status,
                          sample_url, hits, successes, failures, updated_at
                   FROM site_rules ORDER BY updated_at DESC'''
            ).fetchall()
        keys = ['id', 'domain', 'template_key', 'accept_score', 'confidence',
                'status', 'sample_url', 'hits', 'successes', 'failures', 'updated_at']
        return [dict(zip(keys, r)) for r in rows]
    except Exception as e:
        debug("SiteRules", f"列出规则失败: {e}")
        return []


def set_rule_status(rule_id, status):
    with db_connection() as conn:
        _ensure_table(conn)
        conn.execute('UPDATE site_rules SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                     (status, rule_id))
    return True


def delete_rule(rule_id):
    with db_connection() as conn:
        _ensure_table(conn)
        conn.execute('DELETE FROM site_rules WHERE id=?', (rule_id,))
    return True


def get_rule_json(rule_id):
    with db_connection() as conn:
        _ensure_table(conn)
        row = conn.execute('SELECT rule_json FROM site_rules WHERE id=?', (rule_id,)).fetchone()
    return json.loads(row[0]) if row else None


def domain_of(url):
    """从 URL 提取可作规则键的域名（小写，去端口）。"""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ''
        return host.rstrip('.').lower()
    except Exception:
        return ''


def apply_site_rule_for_url(url, fetcher):
    """爬虫主链路的规则层入口：返回 (payload|None, trace)。

    payload 已带 rule_applied 盖章，与通用引擎同构（未打识别契约）；
    任何失败都返回 None，由调用方落回通用逻辑。
    """
    from recognition.rule_engine import extract_chapter

    domain = domain_of(url)
    trace = {'domain': domain}
    if not domain:
        trace['reason'] = 'no_domain'
        return None, trace

    found = get_active_rule(domain)
    if not found:
        trace['reason'] = 'no_rule'
        return None, trace
    rule, row = found

    try:
        first_html = fetcher(url)
    except Exception as exc:
        trace['reason'] = f'fetch_failed:{type(exc).__name__}'
        record_rule_usage(row['id'], False, domain=domain)
        return None, trace
    if not first_html:
        trace['reason'] = 'empty_fetch'
        record_rule_usage(row['id'], False, domain=domain)
        return None, trace

    result = extract_chapter(first_html, rule, url, fetcher=fetcher)
    trace.update(result['trace'])
    payload = result['payload']
    ok = bool(payload and payload.get('content'))
    record_rule_usage(row['id'], ok, domain=domain)
    if ok:
        payload['rule_applied'] = {'domain': domain, 'pages': trace.get('pages', 1)}
    else:
        payload = None
    return payload, trace
