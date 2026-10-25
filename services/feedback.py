"""页面质量反馈：阅读页一键反馈 → 记录 → 触发规则再生成 → 回写结果。

反馈本身是审计记录；真正的解析改进由 rule_generator 的生成管线完成，
这里只负责登记与状态回写。
"""
import json

from managers import db_connection, debug


def _ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS page_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        domain TEXT,
                        book_key TEXT,
                        username TEXT,
                        note TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        outcome TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')


def record_feedback(url, book_key, username, domain, note=''):
    """登记一条反馈，返回反馈 id；状态 pending 直到生成任务回写。"""
    try:
        with db_connection() as conn:
            _ensure_table(conn)
            cursor = conn.execute(
                '''INSERT INTO page_feedback (url, domain, book_key, username, note)
                   VALUES (?, ?, ?, ?, ?)''',
                (url, domain, book_key or '', username or '', (note or '')[:200]),
            )
            return cursor.lastrowid
    except Exception as e:
        debug("Feedback", f"记录反馈失败: {e}")
        return None


def update_feedback_outcome(feedback_id, result):
    """生成任务结束后回写：active→rule_updated，其余按结果标注。"""
    if not feedback_id:
        return
    status_map = {'active': 'rule_updated', 'unverified': 'unverified', 'error': 'failed'}
    status = status_map.get(result.get('status'), 'failed')
    outcome = {k: result.get(k) for k in ('status', 'domain', 'score', 'message', 'paragraphs')
               if result.get(k) is not None}
    try:
        with db_connection() as conn:
            _ensure_table(conn)
            conn.execute(
                '''UPDATE page_feedback SET status=?, outcome=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?''',
                (status, json.dumps(outcome, ensure_ascii=False), feedback_id),
            )
    except Exception as e:
        debug("Feedback", f"回写反馈结果失败: {e}")


def list_feedback(limit=50):
    try:
        with db_connection() as conn:
            _ensure_table(conn)
            rows = conn.execute(
                '''SELECT id, url, domain, book_key, username, note, status, outcome,
                          created_at, updated_at
                   FROM page_feedback ORDER BY id DESC LIMIT ?''',
                (int(limit),),
            ).fetchall()
        keys = ['id', 'url', 'domain', 'book_key', 'username', 'note', 'status',
                'outcome', 'created_at', 'updated_at']
        import json as _json
        result = []
        for r in rows:
            item = dict(zip(keys, r))
            try:
                item['outcome'] = _json.loads(item['outcome']) if item['outcome'] else None
            except Exception:
                pass
            result.append(item)
        return result
    except Exception as e:
        debug("Feedback", f"列出反馈失败: {e}")
        return []
