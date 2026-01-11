import os
import json
import sqlite3
import hashlib
import time
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

class IsolatedDB:
    def _get_db_conn(self):
        # 这是一个辅助方法，用于非 Flask 请求上下文（如后台线程）获取连接
        # 在 web 请求中应优先使用 get_db()
        username = session.get('user', {}).get('username', 'default_user')
        db_path = os.path.join(USER_DATA_DIR, f"{username}.sqlite")
        conn = sqlite3.connect(db_path)
        return conn
    # managers.py -> IsolatedDB 类中

    def rename_key(self, old_key, new_key):
        if not old_key or not new_key: return {"status": "error", "message": "Key cannot be empty"}
        u = get_current_user()
        
        try:
            with get_db() as conn:
                # 1. 检查新 Key 是否已存在
                exists = conn.execute("SELECT 1 FROM user_books WHERE username=? AND book_key=?", (u, new_key)).fetchone()
                if exists:
                    return {"status": "error", "message": f"目标 Key [{new_key}] 已存在"}

                # 2. 更新主表 (user_books)
                conn.execute("UPDATE user_books SET book_key=? WHERE username=? AND book_key=?", (new_key, u, old_key))
                
                # 3. 更新影子元数据 (key:meta)
                conn.execute("UPDATE user_books SET book_key=? WHERE username=? AND book_key=?", 
                           (f"{new_key}:meta", u, f"{old_key}:meta"))
                
                # 4. 更新历史版本表 (book_history)
                conn.execute("UPDATE book_history SET book_key=? WHERE username=? AND book_key=?", (new_key, u, old_key))
                
                conn.commit()

            # 5. 更新 JSON 模块中的引用 (Tags 和 Updates)
            # 处理标签
            tags_data = tag_manager.load(u)
            if old_key in tags_data:
                tags_data[new_key] = tags_data.pop(old_key)
                tag_manager.save(tags_data, u)
            
            # 处理追更状态
            updates_data = update_manager.load(u)
            if old_key in updates_data:
                updates_data[new_key] = updates_data.pop(old_key)
                update_manager.save(updates_data, u)

            # 6. 处理书单 (Booklists)
            bl_data = booklist_manager.load(u)
            changed = False
            for lid in bl_data:
                for b in bl_data[lid].get('books', []):
                    if b['key'] == old_key:
                        b['key'] = new_key
                        changed = True
            if changed:
                booklist_manager.save(bl_data, u)

            return {"status": "success", "message": f"已将 [{old_key}] 重命名为 [{new_key}]"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    def _ensure_history_table(self):
        """确保历史记录表存在"""
        try:
            with get_db() as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS book_history (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                username TEXT NOT NULL,
                                book_key TEXT NOT NULL,
                                value TEXT,
                                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )''')
                conn.commit()
        except: pass

    def add_version(self, key, value):
        """添加一个历史版本，并保留最近 5 条"""
        self._ensure_history_table()
        u = get_current_user()
        try:
            with get_db() as conn:
                # 1. 插入新记录
                conn.execute("INSERT INTO book_history (username, book_key, value) VALUES (?, ?, ?)", (u, key, value))
                
                # 2. 清理旧记录 (只保留最近 5 条)
                # 逻辑：找出该用户该书的所有记录ID，按时间倒序排列，跳过前5个，剩下的删掉
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
        except Exception as e:
            print(f"[DB] Add Version Error: {e}")
            return False

    def get_versions(self, key):
        """获取某本书的最近 5 个版本"""
        self._ensure_history_table()
        u = get_current_user()
        try:
            with get_db() as conn:
                cursor = conn.execute("SELECT value, recorded_at FROM book_history WHERE username=? AND book_key=? ORDER BY recorded_at DESC", (u, key))
                return [{"value": row[0], "time": row[1]} for row in cursor.fetchall()]
        except: return []

    # === 原有的基础方法 (保持不变，但 update/insert 稍微调整逻辑在路由层做) ===
    def insert(self, key, value):
        if not key: return {"status": "error", "message": "Key cannot be empty"}
        u = get_current_user()
        try:
            with get_db() as conn:
                conn.execute("INSERT OR REPLACE INTO user_books (username, book_key, value) VALUES (?, ?, ?)", (u, key, value))
                conn.commit()
            return {"status": "success", "message": f"Saved: {key}", "data": {key: value}}
        except Exception as e: return {"status": "error", "message": str(e)}

    def update(self, key, value): return self.insert(key, value)

    def remove(self, key):
        u = get_current_user()
        try:
            with get_db() as conn:
                conn.execute("DELETE FROM user_books WHERE username=? AND book_key=?", (u, key))
                # 顺便把历史记录也删了？通常保留历史比较安全，这里选择保留
                conn.commit()
            return {"status": "success", "message": f"Removed: {key}"}
        except Exception as e: return {"status": "error", "message": str(e)}

    def list_all(self):
        u = get_current_user()
        try:
            conn = get_db()
            cursor = conn.execute("SELECT book_key, value FROM user_books WHERE username=? AND book_key NOT LIKE '@%' ORDER BY updated_at DESC", (u,))
            data = {row[0]: row[1] for row in cursor.fetchall()}
            return {"status": "success", "data": data}
        except Exception as e: return {"status": "error", "message": str(e)}

    def find(self, term):
        u = get_current_user()
        try:
            t = f'%{term}%'
            conn = get_db()
            cursor = conn.execute("SELECT book_key, value FROM user_books WHERE username=? AND (book_key LIKE ? OR value LIKE ?)", (u, t, t))
            data = {row[0]: row[1] for row in cursor.fetchall()}
            return {"status": "success", "data": data}
        except Exception as e: return {"status": "error", "message": str(e)}

    def get_val(self, key):
        u = get_current_user()
        try:
            conn = get_db()
            row = conn.execute("SELECT value FROM user_books WHERE username=? AND book_key=?", (u, key)).fetchone()
            return row[0] if row else None
        except: return None
    
    def rollback(self): return {"status": "error", "message": "Use version history instead"}
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

# 注入到 shared 供装饰器使用
shared.role_manager_instance = role_manager