"""书架自动备份：复用手动备份的载荷格式，定时快照并按份数轮转。"""
import json
import os
import time
from datetime import datetime

from managers import db_connection, debug, info
import managers


def build_backup(username):
    """与 /api/library/backup 相同的便携载荷（剔除可再生缓存）。"""
    books = []
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT content FROM books_v2 WHERE username=? ORDER BY book_key",
            (username,),
        ).fetchall()
    for row in rows:
        try:
            book = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if isinstance(book, dict):
            book['cache'] = {}  # 目录/正文缓存可再生且可能很大
            books.append(book)
    return {
        'format': 'notedb-library-backup',
        'version': 1,
        'created_at': int(time.time()),
        'books': books,
        'booklists': managers.booklist_manager.load(username),
    }


def create_snapshot(username, backup_dir, keep=14):
    """写一份快照并轮转旧备份；返回 (文件名, 书籍数)。"""
    os.makedirs(backup_dir, exist_ok=True)
    backup = build_backup(username)
    filename = f"library-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path = os.path.join(backup_dir, filename)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

    snapshots = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith('library-') and f.endswith('.json')
    )
    for stale in snapshots[:-max(1, int(keep))]:
        try:
            os.remove(os.path.join(backup_dir, stale))
        except OSError as exc:
            debug("Backup", f"轮转删除失败: {exc}")

    info("Backup", f"书架快照: {username} -> {filename} ({len(backup['books'])} 本)")
    return filename, len(backup['books'])


def snapshot_all_users(backup_dir, keep=14):
    """对所有出现过书籍的用户各做一份快照（自用系统通常只有一人）。"""
    try:
        with db_connection() as conn:
            usernames = [r[0] for r in
                         conn.execute("SELECT DISTINCT username FROM books_v2").fetchall()]
    except Exception as exc:
        debug("Backup", f"枚举用户失败: {exc}")
        return []
    return [
        {'username': u, **dict(zip(('filename', 'books'), create_snapshot(u, backup_dir, keep)))}
        for u in usernames
    ]
