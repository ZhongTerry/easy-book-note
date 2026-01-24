import os
import json
import sqlite3
import hashlib
import time
import uuid
import re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import session, g, has_request_context
from shared import USER_DATA_DIR, CACHE_DIR, DL_DIR
import shared

# ==========================================
# 0. 数据库核心 (SQL版)
# ==========================================
DB_PATH = os.path.join(USER_DATA_DIR, "data.sqlite")

def get_db():
    """获取数据库连接"""
    if has_request_context():
        if 'db' not in g:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
        return g.db
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None: db.close()

def get_current_user():
    if not has_request_context():
        return 'default_user'
    return session.get('user', {}).get('username', 'default_user')

# ==========================================
# 1. 基础类定义 (BaseJsonManager)
# ==========================================
class BaseJsonManager:
    def __init__(self, module_type):
        self.module_type = module_type

    def load(self, username=None):
        u = username or get_current_user()
        try:
            conn = get_db()
            row = conn.execute("SELECT json_content FROM user_modules WHERE username=? AND module_type=?", (u, self.module_type)).fetchone()
            if row and row[0]: return json.loads(row[0])
        except Exception as e:
            # print(f"DB Load Error ({self.module_type}): {e}")
            pass
        return {}

    def save(self, data, username=None):
        u = username or get_current_user()
        try:
            json_str = json.dumps(data, ensure_ascii=False)
            conn = get_db()
            conn.execute("REPLACE INTO user_modules (username, module_type, json_content) VALUES (?, ?, ?)", (u, self.module_type, json_str))
            conn.commit()
            if not has_request_context(): conn.close()
        except Exception as e:
            print(f"DB Save Error ({self.module_type}): {e}")

# ==========================================
# 2. 角色管理 (System Config)
# ==========================================
class RoleManager:
    def load(self):
        return self._load_config()
    def _load_config(self):
        try:
            with get_db() as conn:
                row = conn.execute("SELECT value FROM sys_config WHERE key='roles'").fetchone()
                return json.loads(row[0]) if row else {"admins": [], "pros": []}
        except: return {"admins": [], "pros": []}

    def _save_config(self, data):
        with get_db() as conn:
            conn.execute("REPLACE INTO sys_config (key, value) VALUES (?, ?)", ('roles', json.dumps(data)))
            conn.commit()

    def get_role(self, username):
        if not username: return "guest"
        data = self._load_config()
        if username in data.get("admins", []): return "admin"
        if username in data.get("pros", []): return "pro"
        return "user"

    def set_role(self, username, role):
        data = self._load_config()
        if username in data["admins"]: data["admins"].remove(username)
        if username in data["pros"]: data["pros"].remove(username)
        if role == "admin": data["admins"].append(username)
        elif role == "pro": data["pros"].append(username)
        self._save_config(data)

# ==========================================
# 3. 业务管理器 (继承 BaseJsonManager)
# ==========================================

class HistoryManager(BaseJsonManager):
    def __init__(self): super().__init__('history')

    def add_record(self, book_key, title, url, book_name=None):
        data = self.load()
        if "records" not in data: data["records"] = []
        # 去重并置顶
        records = [r for r in data["records"] if r.get('key') != book_key]
        records.insert(0, {
            "key": book_key,
            "title": title,
            "url": url,
            "timestamp": int(time.time()),
            "book_name": book_name or book_key
        })
        data["records"] = records[:50] # 保留最近50条
        self.save(data)

    def get_history(self): return self.load().get("records", [])
    def clear(self): self.save({"records": []})

class IsolatedBooklistManager(BaseJsonManager):
    def __init__(self): super().__init__('booklists')

    def add_list(self, name):
        data = self.load()
        list_id = str(int(time.time()))
        data[list_id] = {"name": name, "books": []}
        self.save(data)
        return list_id

    def add_to_list(self, list_id, book_data):
        data = self.load()
        if list_id in data:
            if not any(b['key'] == book_data['key'] for b in data[list_id]['books']):
                data[list_id]['books'].append(book_data)
                self.save(data)

    def update_status(self, list_id, book_key, status, action):
        data = self.load()
        if list_id in data:
            books = data[list_id]['books']
            if action == 'remove':
                data[list_id]['books'] = [b for b in books if b['key'] != book_key]
            else:
                for b in books:
                    if b['key'] == book_key: b['status'] = status
            self.save(data)
    
    # 兼容旧代码调用 load 方法直接返回字典
    def load(self, username=None):
        return super().load(username)

class IsolatedTagManager(BaseJsonManager):
    def __init__(self): super().__init__('tags')
    
    def update_tags(self, key, tags):
        d = self.load()
        if tags: d[key] = [t.strip() for t in tags if t.strip()]
        elif key in d: del d[key]
        self.save(d)
        return d.get(key, [])
    
    def get_all(self): return self.load()

class UpdateManager(BaseJsonManager):
    def __init__(self): super().__init__('updates')
    
    def set_update(self, book_key, latest_data, username=None):
        data = self.load(username)
        # 兼容性处理
        title = latest_data.get('title') or latest_data.get('latest_title') or "未知"
        url = latest_data.get('url') or latest_data.get('latest_url')
        cid = latest_data.get('id') or latest_data.get('latest_id') or -1
        
        data[book_key] = {
            "latest_title": title,
            "latest_url": url,
            "latest_id": cid,
            "toc_url": latest_data.get('toc_url'),
            "last_check": int(time.time())
        }
        self.save(data, username)
        
    def get_update(self, book_key):
        return self.load().get(book_key)

    def update_progress(self, book_key, unread_count, status_text, username=None):
        data = self.load(username)
        if book_key in data:
            data[book_key]['unread_count'] = unread_count
            data[book_key]['status_text'] = status_text
            self.save(data, username)

class IsolatedStatsManager(BaseJsonManager):
    def __init__(self): super().__init__('stats')

    def update(self, t, w, c, bk):
        d = self.load()
        if "daily_stats" not in d: d["daily_stats"] = {}
        k = datetime.now().strftime('%Y-%m-%d')
        if k not in d["daily_stats"]: d["daily_stats"][k] = {"time":0,"words":0,"chapters":0,"books":[]}
        r = d["daily_stats"][k]
        r["time"]+=t; r["words"]+=w; r["chapters"]+=c
        if bk and bk not in r["books"]: r["books"].append(bk)
        self.save(d)
        
    def get_summary(self):
        today = datetime.now()
        summary = {
            "24h": {"time": 0, "words": 0, "chapters": 0, "books": 0},
            "7d":  {"time": 0, "words": 0, "chapters": 0, "books": 0},
            "30d": {"time": 0, "words": 0, "chapters": 0, "books": 0},
            "all": {"time": 0, "words": 0, "chapters": 0, "books": 0, "heatmap": []},
            "trend": {"dates": [], "times": []}
        }
        books_sets = {"24h": set(), "7d": set(), "30d": set(), "all": set()}
        data = self.load()
        daily = data.get("daily_stats", {})
        
        for i in range(29, -1, -1):
            day = today - timedelta(days=i)
            d_str = day.strftime('%Y-%m-%d')
            rec = daily.get(d_str, {})
            summary["trend"]["dates"].append(d_str[5:])
            summary["trend"]["times"].append(int(rec.get("time", 0) / 60))

        for date_str, rec in daily.items():
            try:
                rec_date = datetime.strptime(date_str, '%Y-%m-%d')
                delta = (today - rec_date).days
                t, w, c = rec.get("time", 0), rec.get("words", 0), rec.get("chapters", 0)
                b_list = rec.get("books", [])
                summary["all"]["time"] += t; summary["all"]["words"] += w; summary["all"]["chapters"] += c; books_sets["all"].update(b_list)
                if t > 0: summary["all"]["heatmap"].append({"date": date_str, "count": int(t/60)})
                if delta == 0: summary["24h"]["time"] += t; summary["24h"]["words"] += w; summary["24h"]["chapters"] += c; books_sets["24h"].update(b_list)
                if delta < 7: summary["7d"]["time"] += t; summary["7d"]["words"] += w; summary["7d"]["chapters"] += c; books_sets["7d"].update(b_list)
                if delta < 30: summary["30d"]["time"] += t; summary["30d"]["words"] += w; summary["30d"]["chapters"] += c; books_sets["30d"].update(b_list)
            except: pass
        for k in books_sets:
            summary[k]["books"] = len(books_sets[k])
            summary[k]["time"] = int(summary[k]["time"] / 60) 
        return summary

# ==========================================
# 4. 核心 KV 数据库 (SQL版)
# ==========================================
# managers.py 中的 IsolatedDB 类 (替换原有的)

# managers.py -> IsolatedDB 类 (完整替换)

# managers.py -> IsolatedDB 类 (完整替换)

# managers.py -> IsolatedDB 类 (完全替换)

# managers.py -> IsolatedDB 类 (请替换整个类)

class IsolatedDB:
    def _get_db_conn(self):
        username = session.get('user', {}).get('username', 'default_user')
        db_path = os.path.join(USER_DATA_DIR, f"{username}.sqlite")
        conn = sqlite3.connect(db_path)
        return conn

    def _ensure_history_table(self):
        try:
            with get_db() as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS book_history (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                username TEXT NOT NULL,
                                book_key TEXT NOT NULL,
                                value TEXT,
                                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )''')
                # === [新增] 追更表 ===
                conn.execute('''CREATE TABLE IF NOT EXISTS book_updates (
                                book_key TEXT PRIMARY KEY,
                                username TEXT NOT NULL,
                                toc_url TEXT,
                                last_local_id INTEGER DEFAULT 0,
                                last_remote_id INTEGER DEFAULT 0,
                                has_update BOOLEAN DEFAULT 0,
                                updated_at TIMESTAMP
                            )''')
                conn.commit()
        except: pass

    # === 1. 迁移逻辑 (启动时运行) ===
    def migrate_legacy_data(self):
        """将旧的纯文本 URL 转换为 JSON 对象"""
        print("[DB] 检查数据结构版本...")
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT username, book_key, value FROM user_books")
                rows = cursor.fetchall()
                
                count = 0
                for username, key, val in rows:
                    if not val or key.startswith('@') or key.endswith(':meta'): continue
                    
                    # 检查是否已经是 JSON
                    is_json = False
                    try:
                        d = json.loads(val)
                        if isinstance(d, dict) and 'url' in d: is_json = True
                    except: pass
                    
                    if not is_json:
                        # 迁移：纯字符串 -> JSON
                        new_data = json.dumps({
                            "url": val,
                            "cover": "",
                            "author": "",
                            "desc": "",
                            "updated_at": int(time.time())
                        }, ensure_ascii=False)
                        cursor.execute("UPDATE user_books SET value=? WHERE username=? AND book_key=?", (new_data, username, key))
                        count += 1
                
                conn.commit()
                if count > 0: print(f"[DB] ✅ 已迁移 {count} 条旧数据为 JSON 格式")
        except Exception as e:
            print(f"[DB] 迁移检查跳过: {e}")

    # === 2. 核心写入逻辑 (自动包装) ===
    def insert(self, key, value, username=None):
        if not key: return {"status": "error", "message": "Key cannot be empty"}
        u = username or get_current_user()
        
        final_json = ""
        if isinstance(value, dict):
            final_json = json.dumps(value, ensure_ascii=False)
        else:
            try:
                temp = json.loads(value)
                if isinstance(temp, dict) and 'url' in temp: final_json = value
                else: raise ValueError()
            except:
                final_json = json.dumps({
                    "url": value, 
                    "updated_at": int(time.time())
                }, ensure_ascii=False)

        try:
            with get_db() as conn:
                conn.execute("INSERT OR REPLACE INTO user_books (username, book_key, value) VALUES (?, ?, ?)", (u, key, final_json))
                conn.commit()
            return {"status": "success", "message": f"Saved: {key}", "data": {key: final_json}}
        except Exception as e: return {"status": "error", "message": str(e)}

    def update(self, key, value, username=None):
        u = username or get_current_user()
        try:
            conn = get_db()
            row = conn.execute("SELECT value FROM user_books WHERE username=? AND book_key=?", (u, key)).fetchone()
            
            # [关键修复] 对于 :meta 结尾的 key，直接存储原始值，不要包装
            if key.endswith(':meta'):
                # value 已经是 JSON 字符串，直接存储
                conn.execute("INSERT OR REPLACE INTO user_books (username, book_key, value) VALUES (?, ?, ?)", 
                           (u, key, value))
                conn.commit()
                return {"status": "success", "message": f"Updated meta: {key}"}
            
            # 正常的书籍 key，使用包装逻辑
            current_data = {}
            if row and row[0]:
                try: current_data = json.loads(row[0])
                except: current_data = {"url": row[0]}
            
            if isinstance(value, dict):
                current_data.update(value)
            else:
                current_data['url'] = value
            
            current_data['updated_at'] = int(time.time())
            
            final_json = json.dumps(current_data, ensure_ascii=False)
            conn.execute("UPDATE user_books SET value=? WHERE username=? AND book_key=?", (final_json, u, key))
            conn.commit()
            return {"status": "success", "message": f"Updated: {key}"}
        except:
            return self.insert(key, value, username=username)

    # === 3. 核心读取逻辑 (自动解包 - 兼容旧接口) ===
    def get_val(self, key, username=None):
        """默认只返回 URL 字符串，保证旧代码不崩"""
        # [关键修复] 对于 :meta 结尾的 key，直接返回原始值
        if key.endswith(':meta'):
            u = username or get_current_user()
            try:
                conn = get_db()
                row = conn.execute("SELECT value FROM user_books WHERE username=? AND book_key=?", (u, key)).fetchone()
                return row[0] if row and row[0] else None
            except:
                return None
        
        # 正常的书籍 key，使用解包逻辑
        full = self.get_full_data(key, username=username)
        return full.get('url') if full else None

    def get_full_data(self, key, username=None):
        """新接口：获取完整元数据"""
        u = username or get_current_user()
        try:
            conn = get_db()
            row = conn.execute("SELECT value FROM user_books WHERE username=? AND book_key=?", (u, key)).fetchone()
            if row and row[0]:
                try:
                    d = json.loads(row[0])
                    return d if isinstance(d, dict) else {"url": row[0]}
                except: return {"url": row[0]}
            return None
        except: return None

    # === 4. 列表查询 (自动解包) ===
    def list_all(self):
        u = get_current_user()
        try:
            conn = get_db()
            cursor = conn.execute("SELECT book_key, value FROM user_books WHERE username=? AND book_key NOT LIKE '@%' ORDER BY updated_at DESC", (u,))
            result = {}
            for row in cursor.fetchall():
                k, v_str = row[0], row[1]
                if k.endswith(':meta'): continue
                try:
                    obj = json.loads(v_str)
                    if isinstance(obj, dict): result[k] = obj
                    else: result[k] = {"url": v_str}
                except: result[k] = {"url": v_str}
            return {"status": "success", "data": result}
        except Exception as e: return {"status": "error", "message": str(e)}

    # ... (find, remove, rename_key, rollback, add_version, get_versions 保持不变) ...
    def find(self, term):
        u = get_current_user()
        try:
            t = f'%{term}%'
            conn = get_db()
            cursor = conn.execute("SELECT book_key, value FROM user_books WHERE username=? AND (book_key LIKE ? OR value LIKE ?)", (u, t, t))
            result = {}
            for row in cursor.fetchall():
                k, v_str = row[0], row[1]
                if k.endswith(':meta'): continue
                try:
                    obj = json.loads(v_str)
                    result[k] = obj if isinstance(obj, dict) else {"url": obj}
                except: result[k] = {"url": v_str}
            return {"status": "success", "data": result}
        except Exception as e: return {"status": "error", "message": str(e)}

    def remove(self, key):
        u = get_current_user()
        try:
            with get_db() as conn:
                conn.execute("DELETE FROM user_books WHERE username=? AND book_key=?", (u, key))
                conn.commit()
            return {"status": "success", "message": f"Removed: {key}"}
        except Exception as e: return {"status": "error", "message": str(e)}

    def rename_key(self, old_key, new_key):
        if not old_key or not new_key: return {"status": "error", "message": "Key cannot be empty"}
        u = get_current_user()
        try:
            with get_db() as conn:
                exists = conn.execute("SELECT 1 FROM user_books WHERE username=? AND book_key=?", (u, new_key)).fetchone()
                if exists: return {"status": "error", "message": f"目标 Key [{new_key}] 已存在"}
                conn.execute("UPDATE user_books SET book_key=? WHERE username=? AND book_key=?", (new_key, u, old_key))
                conn.execute("UPDATE user_books SET book_key=? WHERE username=? AND book_key=?", (f"{new_key}:meta", u, f"{old_key}:meta"))
                conn.execute("UPDATE book_history SET book_key=? WHERE username=? AND book_key=?", (new_key, u, old_key))
                conn.commit()
            
            tags_data = tag_manager.load(u)
            if old_key in tags_data:
                tags_data[new_key] = tags_data.pop(old_key)
                tag_manager.save(tags_data, u)
            
            updates_data = update_manager.load(u)
            if old_key in updates_data:
                updates_data[new_key] = updates_data.pop(old_key)
                update_manager.save(updates_data, u)

            bl_data = booklist_manager.load(u)
            changed = False
            for lid in bl_data:
                for b in bl_data[lid].get('books', []):
                    if b['key'] == old_key:
                        b['key'] = new_key
                        changed = True
            if changed: booklist_manager.save(bl_data, u)

            return {"status": "success", "message": f"已将 [{old_key}] 重命名为 [{new_key}]"}
        except Exception as e: return {"status": "error", "message": str(e)}

    def rollback(self): return {"status": "error", "message": "Use version history instead"}
    
    def add_version(self, key, value):
        self._ensure_history_table()
        u = get_current_user()
        try:
            with get_db() as conn:
                conn.execute("INSERT INTO book_history (username, book_key, value) VALUES (?, ?, ?)", (u, key, value))
                conn.execute(f'''
                    DELETE FROM book_history 
                    WHERE id IN (
                        SELECT id FROM book_history 
                        WHERE username=? AND book_key=? 
                        ORDER BY recorded_at DESC 
                        LIMIT -1 OFFSET 5
                    )
                ''', (u, key))
                conn.commit()
            return True
        except Exception as e: return False

    def get_versions(self, key):
        self._ensure_history_table()
        u = get_current_user()
        try:
            with get_db() as conn:
                cursor = conn.execute("SELECT value, recorded_at FROM book_history WHERE username=? AND book_key=? ORDER BY recorded_at DESC", (u, key))
                return [{"value": row[0], "time": row[1]} for row in cursor.fetchall()]
        except: return []

class UpdateRecordManager:
    """管理自动追更的数据库操作"""
    def __init__(self):
        self._ensure_table() # [修复] 初始化时自动建表
        pass

    def _ensure_table(self):
        """确保 book_updates 表存在"""
        try:
            # 这里调用 get_db() 可能会因为没有 request context 报错
            # 但我们在 __init__ 里调用时通常是在 import 阶段，也不行
            # 所以只能把建表逻辑通过独立的连接来做，或者每次操作前检查
            
            # 使用独立连接建表，防止 Flask context 报错
            conn = sqlite3.connect(DB_PATH) 
            conn.execute('''CREATE TABLE IF NOT EXISTS book_updates (
                            book_key TEXT PRIMARY KEY,
                            username TEXT NOT NULL,
                            toc_url TEXT,
                            last_local_id INTEGER DEFAULT 0,
                            last_remote_id INTEGER DEFAULT 0,
                            has_update BOOLEAN DEFAULT 0,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )''')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB Init] 自动建表失败 (不用担心，可能是文件锁定): {e}")

    def subscribe(self, username, book_key, toc_url, current_id):
        """开启追更"""
        # 为了双重保险，如果 __init__ 失败了，这里再试一次
        try: self._ensure_table()
        except: pass
        
        with get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO book_updates 
                (book_key, username, toc_url, last_local_id, has_update, updated_at)
                VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            """, (book_key, username, toc_url, current_id))
            conn.commit()

    def unsubscribe(self, book_key):
        """取消追更"""
        with get_db() as conn:
            conn.execute("DELETE FROM book_updates WHERE book_key=?", (book_key,))
            conn.commit()

    def is_subscribed(self, book_key):
        with get_db() as conn:
            row = conn.execute("SELECT 1 FROM book_updates WHERE book_key=?", (book_key,)).fetchone()
            return bool(row)

    # [新增] 获取更详细的状态，供前端渲染红点
    def get_book_status(self, book_key):
        with get_db() as conn:
            row = conn.execute("SELECT has_update, last_remote_id FROM book_updates WHERE book_key=?", (book_key,)).fetchone()
            if row:
                return {"subscribed": True, "has_update": bool(row['has_update']), "remote_id": row['last_remote_id']}
            return {"subscribed": False, "has_update": False}
    
    def update_status(self, book_key, remote_id, has_u):
        with get_db() as conn:
            conn.execute("UPDATE book_updates SET last_remote_id=?, has_update=?, updated_at=CURRENT_TIMESTAMP WHERE book_key=?", 
                         (remote_id, 1 if has_u else 0, book_key))
            conn.commit()

    def get_all_updates(self, username):
        """获取某用户所有有更新的书"""
        with get_db() as conn:
            rows = conn.execute("SELECT book_key FROM book_updates WHERE username=? AND has_update=1", (username,)).fetchall()
            return [r[0] for r in rows]

    # [新增] 获取某用户所有已订阅的书 (用于 api_get_updates_status 确定检查范围)
    def get_all_subscribed(self, username):
        """获取某用户所有开启了自动追更的书"""
        with get_db() as conn:
            rows = conn.execute("SELECT book_key FROM book_updates WHERE username=?", (username,)).fetchall()
            return [r[0] for r in rows]

    def get_all_tasks(self):
        """后台线程用：获取所有任务"""
        # 注意：这里可能是在 request 上下文之外调用的，所以不能用 get_db()，要手动连
        # 但因为 DB 是按用户分文件的，我们这里需要遍历所有用户的 DB 文件？
        # 简化策略：目前单机版很多逻辑还没做完全的用户隔离，我们先只处理主DB
        # 或者修正逻辑：schedule_auto_check 负责遍历文件
        pass

# ==========================================
# 5. 文件/缓存管理
# ==========================================
class OfflineBookManager:
    def __init__(self):
        self.offline_dir = os.path.join(USER_DATA_DIR, "offline_books")
        if not os.path.exists(self.offline_dir): os.makedirs(self.offline_dir)
    def _get_book_path(self, k): return os.path.join(self.offline_dir, f"{k}.json")
    def is_downloaded(self, k): return os.path.exists(self._get_book_path(k))
    def save_book(self, k, d): 
        with open(self._get_book_path(k), 'w', encoding='utf-8') as f: json.dump(d, f)
    def get_chapter(self, k, u):
        if not self.is_downloaded(k): return None
        try:
            with open(self._get_book_path(k), 'r') as f: return json.load(f).get(u)
        except: return None
import redis
import threading
class CacheManager:
    def __init__(self, ttl=604800): 
        self.cache_dir = CACHE_DIR
        self.ttl = ttl 
    def _get_filename(self, url):
        hash_object = hashlib.md5(url.encode('utf-8'))
        return os.path.join(self.cache_dir, hash_object.hexdigest() + ".json")
    def get(self, url):
        fp = self._get_filename(url)
        if not os.path.exists(fp): return None
        if time.time() - os.path.getmtime(fp) > self.ttl: return None
        try:
            with open(fp, 'r', encoding='utf-8') as f: return json.load(f)
        except: return None
    def set(self, url, data):
        fp = self._get_filename(url)
        # [修复] 修正语法错误，拆分为标准写法
        try:
            with open(fp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            print(f"[Cache] Write Error: {e}")
            
    def cleanup_expired(self):
        now = time.time(); count = 0; size = 0
        for f in os.listdir(self.cache_dir):
            fp = os.path.join(self.cache_dir, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > self.ttl:
                try: size += os.path.getsize(fp); os.remove(fp); count += 1
                except: pass
        return count, size / (1024*1024)

class DownloadManager:
    def __init__(self):
        self.downloads = {}
        self.executor = ThreadPoolExecutor(max_workers=5)
    def start_download(self, book_name, chapters, crawler_instance):
        task_id = hashlib.md5((book_name + str(time.time())).encode()).hexdigest()
        self.downloads[task_id] = {'book_name': book_name, 'total': len(chapters), 'current': 0, 'status': 'running', 'filename': f"{re.sub(r'[\\/*?:|<>]', '', book_name)}.txt"}
        threading.Thread(target=self._master_worker, args=(task_id, chapters, crawler_instance)).start()
        return task_id
    def _master_worker(self, task_id, chapters, crawler):
        task = self.downloads[task_id]
        results = [None] * len(chapters)
        with ThreadPoolExecutor(max_workers=8) as pool:
            future_to_index = {pool.submit(self._fetch_worker, c['url'], crawler): i for i, c in enumerate(chapters)}
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    c, t = future.result()
                    # [修复] 格式化
                    results[idx] = f"\n\n=== {t} ===\n\n" + '\n'.join(c)
                except Exception as e: 
                    results[idx] = f"\n\nError: {e}"
                task['current'] += 1
        try:
            with open(os.path.join(DL_DIR, task['filename']), 'w', encoding='utf-8') as f:
                f.write(f"=== {task['book_name']} ===\n")
                for r in results: f.write(r or "")
            task['status'] = 'completed'
        except Exception as e: task['status'] = 'error'; task['error_msg'] = str(e)
    def _fetch_worker(self, url, crawler):
        data = crawler.run(url)
        if data and data['content']: return data['content'], data.get('title', '')
        raise Exception("Empty")
    def get_status(self, tid): return self.downloads.get(tid)

# ==========================================
# 导出管理器 (TXT/EPUB) - 支持断点续传
# ==========================================
import threading
class ExportManager:
    def __init__(self):
        self.exports = {}  # 内存中的活跃任务
        self.task_file = os.path.join(USER_DATA_DIR, 'export_tasks.json')
        self._load_tasks()
        
    def _load_tasks(self):
        """加载持久化的任务"""
        if os.path.exists(self.task_file):
            try:
                with open(self.task_file, 'r', encoding='utf-8') as f:
                    saved_tasks = json.load(f)
                    # 加载所有任务（包括已完成的，用于历史记录）
                    for task_id, task in saved_tasks.items():
                        if task.get('status') not in ['completed', 'error']:
                            task['status'] = 'paused'  # 未完成的标记为暂停
                        # 添加创建时间（如果没有）
                        if 'created_at' not in task:
                            task['created_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                        self.exports[task_id] = task
            except Exception as e:
                print(f"[ExportManager] 加载任务失败: {e}")
    
    def _save_task(self, task_id):
        """保存单个任务到文件"""
        try:
            all_tasks = {}
            if os.path.exists(self.task_file):
                with open(self.task_file, 'r', encoding='utf-8') as f:
                    all_tasks = json.load(f)
            
            all_tasks[task_id] = self.exports[task_id]
            
            with open(self.task_file, 'w', encoding='utf-8') as f:
                json.dump(all_tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ExportManager] 保存任务失败: {e}")
    
    def find_unfinished_task(self, book_name):
        """查找指定书籍的未完成任务"""
        for task_id, task in self.exports.items():
            if task.get('book_name') == book_name and task.get('status') == 'paused':
                return task_id
        return None
    
    def start_export(self, book_name, chapters, crawler_instance, export_format='txt', metadata=None, resume_task_id=None, delay=0.5):
        """启动导出任务（支持续传）
        
        Args:
            delay: 每个章节抓取后的延迟时间（秒），默认 0.5 秒，防止被封
        """
        if resume_task_id and resume_task_id in self.exports:
            # 断点续传
            task_id = resume_task_id
            task = self.exports[task_id]
            task['status'] = 'running'
            task['delay'] = delay  # 更新延迟设置
            print(f"[Export] 续传任务 {task_id}，已完成 {len(task.get('completed_chapters', []))} 章")
        else:
            # 新任务
            task_id = hashlib.md5((book_name + str(time.time())).encode()).hexdigest()
            safe_name = re.sub(r'[\\/*?:|<>]', '', book_name)
            filename = f"{safe_name}.{export_format}"
            
            task = {
                'book_name': book_name,
                'total': len(chapters),
                'current': 0,
                'status': 'running',
                'format': export_format,
                'filename': filename,
                'metadata': metadata or {},
                'chapters': [{'name': c.get('name', f'第{i+1}章'), 'url': c['url']} for i, c in enumerate(chapters)],
                'completed_chapters': [],  # 已完成的章节索引
                'results': {},  # 已抓取的章节内容 {index: {title, content}}
                'delay': delay,  # 抓取延迟（秒）
                'paused': False,  # 暂停标志
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S')  # 创建时间
            }
            self.exports[task_id] = task
        
        self._save_task(task_id)
        threading.Thread(target=self._export_worker, args=(task_id, crawler_instance)).start()
        return task_id
    
    def pause_export(self, task_id):
        """暂停导出任务"""
        if task_id in self.exports:
            self.exports[task_id]['paused'] = True
            self.exports[task_id]['status'] = 'paused'
            self._save_task(task_id)
            return True
        return False
    
    def resume_export(self, task_id, crawler_instance):
        """恢复暂停的导出任务"""
        if task_id in self.exports:
            task = self.exports[task_id]
            if task.get('status') == 'paused':
                task['status'] = 'running'
                task['paused'] = False
                self._save_task(task_id)
                threading.Thread(target=self._export_worker, args=(task_id, crawler_instance)).start()
                return True
        return False
    
    def _export_worker(self, task_id, crawler):
        """导出工作线程（支持跳过已完成章节和暂停）"""
        task = self.exports[task_id]
        chapters = task['chapters']
        completed = set(task.get('completed_chapters', []))
        results = task.get('results', {})
        delay = task.get('delay', 0.5)  # 获取延迟设置
        
        # 转换 results 的 key 为整数（JSON 保存后会变成字符串）
        results = {int(k): v for k, v in results.items()}
        
        # [新增] 尝试集群并行爬取
        use_cluster = cluster_manager.use_redis and len(cluster_manager.get_active_nodes()) > 0
        
        if use_cluster:
            print(f"[Export] 🚀 启用集群并行爬取模式（{len(cluster_manager.get_active_nodes())} 个节点在线）")
            results = self._cluster_parallel_fetch(task_id, chapters, completed, results, delay)
        else:
            print(f"[Export] 🐢 使用本地并发模式（集群不可用）")
            # 原有的本地并发逻辑
            pending_chapters = [(i, c) for i, c in enumerate(chapters) if i not in completed]
            
            with ThreadPoolExecutor(max_workers=3) as pool:
                future_to_index = {
                    pool.submit(self._fetch_chapter, c['url'], crawler): i 
                    for i, c in pending_chapters
                }
                
                for future in as_completed(future_to_index):
                    # 检查暂停标志
                    if task.get('paused'):
                        print(f"[Export] 任务 {task_id} 已暂停")
                        # 取消所有未完成的任务
                        for f in future_to_index:
                            f.cancel()
                        break
                    
                    idx = future_to_index[future]
                    try:
                        results[idx] = future.result()
                        completed.add(idx)
                    except Exception as e:
                        results[idx] = {
                            'title': chapters[idx].get('name', f'第{idx+1}章'), 
                            'content': f'抓取失败: {str(e)}'
                        }
                        completed.add(idx)
                    
                    # 更新进度
                    task['current'] = len(completed)
                    task['completed_chapters'] = list(completed)
                    task['results'] = results
                    
                    # 每完成一章添加延迟，防止被封
                    if delay > 0:
                        import random
                        actual_delay = delay * random.uniform(0.8, 1.2)  # 随机浮动 ±20%
                        time.sleep(actual_delay)
                    
                    # 每完成 10 章保存一次
                    if len(completed) % 10 == 0:
                        self._save_task(task_id)
        
        # 如果被暂停，不生成文件
        if task.get('paused'):
            self._save_task(task_id)
            return
        
        # 生成文件
        try:
            # 按索引排序结果
            sorted_results = [results[i] for i in range(len(chapters))]
            
            if task['format'] == 'txt':
                self._generate_txt(task, sorted_results)
            elif task['format'] == 'epub':
                self._generate_epub(task, sorted_results)
            
            task['status'] = 'completed'
            # 完成后清理 results 以节省空间
            task.pop('results', None)
        except Exception as e:
            task['status'] = 'error'
            task['error_msg'] = str(e)
        
        self._save_task(task_id)
    
    def _fetch_chapter(self, url, crawler):
        """抓取单个章节"""
        data = crawler.run(url)
        if data and data.get('content'):
            return {
                'title': data.get('title', '无标题'),
                'content': '\n'.join(data['content']) if isinstance(data['content'], list) else data['content']
            }
        raise Exception("章节内容为空")
    
    def _cluster_parallel_fetch(self, task_id, chapters, completed, results, delay):
        """集群并行爬取章节"""
        import uuid as uuid_lib
        import json
        import time
        from spider_core import _remote_request
        
        task = self.exports[task_id]
        pending_chapters = [(i, c) for i, c in enumerate(chapters) if i not in completed]
        
        if not pending_chapters:
            return results
        
        print(f"[Cluster] 📦 待爬取章节: {len(pending_chapters)} 章")
        
        # 批量推送任务到队列
        task_mapping = {}  # {task_uuid: chapter_index}
        
        for idx, chapter in pending_chapters:
            # 检查暂停标志
            if task.get('paused'):
                print(f"[Cluster] 任务 {task_id} 已暂停，停止推送")
                break
                
            task_uuid = str(uuid_lib.uuid4())
            task_package = {
                "id": task_uuid,
                "endpoint": "run",
                "payload": {"url": chapter['url']},
                "timestamp": time.time()
            }
            
            try:
                cluster_manager.r.lpush("crawler:queue:pending", json.dumps(task_package))
                task_mapping[task_uuid] = idx
                print(f"[Cluster] ✅ 已推送: 第{idx+1}章 ({chapter.get('name', '无标题')})")
            except Exception as e:
                print(f"[Cluster] ❌ 推送失败: {e}")
                # 失败的章节标记为错误
                results[idx] = {
                    'title': chapter.get('name', f'第{idx+1}章'),
                    'content': f'推送失败: {str(e)}'
                }
                completed.add(idx)
        
        print(f"[Cluster] ⏳ 等待节点处理 {len(task_mapping)} 个任务...")
        
        # 轮询等待结果
        start_time = time.time()
        timeout = 300  # 5分钟超时
        check_interval = 0.5  # 每0.5秒检查一次
        
        while task_mapping and (time.time() - start_time < timeout):
            # 检查暂停标志
            if task.get('paused'):
                print(f"[Cluster] 任务 {task_id} 已暂停")
                break
            
            completed_tasks = []
            
            for task_uuid, idx in list(task_mapping.items()):
                result_key = f"crawler:result:{task_uuid}"
                res = cluster_manager.r.get(result_key)
                
                if res:
                    # 解析结果
                    json_res = json.loads(res)
                    cluster_manager.r.delete(result_key)  # 读完即焚
                    
                    if json_res.get('status') == 'success':
                        data = json_res.get('data')
                        if data and data.get('content'):
                            results[idx] = {
                                'title': data.get('title', '无标题'),
                                'content': '\n'.join(data['content']) if isinstance(data['content'], list) else data['content']
                            }
                            completed.add(idx)
                            print(f"[Cluster] ✅ 完成: 第{idx+1}章 (Worker: {json_res.get('worker_uuid', 'unknown')[:8]}...)")
                        else:
                            results[idx] = {
                                'title': chapters[idx].get('name', f'第{idx+1}章'),
                                'content': '爬取结果为空'
                            }
                            completed.add(idx)
                    else:
                        # 失败的章节
                        results[idx] = {
                            'title': chapters[idx].get('name', f'第{idx+1}章'),
                            'content': f'爬取失败: {json_res.get("msg", "未知错误")}'
                        }
                        completed.add(idx)
                        print(f"[Cluster] ❌ 失败: 第{idx+1}章")
                    
                    completed_tasks.append(task_uuid)
            
            # 移除已完成的任务
            for task_uuid in completed_tasks:
                del task_mapping[task_uuid]
            
            # 更新进度
            if completed_tasks:
                task['current'] = len(completed)
                task['completed_chapters'] = list(completed)
                task['results'] = results
                
                # 每完成 10 章保存一次
                if len(completed) % 10 == 0:
                    self._save_task(task_id)
            
            # 如果还有待处理任务，等待一会再检查
            if task_mapping:
                time.sleep(check_interval)
        
        # 超时或暂停后，标记剩余章节为超时
        if task_mapping:
            print(f"[Cluster] ⚠️ {len(task_mapping)} 个章节超时或被暂停")
            for task_uuid, idx in task_mapping.items():
                if idx not in completed:
                    results[idx] = {
                        'title': chapters[idx].get('name', f'第{idx+1}章'),
                        'content': '爬取超时或被暂停'
                    }
                    completed.add(idx)
        
        print(f"[Cluster] 🎉 集群爬取完成: {len(completed)}/{len(chapters)} 章")
        return results
    
    def _generate_txt(self, task, results):
        """生成 TXT 文件"""
        filepath = os.path.join(DL_DIR, task['filename'])
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"{task['book_name']}\n")
            f.write("=" * 50 + "\n\n")
            
            for chapter in results:
                if chapter:
                    f.write(f"\n\n{'=' * 50}\n")
                    f.write(f"{chapter['title']}\n")
                    f.write(f"{'=' * 50}\n\n")
                    f.write(chapter['content'])
                    f.write("\n\n")
    
    def _generate_epub(self, task, results):
        """生成 EPUB 文件（需要 ebooklib）"""
        try:
            from ebooklib import epub
        except ImportError:
            raise Exception("需要安装 ebooklib 库: pip install ebooklib")
        
        book = epub.EpubBook()
        metadata = task.get('metadata', {})
        
        # 设置元数据
        book.set_identifier(hashlib.md5(task['book_name'].encode()).hexdigest())
        book.set_title(task['book_name'])
        book.set_language(metadata.get('language', 'zh'))
        
        if metadata.get('author'):
            book.add_author(metadata['author'])
        
        if metadata.get('description'):
            book.add_metadata('DC', 'description', metadata['description'])
        
        # 添加封面（如果提供）
        if metadata.get('cover_path') and os.path.exists(metadata['cover_path']):
            with open(metadata['cover_path'], 'rb') as f:
                book.set_cover('cover.jpg', f.read())
        
        # 创建章节
        chapters_epub = []
        spine = ['nav']
        
        for i, chapter_data in enumerate(results):
            if not chapter_data:
                continue
                
            chapter = epub.EpubHtml(
                title=chapter_data['title'],
                file_name=f'chapter_{i+1}.xhtml',
                lang='zh'
            )
            
            # 添加章节内容
            content = f'<h1>{chapter_data["title"]}</h1>'
            content += '<div>' + chapter_data['content'].replace('\n', '</p><p>') + '</div>'
            chapter.content = content
            
            book.add_item(chapter)
            chapters_epub.append(chapter)
            spine.append(chapter)
        
        # 添加目录
        book.toc = tuple(chapters_epub)
        
        # 添加导航文件
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        
        # 设置 spine
        book.spine = spine
        
        # 写入文件
        filepath = os.path.join(DL_DIR, task['filename'])
        epub.write_epub(filepath, book, {})
    
    def get_status(self, task_id):
        """获取任务状态"""
        return self.exports.get(task_id)
class ClusterManager:
    def __init__(self):
        self.redis_url = os.environ.get('REDIS_URL')
        self.use_redis = False
        self.nodes = {} # 内存 fallback
        self.r = None

        if self.redis_url:
            try:
                self.r = redis.from_url(self.redis_url, decode_responses=True)
                self.r.ping() # 测试连接
                self.use_redis = True
                print("✅ [Cluster] Redis 连接成功，集群模式已就绪")
            except Exception as e:
                print(f"⚠️ [Cluster] Redis 连接失败 ({e})，降级为内存模式")
        else:
            print("ℹ️ [Cluster] 未配置 REDIS_URL，使用内存模式 (重启后节点信息丢失)")
    # managers.py -> ClusterManager 类

    # ... (前面的方法保持不变) ...
    def record_latency(self, url, worker_uuid, latency_ms):
        """
        自适应权重记录：EWMA平滑 + 异常值过滤 + 熔断保护
        Redis Key: crawler:latency:{domain}  (Hash结构)
        """
        if not self.use_redis or not url: return
        
        try:
            from urllib.parse import urlparse
            import statistics
            
            domain = urlparse(url).netloc
            if not domain: return

            key = f"crawler:latency:{domain}"
            
            # === 1. 异常值过滤（防止单次超时污染权重） ===
            # 获取该域名下所有节点的延迟，用于统计分析
            all_latencies_raw = self.r.hgetall(key)
            all_latencies = [float(v) for v in all_latencies_raw.values() if v]
            
            # 如果有足够样本（至少3个节点），进行异常检测
            if len(all_latencies) >= 3:
                mean = statistics.mean(all_latencies)
                try:
                    std = statistics.stdev(all_latencies)
                except:
                    std = mean * 0.3  # 如果标准差计算失败，用30%作为估计
                
                # 检测异常值：超过均值+3倍标准差视为异常
                threshold = mean + 3 * std
                if latency_ms > threshold:
                    # 钳制到均值+2倍标准差（保留一定惩罚，但不至于过度）
                    clamped = mean + 2 * std
                    print(f"[Latency] 异常值过滤: {domain} {worker_uuid} {latency_ms}ms -> {clamped:.0f}ms (均值{mean:.0f})")
                    latency_ms = clamped
            
            # === 2. 熔断保护（超时直接降权） ===
            if latency_ms > 15000:  # 超过15秒视为严重超时
                print(f"[Latency] 熔断触发: {domain} {worker_uuid} {latency_ms}ms")
                latency_ms = 15000  # 钳制到15秒上限
            
            # === 3. EWMA平滑处理（核心算法） ===
            old_latency_str = self.r.hget(key, worker_uuid)
            
            if old_latency_str:
                old_latency = float(old_latency_str)
                # α = 0.15：历史占85%，新数据占15%（保守策略，适合不稳定网络）
                alpha = 0.15
                smoothed_latency = alpha * latency_ms + (1 - alpha) * old_latency
            else:
                # 冷启动：第一次记录直接使用
                smoothed_latency = latency_ms
            
            # === 4. 保存到Redis ===
            self.r.hset(key, worker_uuid, int(smoothed_latency))
            
            # 设置过期时间7天（网络环境会变化）
            self.r.expire(key, 7 * 86400)
            
            # 调试日志（生产环境可注释）
            # print(f"[Latency] {domain} {worker_uuid}: {latency_ms}ms -> {smoothed_latency:.0f}ms")
            
        except Exception as e:
            print(f"[Latency] 记录失败: {e}")
    def _get_speed_coefficient(self, latency):
        """
        延迟转权重系数（平滑曲线，避免阶梯式跳变）
        公式: weight = baseline / max(latency, min_latency)
        """
        # 错误熔断：之前报错过的节点给极低权重
        if latency < 0:
            return 0.05
        
        # 基准延迟：1000ms（1秒）视为标准水平
        baseline = 1000
        
        # 最小延迟保护：避免除零，最小按100ms计算
        safe_latency = max(latency, 100)
        
        # 计算权重比例（反比关系：延迟越低权重越高）
        ratio = baseline / safe_latency
        
        # 限制在合理区间 [0.1, 3.0]
        # - 最快节点（100ms）最多3倍权重
        # - 最慢节点（10s+）最低0.1倍权重
        coefficient = max(0.1, min(3.0, ratio))
        
        return coefficient
    def get_speed_multiplier(self, url, worker_uuid):
        """
        [新增] 计算速度加权系数
        """
        if not self.use_redis or not url: return 1.0
        
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            
            # 获取该节点的历史延迟
            latency_str = self.r.hget(f"crawler:latency:{domain}", worker_uuid)
            
            if not latency_str: return 1.0 # 无记录，中立
            
            latency = int(latency_str)
            
            if latency < 0: return 0.1    # 之前报错过，极刑
            if latency < 200: return 2.0  # 极速
            if latency < 500: return 1.5  # 很快
            if latency < 1000: return 1.2 # 还行
            if latency < 2000: return 1.0 # 一般
            if latency < 5000: return 0.8 # 有点慢
            return 0.5                    # 太慢了
            
        except: return 1.0
    def start_speed_test(self, url):
        """发布测速广播"""
        if not self.use_redis: return None
        
        import uuid
        import time
        test_id = str(uuid.uuid4())
        
        # 1. 存入指令
        cmd = {
            "id": test_id,
            "url": url,
            "timestamp": time.time()
        }
        
        # 存入元数据 (用于前端判断进度)
        active_nodes = self.get_active_nodes()
        meta = {
            "total": len(active_nodes),
            "start_time": time.time(),
            "url": url
        }
        self.r.setex(f"crawler:speedtest:meta:{test_id}", 300, json.dumps(meta))
        
        # 2. 发布广播指令
        self.r.setex("crawler:cmd:speedtest", 60, json.dumps(cmd))
        
        # 3. [核心修复] 清理所有相关的 Redis Key
        # dispatched: 记录已领任务的节点 (防止重复领)
        # results: 记录结果数据
        self.r.delete(f"crawler:speedtest:dispatched:{test_id}")
        self.r.delete(f"crawler:speedtest:results:{test_id}")
        
        return test_id
    def should_dispatch_speedtest(self, worker_uuid):
        """检查并分发测速任务 (防抖动版)"""
        if not self.use_redis: return None
        
        # 1. 获取全局指令
        cmd_raw = self.r.get("crawler:cmd:speedtest")
        if not cmd_raw: return None
        
        cmd = json.loads(cmd_raw)
        test_id = cmd['id']
        
        # 2. [核心修复] 检查“已下发”名单，而不是“已完成”名单
        dispatch_key = f"crawler:speedtest:dispatched:{test_id}"
        
        # 如果该节点已经在“已下发”名单里，直接忽略
        if self.r.sismember(dispatch_key, worker_uuid):
            return None 
            
        # 3. [核心修复] 立即标记为“已下发” (先斩后奏)
        # 在任务发出的一瞬间就标记，防止 Worker 还没测完又来请求
        self.r.sadd(dispatch_key, worker_uuid)
        self.r.expire(dispatch_key, 60) # 60秒后自动过期
            
        return cmd
    def get_speed_test_results(self, test_id):
        """获取结果并判断状态"""
        if not self.use_redis: return {"status": "error"}
        
        # 1. 获取元数据
        meta_json = self.r.get(f"crawler:speedtest:meta:{test_id}")
        if not meta_json:
            return {"state": "expired", "data": []}
            
        meta = json.loads(meta_json)
        total_expected = meta['total']
        start_time = meta['start_time']
        
        # 2. 获取当前结果
        raw_results = self.r.hgetall(f"crawler:speedtest:results:{test_id}")
        results = []
        for k, v in raw_results.items():
            try: results.append(json.loads(v))
            except: pass
            
        # 3. 核心：判断状态
        # 状态：running (进行中), finished (全收齐), timeout (超时)
        
        current_count = len(results)
        elapsed = time.time() - start_time
        
        if current_count >= total_expected:
            state = "finished" # 全齐了
        elif elapsed > 5:
            state = "timeout"  # 超过5秒了，强制结束
        else:
            state = "running"
            
        return {
            "state": state,
            "total": total_expected,
            "received": current_count,
            "elapsed": round(elapsed, 1),
            "data": results
        }
    def update_heartbeat(self, node_data, real_ip):
        """更新节点心跳"""
        uuid = node_data['uuid']
        
        # 自动补全 IP：如果 Worker 没配 public_url，用 real_ip 补全
        if not node_data['config'].get('public_url'):
            port = node_data['config']['port']
            node_data['config']['public_url'] = f"http://{real_ip}:{port}"
        
        # 记录最后更新时间
        node_data['last_seen'] = time.time()

        if self.use_redis:
            try:
                # 30秒过期
                self.r.setex(f"crawler:node:{uuid}", 30, json.dumps(node_data))
            except Exception as e:
                print(f"❌ [Cluster] Redis Write Error: {e}")
        else:
            self.nodes[uuid] = node_data

    # managers.py -> ClusterManager 类

    def get_active_nodes(self):
        """获取所有节点并进行初步清洗"""
        nodes = []
        if self.use_redis:
            try:
                # 获取所有 crawler:node:* 的键
                keys = self.r.keys("crawler:node:*")
                if keys:
                    # 批量获取
                    vals = self.r.mget(keys)
                    for v in vals:
                        if v:
                            nodes.append(json.loads(v))
            except Exception as e:
                print(f"❌ [Cluster] Redis Read Error: {e}")
                return []
        else:
            # 内存模式：清理过期节点
            now = time.time()
            # 过滤掉超过 40 秒没心跳的节点 (给一点宽容度)
            self.nodes = {k: v for k, v in self.nodes.items() if now - v.get('last_seen', 0) < 40}
            nodes = list(self.nodes.values())
        
        return nodes

    def select_best_node(self, target_url=None):
        """
        [重构] 智能路由算法 (负载 + 区域 + 域名级速度)
        """
        nodes = self.get_active_nodes()
        if not nodes: return None

        best_node = None
        highest_score = -9999
        
        # 预先提取域名
        target_domain = None
        if target_url:
            try:
                from urllib.parse import urlparse
                target_domain = urlparse(target_url).netloc
            except: pass

        for node in nodes:
            cfg = node['config']
            status = node['status']
            uuid = node['uuid']
            
            # 1. 熔断机制：满载不接客
            if status['current_tasks'] >= cfg['max_tasks']: 
                continue

            # 2. 基础资源分 (0~100)
            # 逻辑：(1 - 负载率) * 100
            load_ratio = status['current_tasks'] / cfg['max_tasks']
            base_score = (1 - load_ratio) * 100
            
            # CPU 惩罚 (如果 CPU > 80%，分数大减)
            if status['cpu'] > 80: base_score *= 0.5

            # 3. 区域加权 (粗略筛选)
            region_coef = 1.0
            if target_url:
                is_cn_site = any(x in target_url for x in ['.cn', 'biqu', 'gongzicp'])
                if is_cn_site and cfg['region'] == 'CN': region_coef = 1.2
                if not is_cn_site and cfg['region'] == 'GLOBAL': region_coef = 1.2
            
            # 4. [核心] 域名级速度加权 (精细筛选)
            speed_coef = 1.0
            if target_domain and self.use_redis:
                # 查 Redis: crawler:latency:www.google.com -> {uuid: 150}
                latency_str = self.r.hget(f"crawler:latency:{target_domain}", uuid)
                if latency_str:
                    speed_coef = self._get_speed_coefficient(int(latency_str))

            # === 最终得分公式 ===
            # 资源分 * 区域系数 * 速度系数
            final_score = base_score * region_coef * speed_coef
            
            # 调试日志 (开发时取消注释)
            # print(f"Node: {cfg['name']} | Base: {base_score:.0f} | Reg: {region_coef} | Spd: {speed_coef} ({target_domain}) -> Final: {final_score:.1f}")

            if final_score > highest_score:
                highest_score = final_score
                best_node = node

        return best_node


class TaskManager:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.tasks = {} # task_id -> {status, result, error, created_at, message}
        self.redis_url = os.environ.get('REDIS_URL')
        self.use_redis = False
        self.r = None
        if self.redis_url:
            try:
                self.r = redis.from_url(self.redis_url, decode_responses=True)
                self.r.ping()
                self.use_redis = True
                print("✅ [TaskMgr] Redis 连接成功，任务状态持久化启用")
            except Exception as e:
                print(f"⚠️ [TaskMgr] Redis 连接失败 ({e})，降级为内存模式")
        else:
            print("ℹ️ [TaskMgr] 未配置 REDIS_URL，使用内存任务状态")

    def _redis_key(self, task_id):
        return f"task:{task_id}"

    def _set_task(self, task_id, data, expire=3600):
        if not self.use_redis:
            self.tasks[task_id] = data
            return
        try:
            payload = data.copy()
            # 序列化 result
            if 'result' in payload:
                payload['result'] = json.dumps(payload['result'], ensure_ascii=False) if payload['result'] is not None else ''
            self.r.hset(self._redis_key(task_id), mapping=payload)
            self.r.expire(self._redis_key(task_id), expire)
        except Exception as e:
            print(f"[TaskMgr] Redis 写入失败: {e}")
            self.tasks[task_id] = data

    def _get_task(self, task_id):
        if not self.use_redis:
            return self.tasks.get(task_id)
        try:
            data = self.r.hgetall(self._redis_key(task_id))
            if not data:
                return None
            # 反序列化 result
            result = data.get('result', '')
            if result:
                try:
                    data['result'] = json.loads(result)
                except Exception:
                    data['result'] = result
            else:
                data['result'] = None
            # 类型修正
            if 'progress' in data:
                try: data['progress'] = int(data['progress'])
                except: pass
            if 'created_at' in data:
                try: data['created_at'] = float(data['created_at'])
                except: pass
            return data
        except Exception as e:
            print(f"[TaskMgr] Redis 读取失败: {e}")
            return self.tasks.get(task_id)

    def submit(self, func, *args, **kwargs):
        task_id = str(uuid.uuid4())
        task_data = {
            "status": "pending",
            "progress": 0,
            "result": None,
            "error": None,
            "message": "Waiting...",
            "created_at": time.time()
        }
        self._set_task(task_id, task_data)
        # 将 update_callback 注入到 kwargs 中，供 func 调用
        kwargs['_task_update_cb'] = self._create_updater(task_id)
        self.executor.submit(self._worker, task_id, func, *args, **kwargs)
        return task_id

    def _create_updater(self, task_id):
        def updater(progress=None, msg=None, result_delta=None):
            data = self._get_task(task_id) or {}
            if progress is not None: data["progress"] = progress
            if msg is not None: data["message"] = msg
            # Support intermediate results (list only for now)
            if result_delta is not None:
                if data.get("result") is None: data["result"] = []
                if isinstance(data.get("result"), list) and isinstance(result_delta, list):
                    data["result"].extend(result_delta)
            self._set_task(task_id, data)
        return updater

    def _worker(self, task_id, func, *args, **kwargs):
        print(f"[Task] Starting {task_id}")
        callback = kwargs.pop('_task_update_cb', None)
        try:
            data = self._get_task(task_id) or {}
            data["status"] = "running"
            self._set_task(task_id, data)
            
            # Pass user_callback if the function accepts it
            # We assume func signature might allow **kwargs or explicit 'callback'
            # But to be safe, we only pass it if 'callback' is in kwargs, which we handle in submit wrapper?
            # actually, let's just pass it as 'callback' arg if the user function expects it.
            # However, for simplicity, I'll inject it into kwargs and let the target function `pop` it if it needs.
            
            # Re-inject for the function to use
            kwargs['callback'] = callback
            
            # Execute
            res = func(*args, **kwargs)
            
            # Only overwrite result if it's returned and task result is not managed incrementally
            # Or assume the function returns the FINAL COMPLETE result.
            data = self._get_task(task_id) or {}
            data["result"] = res
            data["status"] = "completed"
            data["progress"] = 100
            self._set_task(task_id, data)
            print(f"[Task] Completed {task_id}")
        except Exception as e:
            print(f"[Task Error] {task_id}: {e}")
            import traceback
            traceback.print_exc()
            data = self._get_task(task_id) or {}
            data["status"] = "failed"
            data["error"] = str(e)
            data["message"] = "Task failed"
            self._set_task(task_id, data)
    def get_status(self, task_id):
        # Debug: print keys if not found
        t = self._get_task(task_id)
        if not t:
            print(f"[TaskMgr] Checking {task_id} -> Not Found")
        return t

    def cleanup(self):
        # 简单清理超过1小时的任务
        if self.use_redis:
            return
        now = time.time()
        to_del = [k for k,v in self.tasks.items() if now - v['created_at'] > 3600]
        for k in to_del: del self.tasks[k]

task_manager = TaskManager()

# 实例化
cluster_manager = ClusterManager()
# ==========================================
# 6. 初始化所有单例
# ==========================================
role_manager = RoleManager()
offline_manager = OfflineBookManager()
cache = CacheManager()
db = IsolatedDB()
booklist_manager = IsolatedBooklistManager()
downloader = DownloadManager()
tag_manager = IsolatedTagManager()
stats_manager = IsolatedStatsManager()
history_manager = HistoryManager()
update_manager = UpdateManager()
update_sub_manager = UpdateRecordManager()
exporter = ExportManager()

# 注入到 shared 供装饰器使用
shared.role_manager_instance = role_manager

class MemoManager:
    """桌面备忘录管理"""
    
    def __init__(self):
        self._ensure_table()
    
    def _ensure_table(self):
        """确保备忘录表存在"""
        try:
            with get_db() as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS user_memos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '未命名备忘录',
                    content TEXT,
                    tags TEXT,  -- JSON 数组存储标签
                    is_pinned BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
                
                # 历史版本表（每次保存自动快照）
                conn.execute('''CREATE TABLE IF NOT EXISTS memo_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memo_id INTEGER NOT NULL,
                    content TEXT,
                    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(memo_id) REFERENCES user_memos(id)
                )''')
                conn.commit()
        except Exception as e:
            print(f"[MemoManager] 建表失败: {e}")
    
    def get_all_memos(self, username):
        """获取用户所有备忘录"""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM user_memos WHERE username=? ORDER BY is_pinned DESC, updated_at DESC",
                (username,)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def get_memo(self, memo_id):
        """获取单条备忘录"""
        with get_db() as conn:
            row = conn.execute("SELECT * FROM user_memos WHERE id=?", (memo_id,)).fetchone()
            return dict(row) if row else None
    
    def save_memo(self, username, memo_id=None, title=None, content=None, tags=None):
        """保存备忘录（新建或更新）"""
        with get_db() as conn:
            if memo_id:
                # 更新现有备忘录
                conn.execute("""
                    UPDATE user_memos 
                    SET title=COALESCE(?, title), 
                        content=COALESCE(?, content),
                        tags=COALESCE(?, tags),
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (title, content, tags, memo_id))
                
                # 保存历史版本
                if content:
                    conn.execute(
                        "INSERT INTO memo_history (memo_id, content) VALUES (?, ?)",
                        (memo_id, content)
                    )
            else:
                # 新建备忘录
                cursor = conn.execute("""
                    INSERT INTO user_memos (username, title, content, tags)
                    VALUES (?, ?, ?, ?)
                """, (username, title or '新备忘录', content or '', tags or '[]'))
                memo_id = cursor.lastrowid
            
            conn.commit()
            return memo_id
    
    def delete_memo(self, memo_id):
        """删除备忘录"""
        with get_db() as conn:
            conn.execute("DELETE FROM user_memos WHERE id=?", (memo_id,))
            conn.execute("DELETE FROM memo_history WHERE memo_id=?", (memo_id,))
            conn.commit()
    
    def toggle_pin(self, memo_id):
        """置顶/取消置顶"""
        with get_db() as conn:
            conn.execute("""
                UPDATE user_memos 
                SET is_pinned = NOT is_pinned 
                WHERE id=?
            """, (memo_id,))
            conn.commit()
    
    def search_memos(self, username, keyword):
        """搜索备忘录"""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM user_memos 
                WHERE username=? AND (title LIKE ? OR content LIKE ?)
                ORDER BY updated_at DESC
            """, (username, f'%{keyword}%', f'%{keyword}%')).fetchall()
            return [dict(row) for row in rows]

# 全局实例
memo_manager = MemoManager()