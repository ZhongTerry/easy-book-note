"""站点规则存储与生命周期：site_rules 表的 CRUD、使用计数与自动降级。

规则是数据：入库前由 rule_generator 做静态校验与自动验收，本模块只管
存取与统计；应用侧（spider_core）按 domain 取 active 规则并回报结果。
"""
import json
import sqlite3

from managers import db_connection, debug, info, warn

_DEMOTE_AFTER_CONSECUTIVE_FAILURES = 5


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
