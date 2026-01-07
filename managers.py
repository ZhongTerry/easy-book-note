import os
import json
import sqlite3
import hashlib
import time
import re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import session
from shared import USER_DATA_DIR, CACHE_DIR, DL_DIR
import shared

# ==========================================
# 1. 角色管理 (RoleManager)
# ==========================================
class RoleManager:
    def __init__(self):
        self.config_path = os.path.join(USER_DATA_DIR, "roles.json")
        if not os.path.exists(self.config_path):
            self.save({"admins": [], "pros": []})

    def load(self):
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {"admins": [], "pros": []}

    def save(self, data):
        with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(data, f)

    def get_role(self, username):
        if not username: return "guest"
        data = self.load()
        if username in data.get("admins", []): return "admin"
        if username in data.get("pros", []): return "pro"
        return "user"

    def set_role(self, username, role):
        data = self.load()
        if username in data["admins"]: data["admins"].remove(username)
        if username in data["pros"]: data["pros"].remove(username)
        
        if role == "admin": data["admins"].append(username)
        elif role == "pro": data["pros"].append(username)
        self.save(data)

# ==========================================
# 2. 追更管理 (UpdateManager) - 修复版
# ==========================================
class UpdateManager:
    def __init__(self):
        self._path_func = lambda: os.path.join(USER_DATA_DIR, f"{session.get('user', {}).get('username', 'default')}_updates.json")

    def _get_path(self, username=None):
        u = username if username else session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_updates.json")

    def load(self, username=None):
        path = self._get_path(username)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: 
                    c = f.read().strip()
                    return json.loads(c) if c else {}
            except: pass
        return {}

    def save(self, data, username=None):
        path = self._get_path(username)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def set_update(self, book_key, latest_info, username=None):
        data = self.load(username)
        
        # [兼容性修复] 兼容 title/latest_title 两种键名
        title = latest_info.get('title') or latest_info.get('latest_title') or "未知章节"
        url = latest_info.get('url') or latest_info.get('latest_url') or ""
        total = latest_info.get('total_chapters') or latest_info.get('total') or 0
        chap_id = latest_info.get('id') or latest_info.get('latest_id') or -1

        data[book_key] = {
            "latest_title": title,
            "latest_url": url,
            "latest_id": chap_id,
            "total": total,
            "last_check": int(time.time()),
            "status_text": latest_info.get('status_text', ""),
            "unread_count": latest_info.get('unread_count', 0),
            "toc_url": latest_info.get('toc_url') # 缓存目录链接，方便下次快速检查
        }
        self.save(data, username)
        
    def get_update(self, book_key):
        return self.load().get(book_key)

    # [新增] 纯数字更新逻辑 (供阅读时快速更新状态)
    def update_progress(self, book_key, new_unread_count, status_text, username=None):
        data = self.load(username)
        if book_key in data:
            data[book_key]['unread_count'] = new_unread_count
            data[book_key]['status_text'] = status_text
            self.save(data, username)

# ==========================================
# 3. 离线书籍管理 (OfflineBookManager)
# ==========================================
class OfflineBookManager:
    def __init__(self):
        self.offline_dir = os.path.join(USER_DATA_DIR, "offline_books")
        if not os.path.exists(self.offline_dir): os.makedirs(self.offline_dir)

    def _get_book_path(self, book_key):
        return os.path.join(self.offline_dir, f"{book_key}.json")

    def is_downloaded(self, book_key):
        return os.path.exists(self._get_book_path(book_key))

    def save_book(self, book_key, chapters_data):
        with open(self._get_book_path(book_key), 'w', encoding='utf-8') as f:
            json.dump(chapters_data, f, ensure_ascii=False)

    def get_chapter(self, book_key, chapter_url):
        if not self.is_downloaded(book_key): return None
        try:
            with open(self._get_book_path(book_key), 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get(chapter_url)
        except: return None

# ==========================================
# 4. 缓存管理 (CacheManager)
# ==========================================
class CacheManager:
    def __init__(self, ttl=604800): 
        self.cache_dir = CACHE_DIR
        self.ttl = ttl 
    
    def _get_filename(self, url):
        hash_object = hashlib.md5(url.encode('utf-8'))
        return os.path.join(self.cache_dir, hash_object.hexdigest() + ".json")

    def get(self, url):
        filepath = self._get_filename(url)
        if not os.path.exists(filepath): return None
        if time.time() - os.path.getmtime(filepath) > self.ttl: return None
        try:
            with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
        except: return None

    def set(self, url, data):
        filepath = self._get_filename(url)
        try:
            with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
        except Exception as e: print(f"[Cache] Write Error: {e}")

    def cleanup_expired(self):
        print("[Cache] 开始清理过期缓存...")
        now = time.time()
        count, size = 0, 0
        for f in os.listdir(self.cache_dir):
            fp = os.path.join(self.cache_dir, f)
            if os.path.isfile(fp):
                if now - os.path.getmtime(fp) > self.ttl:
                    try:
                        size += os.path.getsize(fp)
                        os.remove(fp)
                        count += 1
                    except: pass
        return count, size / (1024*1024)

# ==========================================
# 5. 书单管理 (BooklistManager) - 增强鲁棒性
# ==========================================
class IsolatedBooklistManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_booklists.json")

    def load(self):
        path = self._get_path()
        if not os.path.exists(path): return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: return {}
                return json.loads(content)
        except Exception as e:
            print(f"[Warn] 书单文件损坏: {e}")
            return {}

    def save(self, data):
        with open(self._get_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
        return data
    
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

# ==========================================
# 6. KV 数据库 (SQLite)
# ==========================================
class IsolatedDB:
    def _get_db_conn(self):
        username = session.get('user', {}).get('username', 'default_user')
        db_path = os.path.join(USER_DATA_DIR, f"{username}.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
        return conn

    def insert(self, key, value):
        if not key: return {"status": "error", "message": "Key cannot be empty"}
        try:
            with self._get_db_conn() as conn:
                conn.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)", (key, value))
            return {"status": "success", "message": f"Saved: {key}", "data": {key: value}}
        except Exception as e: return {"status": "error", "message": str(e)}

    def update(self, key, value): return self.insert(key, value)

    def remove(self, key):
        try:
            with self._get_db_conn() as conn: conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            return {"status": "success", "message": f"Removed: {key}"}
        except Exception as e: return {"status": "error", "message": str(e)}

    def list_all(self):
        try:
            with self._get_db_conn() as conn:
                cursor = conn.execute("SELECT key, value FROM kv_store WHERE key NOT LIKE '@%' ORDER BY key DESC")
                data = {row[0]: row[1] for row in cursor.fetchall()}
            return {"status": "success", "data": data}
        except Exception as e: return {"status": "error", "message": str(e)}

    def find(self, term):
        try:
            with self._get_db_conn() as conn:
                t = f'%{term}%'
                cursor = conn.execute("SELECT key, value FROM kv_store WHERE key LIKE ? OR value LIKE ?", (t, t))
                data = {row[0]: row[1] for row in cursor.fetchall()}
            return {"status": "success", "data": data}
        except Exception as e: return {"status": "error", "message": str(e)}

    def get_val(self, key):
        try:
            with self._get_db_conn() as conn:
                row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
                return row[0] if row else None
        except: return None
    
    def rollback(self): return {"status": "error", "message": "Not implemented in SQLite"}

# ==========================================
# 7. 下载管理器 (DownloadManager)
# ==========================================
class DownloadManager:
    def __init__(self):
        self.downloads = {}
        self.executor = ThreadPoolExecutor(max_workers=5)

    def start_download(self, book_name, chapters, crawler_instance):
        task_id = hashlib.md5((book_name + str(time.time())).encode()).hexdigest()
        self.downloads[task_id] = {
            'book_name': book_name, 'total': len(chapters), 'current': 0, 
            'status': 'running', 'filename': f"{re.sub(r'[\\/*?:|<>]', '', book_name)}.txt"
        }
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
                    results[idx] = f"\n\n=== {t} ===\n\n" + '\n'.join(c)
                except Exception as e: results[idx] = f"\n\nError: {e}"
                task['current'] += 1
        
        try:
            with open(os.path.join(DL_DIR, task['filename']), 'w', encoding='utf-8') as f:
                f.write(f"=== {task['book_name']} ===\n")
                for r in results: f.write(r or "")
            task['status'] = 'completed'
        except Exception as e:
            task['status'] = 'error'; task['error_msg'] = str(e)

    def _fetch_worker(self, url, crawler):
        data = crawler.run(url)
        if data and data['content']: return data['content'], data.get('title', '')
        raise Exception("Empty")
    
    def get_status(self, tid): return self.downloads.get(tid)

# ==========================================
# 8. 统计管理器 (StatsManager) - 修复空文件报错版
# ==========================================
class IsolatedStatsManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_stats.json")

    def load(self):
        p = self._get_path()
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f: 
                    content = f.read().strip()
                    if not content: return {"daily_stats": {}}
                    return json.loads(content)
            except (json.JSONDecodeError, Exception) as e:
                print(f"[Stats] Load Error (resetting): {e}")
                return {"daily_stats": {}}
        return {"daily_stats": {}}

    def update(self, t, w, c, bk):
        d = self.load()
        if "daily_stats" not in d: d["daily_stats"] = {}
        k = datetime.now().strftime('%Y-%m-%d')
        if k not in d["daily_stats"]: d["daily_stats"][k] = {"time":0,"words":0,"chapters":0,"books":[]}
        r = d["daily_stats"][k]
        r["time"]+=t; r["words"]+=w; r["chapters"]+=c
        if bk and bk not in r["books"]: r["books"].append(bk)
        with open(self._get_path(), 'w', encoding='utf-8') as f: json.dump(d, f)

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

                summary["all"]["time"] += t
                summary["all"]["words"] += w
                summary["all"]["chapters"] += c
                books_sets["all"].update(b_list)
                
                if t > 0: summary["all"]["heatmap"].append({"date": date_str, "count": int(t/60)})

                if delta == 0:
                    summary["24h"]["time"] += t; summary["24h"]["words"] += w; summary["24h"]["chapters"] += c; books_sets["24h"].update(b_list)
                if delta < 7:
                    summary["7d"]["time"] += t; summary["7d"]["words"] += w; summary["7d"]["chapters"] += c; books_sets["7d"].update(b_list)
                if delta < 30:
                    summary["30d"]["time"] += t; summary["30d"]["words"] += w; summary["30d"]["chapters"] += c; books_sets["30d"].update(b_list)
            except: pass

        for k in books_sets:
            summary[k]["books"] = len(books_sets[k])
            summary[k]["time"] = int(summary[k]["time"] / 60) 
        return summary

# ==========================================
# 9. 标签管理 (TagManager)
# ==========================================
class IsolatedTagManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_tags.json")
    def get_all(self):
        p = self._get_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f: return json.load(f)
        return {}
    def update_tags(self, key, tags):
        d = self.get_all()
        if tags: d[key] = [t.strip() for t in tags if t.strip()]
        elif key in d: del d[key]
        with open(self._get_path(), 'w', encoding='utf-8') as f: json.dump(d, f, ensure_ascii=False)
        return d.get(key, [])

# ==========================================
# 10. 初始化所有单例
# ==========================================
role_manager = RoleManager()
update_manager = UpdateManager() # 之前漏了实例化
offline_manager = OfflineBookManager()
cache = CacheManager()
db = IsolatedDB()
booklist_manager = IsolatedBooklistManager()
downloader = DownloadManager()
tag_manager = IsolatedTagManager()
stats_manager = IsolatedStatsManager()

# 注入到 shared 供装饰器使用
shared.role_manager_instance = role_manager