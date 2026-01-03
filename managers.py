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

# === Role Manager ===
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

# managers.py

    def set_role(self, username, role):
        data = self.load()
        
        # 1. 先把这个人从所有列表里踢出去 (防止残留)
        if username in data["admins"]: data["admins"].remove(username)
        if username in data["pros"]: data["pros"].remove(username)
        
        # 2. 根据新角色添加到对应列表
        if role == "admin": 
            data["admins"].append(username)
        elif role == "pro": 
            data["pros"].append(username)
        # 如果是 'user'，上面踢出去后就不加了，回归普通用户
        
        self.save(data)

# === Offline Book Manager ===
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

# === Cache Manager ===
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

# === Booklist Manager ===
class IsolatedBooklistManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_booklists.json")

    def load(self):
        path = self._get_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    c = f.read().strip()
                    return json.loads(c) if c else {}
            except: return {}
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

# === KV Database ===
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

# === Download Manager ===
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

# === Stats Manager (完整版) ===
class IsolatedStatsManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_stats.json")

    def load(self):
        p = self._get_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f: return json.load(f)
        return {"daily_stats": {}}

    def update(self, t, w, c, bk):
        d = self.load()
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
        
        # 30天趋势
        for i in range(29, -1, -1):
            day = today - timedelta(days=i)
            d_str = day.strftime('%Y-%m-%d')
            rec = daily.get(d_str, {})
            summary["trend"]["dates"].append(d_str[5:])
            summary["trend"]["times"].append(int(rec.get("time", 0) / 60))

        # 统计汇总
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
                    summary["24h"]["time"] += t
                    summary["24h"]["words"] += w
                    summary["24h"]["chapters"] += c
                    books_sets["24h"].update(b_list)
                if delta < 7:
                    summary["7d"]["time"] += t
                    summary["7d"]["words"] += w
                    summary["7d"]["chapters"] += c
                    books_sets["7d"].update(b_list)
                if delta < 30:
                    summary["30d"]["time"] += t
                    summary["30d"]["words"] += w
                    summary["30d"]["chapters"] += c
                    books_sets["30d"].update(b_list)
            except: pass

        for k in books_sets:
            summary[k]["books"] = len(books_sets[k])
            summary[k]["time"] = int(summary[k]["time"] / 60) 
        return summary

# === Tag Manager ===
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

# === 初始化单例 ===
role_manager = RoleManager()
offline_manager = OfflineBookManager()
cache = CacheManager()
db = IsolatedDB()
booklist_manager = IsolatedBooklistManager()
downloader = DownloadManager()
tag_manager = IsolatedTagManager()
stats_manager = IsolatedStatsManager()

# 注入到 shared
shared.role_manager_instance = role_manager