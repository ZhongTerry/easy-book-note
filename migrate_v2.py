from shared import debug, info, warn, error
import sqlite3
import json
import uuid
import os
import time
from datetime import datetime

# 配置
DB_PATH = os.path.join("user_data", "data.sqlite")
BACKUP_PATH = os.path.join("user_data", "data.sqlite.v1.bak")

def migrate():
    if not os.path.exists(DB_PATH):
        error("System", f"❌ 找不到数据库文件: {DB_PATH}")
        return

    # 1. 备份数据库
    info("System", f"📦 备份数据库到 {BACKUP_PATH}...")
    import shutil
    shutil.copy2(DB_PATH, BACKUP_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 2. 创建新表 books_v2
        # id: UUID
        # username: 用户名
        # book_key: 原始 key (如 fanqie:123)
        # content: 包含 value, meta, tags, update_info 等的 JSON
        info("System", "🔨 创建新表 books_v2...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS books_v2 (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                book_key TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, book_key)
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_v2_username ON books_v2(username)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_v2_book_key ON books_v2(book_key)")

        # 3. 获取所有用户
        cursor.execute("SELECT DISTINCT username FROM user_books")
        users = [row['username'] for row in cursor.fetchall()]
        
        for username in users:
            info("System", f"👤 正在迁移用户: {username}")
            
            # 获取该用户的标签
            cursor.execute("SELECT json_content FROM user_modules WHERE username=? AND module_type='tags'", (username,))
            tags_row = cursor.fetchone()
            all_tags = json.loads(tags_row['json_content']) if tags_row and tags_row['json_content'] else {}

            # 获取该用户的追更新信息 (从 user_modules 或 book_updates 表)
            # 优先从 book_updates 表拉取，因为它是最新的结构
            cursor.execute("SELECT * FROM book_updates WHERE username=?", (username,))
            updates_rows = cursor.fetchall()
            all_updates = {row['book_key']: dict(row) for row in updates_rows}

            # 获取所有书籍 (排除 :meta 结尾的)
            cursor.execute("SELECT book_key, value, updated_at FROM user_books WHERE username=? AND book_key NOT LIKE '%:meta' AND book_key NOT LIKE '@%'", (username,))
            books_rows = cursor.fetchall()

            for book in books_rows:
                b_key = book['book_key']
                b_value_raw = book['value']
                
                # 尝试解析 value
                try:
                    b_value = json.loads(b_value_raw)
                except:
                    b_value = {"url": b_value_raw}

                # 获取对应的 meta
                cursor.execute("SELECT value FROM user_books WHERE username=? AND book_key=?", (username, f"{b_key}:meta"))
                meta_row = cursor.fetchone()
                b_meta = json.loads(meta_row['value']) if meta_row and meta_row['value'] else {}

                # 组装新 content
                new_content = {
                    "key": b_key,
                    "value": b_value,
                    "meta": b_meta,
                    "tags": all_tags.get(b_key, []),
                    "update_info": all_updates.get(b_key, {}),
                    "legacy_updated_at": book['updated_at']
                }

                # 生成 UUID
                b_uuid = str(uuid.uuid4())

                # 插入新表
                cursor.execute('''
                    INSERT INTO books_v2 (id, username, book_key, content, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (b_uuid, username, b_key, json.dumps(new_content, ensure_ascii=False), book['updated_at']))

        conn.commit()
        info("System", "✅ 迁移完成！")
        info("System", "💡 您可以现在检查 books_v2 表的数据。")
        info("System", "💡 如果一切正常，后续可以修改 managers.py 来使用新表。")

    except Exception as e:
        conn.rollback()
        error("System", f"❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
