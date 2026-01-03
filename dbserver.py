from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, send_from_directory, session
import os
import shutil
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
import warnings
import hashlib
import json
import time
import random
import threading
from pypinyin import pinyin, lazy_pinyin, Style
import glob
from datetime import datetime, timedelta
import ebooklib
from ebooklib import epub
from werkzeug.utils import secure_filename
from functools import wraps
from urllib.parse import urljoin, urlparse
import sqlite3
import socket

# --- 基础配置 ---
app = Flask(__name__)
# [关键修复] 设置固定的密钥，否则每次服务器重启都会导致所有用户掉线
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-unsafe-key-change-it')
app.permanent_session_lifetime = timedelta(days=30) 
app.config['SESSION_COOKIE_NAME'] = 'simplenote_session'
from dotenv import load_dotenv
# 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data") # 用户数据隔离目录
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LIB_DIR = os.path.join(BASE_DIR, "library")
DL_DIR = os.path.join(BASE_DIR, "downloads")
load_dotenv()
# 自动创建必要目录
for d in [USER_DATA_DIR, CACHE_DIR, LIB_DIR, DL_DIR]:
    if not os.path.exists(d): os.makedirs(d)

# === 认证中心配置 ===
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
# AUTH_SERVER = 'http://127.0.0.1:5124'
AUTH_SERVER = os.environ.get('SERVER', 'https://auth.ztrztr.top')
REDIRECT_URI = os.environ.get('CALLBACK', 'https://book.ztrztr.top/callback')

# --- 登录装饰器 ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            # API 请求返回 401，页面请求跳转登录
            if request.path.startswith('/api/') or request.path in ['/insert', '/update', '/remove', '/list', '/find', '/rollback']:
                return jsonify({"status": "error", "message": "Unauthorized"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
def is_safe_url(url):
    """防止 SSRF 攻击，禁止爬虫访问内网 IP"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'): return False
        hostname = parsed.hostname
        ip = socket.gethostbyname(hostname)
        # 禁止 127.0.0.1, 192.168.x.x, 10.x.x.x 等内网段
        if ip.startswith(('127.', '192.168.', '10.', '172.16.', '0.')): return False
        return True
    except:
        return False

# --- 重构后的 SQLite 数据库类 ---
# === 1. 放在 app.py 里的书单管理器 ===
class IsolatedBooklistManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_booklists.json")

    def load(self):
        path = self._get_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:  # 如果文件是空的
                        return {}
                    return json.loads(content)
            except (json.JSONDecodeError, Exception) as e:
                print(f"读取书单JSON出错 (可能是格式损坏): {e}")
                return {}
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
            # 简单去重
            if not any(b['key'] == book_data['key'] for b in data[list_id]['books']):
                data[list_id]['books'].append(book_data)
                self.save(data)
        return data

# 初始化
booklist_manager = IsolatedBooklistManager()

# === 2. 路由部分 ===
@app.route('/api/booklists/all')
@login_required
def api_booklists_all():
    return jsonify({"status": "success", "data": booklist_manager.load()})

@app.route('/api/booklists/create', methods=['POST'])
@login_required
def api_booklists_create():
    name = request.json.get('name', '新书单')
    return jsonify({"status": "success", "id": booklist_manager.add_list(name)})

@app.route('/api/booklists/update_book', methods=['POST'])
@login_required
def api_booklists_update_book():
    d = request.json # {list_id, book_key, status, action: 'update' | 'remove'}
    data = booklist_manager.load()
    if d['list_id'] in data:
        books = data[d['list_id']]['books']
        if d['action'] == 'remove':
            data[d['list_id']]['books'] = [b for b in books if b['key'] != d['book_key']]
        else:
            for b in books:
                if b['key'] == d['book_key']: b['status'] = d['status']
        booklist_manager.save(data)
    return jsonify({"status": "success", "data": data})

@app.route('/api/booklists/add_book', methods=['POST'])
@login_required
def api_booklists_add_book():
    # book_data: {key, title, status: 'want'}
    booklist_manager.add_to_list(request.json['list_id'], request.json['book_data'])
    return jsonify({"status": "success"})
# --- 0. 缓存管理器 (保持不变) ---
class CacheManager:
    def __init__(self, cache_dir="cache", ttl=604800): 
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.cache_dir = os.path.join(self.base_dir, cache_dir)
        self.ttl = ttl 
        if not os.path.exists(self.cache_dir): os.makedirs(self.cache_dir)

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
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e: print(f"[Cache] Write Error: {e}")

# --- 搜索辅助类 (保持不变) ---
# === 放在 app.py 里的 SearchHelper 类 ===

# === 放在 app.py 里的 SearchHelper 类 (完整替换版) ===

# === app.py 中的 SearchHelper 类 (针对性优化版) ===

# === app.py 中的 SearchHelper 类 (宽容模式 + 强力黑名单) ===

# === app.py 中的 SearchHelper 类 (强制国际版 + 详细调试) ===

# === app.py 中的 SearchHelper 类 (自动代理 + 双引擎满血版) ===

from urllib.request import getproxies # 记得在 app.py 顶部确保有这个，或者直接在这里用

class SearchHelper:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 10
        self.proxies = self._get_system_proxies()

    def _get_system_proxies(self):
        try:
            proxies = getproxies()
            if proxies:
                print(f"[System] Detected proxies: {proxies}")
                return proxies
        except: pass
        return None

    def get_pinyin_key(self, text):
        # 提取拼音首字母
        clean_text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        # 移除一些可能是用户手滑输入的无关词
        clean_text = re.sub(r'(小说|笔趣阁|最新章节)', '', clean_text)
        try:
            initials = lazy_pinyin(clean_text, style=Style.FIRST_LETTER)
            key = ''.join(initials).lower()
            # 限制长度，防止太长
            return key[:15] if key else "temp"
        except: return "temp"

    def _clean_title(self, title):
        return re.split(r'(-|_|\|)', title)[0].strip()

    def _is_junk(self, title, url):
        title = title.lower()
        url = url.lower()
        bad_domains = [
            'facebook.com', 'twitter.com', 'zhihu.com', 'douban.com', 
            'baidu.com', 'baike', 'csdn', 'cnblogs', 'youtube', 'bilibili', 
            '52pojie', '163.com', 'sohu.com', 'microsoft.com', 'google.com',
            'apple.com', 'amazon.com'
        ]
        if any(d in url for d in bad_domains): return True
        bad_keywords = ['工具', '破解', '软件', '下载', '教程', '视频', '剧透', '百科', '资讯', '手游', '官网', 'APP']
        if any(k in title for k in bad_keywords): return True
        return False

    def _do_ddg_search(self, keyword):
        print(f"[Search] DuckDuckGo: {keyword}")
        url = "https://html.duckduckgo.com/html/"
        data = {'q': f"{keyword} 笔趣阁 目录"}
        
        try:
            resp = cffi_requests.post(
                url, data=data, 
                impersonate=self.impersonate, 
                timeout=self.timeout,
                proxies=self.proxies 
            )
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            
            for link in soup.find_all('a', class_='result__a'):
                title = link.get_text(strip=True)
                href = link.get('href')
                if not href.startswith('http'): continue
                if self._is_junk(title, href): continue
                
                results.append({
                    'title': self._clean_title(title),
                    'url': href,
                    # [关键修改] 使用用户输入的 keyword 生成 Key，而不是网页 Title
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': 'DuckDuckGo 🦆'
                })
                if len(results) >= 8: break
            
            if results: return results
        except Exception as e:
            print(f"[Search] DDG Failed: {e}")
        return None

    def _do_bing_search(self, keyword):
        print(f"[Search] Bing Intl: {keyword}")
        url = "https://www.bing.com/search"
        params = {'q': f"{keyword} 笔趣阁 目录", 'setmkt': 'en-US'}
        
        try:
            resp = cffi_requests.get(
                url, params=params,
                impersonate=self.impersonate, 
                timeout=self.timeout,
                proxies=self.proxies
            )
            soup = BeautifulSoup(resp.content, 'html.parser')
            links = soup.select('li.b_algo h2 a') or soup.select('li h2 a') or soup.select('h2 a')
            
            results = []
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                if not href or not href.startswith('http'): continue
                if self._is_junk(title, href): continue
                
                results.append({
                    'title': self._clean_title(title),
                    'url': href,
                    # [关键修改] 同上，使用 keyword
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': 'Bing 🌐'
                })
                if len(results) >= 8: break
            return results
        except Exception as e:
            print(f"[Search] Bing Failed: {e}")
            return []

    def search_bing(self, keyword):
        res = self._do_ddg_search(keyword)
        if res: return res
        return self._do_bing_search(keyword)
class EpubHandler:
    def __init__(self):
        self.lib_dir = os.path.join(BASE_DIR, "library")
        if not os.path.exists(self.lib_dir): os.makedirs(self.lib_dir)

    def save_file(self, file_obj):
        filename = secure_filename(file_obj.filename)
        if not filename: filename = f"book_{int(time.time())}.epub"
        filepath = os.path.join(self.lib_dir, filename)
        file_obj.save(filepath)
        return filename

    def get_toc(self, filename):
        filepath = os.path.join(self.lib_dir, filename)
        if not os.path.exists(filepath): return None
        try:
            book = epub.read_epub(filepath)
            title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else filename
            chapters = [{'title': f"第 {i+1} 节", 'url': f"epub:{filename}:{i}"} for i, _ in enumerate(book.spine)]
            return {'title': title, 'chapters': chapters}
        except: return None

    def get_chapter_content(self, filename, chapter_index):
        filepath = os.path.join(self.lib_dir, filename)
        try:
            book = epub.read_epub(filepath)
            item = book.get_item_with_id(book.spine[chapter_index][0])
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            lines = [p.get_text(strip=True) for p in soup.find_all(['p', 'div', 'h1', 'h2']) if p.get_text(strip=True)]
            return {
                'title': f"第 {chapter_index+1} 节", 'content': lines,
                'prev': f"epub:{filename}:{chapter_index-1}" if chapter_index > 0 else None,
                'next': f"epub:{filename}:{chapter_index+1}" if chapter_index < len(book.spine) - 1 else None,
                'toc_url': f"epub:{filename}:toc"
            }
        except Exception as e: return f"EPUB Error: {e}"

# --- [重构核心] 2. 数据库逻辑 (多文件隔离 + 备份) ---
# --- [重构核心] 2. 数据库逻辑 (SQLite 版) ---
class IsolatedDB:
    def _get_db_conn(self):
        username = session.get('user', {}).get('username', 'default_user')
        # 确保目录存在
        if not os.path.exists(USER_DATA_DIR): os.makedirs(USER_DATA_DIR)
        # [关键] 必须使用 .sqlite 后缀，对应迁移脚本
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
        except Exception as e:
            return {"status": "error", "message": f"Database error: {str(e)}"}

    def update(self, key, value):
        # 为了兼容前端逻辑，update 和 insert 在 KV 存储中通常是一样的
        return self.insert(key, value)

    def remove(self, key):
        if not key: return {"status": "error", "message": "Key cannot be empty"}
        try:
            with self._get_db_conn() as conn:
                conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            return {"status": "success", "message": f"Removed: {key}"}
        except Exception as e:
            return {"status": "error", "message": f"Database error: {str(e)}"}

    def list_all(self):
        try:
            with self._get_db_conn() as conn:
                # 过滤掉系统内部key (如 @last_read)
                cursor = conn.execute("SELECT key, value FROM kv_store WHERE key NOT LIKE '@%' ORDER BY key DESC")
                data = {row[0]: row[1] for row in cursor.fetchall()}
            return {"status": "success", "data": data}
        except Exception as e:
            return {"status": "error", "message": f"Database error: {str(e)}"}

    def find(self, term):
        try:
            with self._get_db_conn() as conn:
                # 使用参数化查询防止 SQL 注入
                like_term = f'%{term}%'
                cursor = conn.execute("SELECT key, value FROM kv_store WHERE key LIKE ? OR value LIKE ?", (like_term, like_term))
                data = {row[0]: row[1] for row in cursor.fetchall()}
            return {"status": "success", "data": data}
        except Exception as e:
            return {"status": "error", "message": f"Database error: {str(e)}"}

    def get_val(self, key):
        try:
            with self._get_db_conn() as conn:
                cursor = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
                row = cursor.fetchone()
                return row[0] if row else None
        except:
            return None

    def rollback(self):
        # SQLite 暂不支持简单的回滚，返回错误提示前端
        return {"status": "error", "message": "SQLite 模式暂不支持撤销功能"}
# --- 3. 下载管理器 (保持不变，支持多线程) ---
from concurrent.futures import ThreadPoolExecutor, as_completed

class DownloadManager:
    def __init__(self):
        self.downloads = {}
        if not os.path.exists(DL_DIR): os.makedirs(DL_DIR)
        # 线程池，最大并发 5 个下载任务，每个任务内部再开线程
        self.executor = ThreadPoolExecutor(max_workers=5)

    def start_download(self, book_name, chapters):
        task_id = hashlib.md5((book_name + str(time.time())).encode()).hexdigest()
        self.downloads[task_id] = {
            'book_name': book_name, 
            'total': len(chapters), 
            'current': 0, 
            'status': 'running', 
            'filename': f"{re.sub(r'[\\/*?:|<>]', '', book_name)}.txt",
            'error_msg': ''
        }
        
        # 启动后台线程处理
        threading.Thread(target=self._master_worker, args=(task_id, chapters)).start()
        return task_id

    def _master_worker(self, task_id, chapters):
        """
        主控线程：负责调度多线程抓取章节
        """
        task = self.downloads[task_id]
        results = [None] * len(chapters) # 预分配数组，保证顺序
        
        # 内部线程池，并发抓取章节
        # 注意：并发太高会被封 IP，设置为 5-8 比较安全
        with ThreadPoolExecutor(max_workers=8) as pool:
            future_to_index = {
                pool.submit(self._fetch_chapter_worker, chap['url']): i 
                for i, chap in enumerate(chapters)
            }
            
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    content, title = future.result()
                    # 格式化章节内容
                    formatted = f"\n\n=== {title} ===\n\n" + '\n'.join(content)
                    results[idx] = formatted
                except Exception as e:
                    results[idx] = f"\n\n=== 第{idx+1}章 下载失败 ===\n\n[Error: {e}]"
                
                # 更新进度
                task['current'] += 1
        
        # 所有线程结束，写入文件
        try:
            filepath = os.path.join(DL_DIR, task['filename'])
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"=== {task['book_name']} ===\n\n")
                f.write(f"来源: Smart NoteDB 自动抓取\n")
                for res in results:
                    if res: f.write(res)
            
            task['status'] = 'completed'
        except Exception as e:
            task['status'] = 'error'
            task['error_msg'] = str(e)

    def _fetch_chapter_worker(self, url):
        """
        单个章节抓取单元，复用全局 crawler 和 cache
        """
        # 1. 查缓存
        cached = cache.get(url)
        if cached and cached.get('content'):
            return cached['content'], cached.get('title', '未知章节')
            
        # 2. 没缓存，抓取
        # 随机延迟，防止并发过快被封
        time.sleep(random.uniform(0.1, 0.5))
        
        data = crawler.run(url)
        if data and data['content']:
            # 写入缓存
            cache.set(url, data)
            return data['content'], data.get('title', '未知章节')
        else:
            raise Exception("Empty content")

    def get_status(self, task_id): 
        return self.downloads.get(task_id)

# --- 标签管理器 (多用户版) ---
class IsolatedTagManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_tags.json")
    
    def get_all(self):
        path = self._get_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        return {}

    def update_tags(self, key, tag_list):
        tags = self.get_all()
        clean = [t.strip() for t in tag_list if t.strip()]
        if clean: tags[key] = clean
        elif key in tags: del tags[key]
        with open(self._get_path(), 'w', encoding='utf-8') as f:
            json.dump(tags, f, ensure_ascii=False, indent=2)
        return clean

# --- 统计管理器 (多用户版) ---
class IsolatedStatsManager:
    def _get_path(self):
        u = session.get('user', {}).get('username', 'default')
        return os.path.join(USER_DATA_DIR, f"{u}_stats.json")

    def load(self):
        path = self._get_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: 
                d = json.load(f)
                return d if "daily_stats" in d else {"daily_stats": {}}
        return {"daily_stats": {}}

    def update(self, t_add, w_add, c_add, b_key):
        d = self.load()
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in d["daily_stats"]: d["daily_stats"][today] = {"time":0, "words":0, "chapters":0, "books":[]}
        rec = d["daily_stats"][today]
        rec["time"]+=t_add; rec["words"]+=w_add; rec["chapters"]+=c_add
        if b_key and b_key not in rec["books"]: rec["books"].append(b_key)
        with open(self._get_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    def get_summary(self):
        """计算 24h(今日), 7天, 30天, 全部 的统计数据"""
        today = datetime.now()
        summary = {
            "24h": {"time": 0, "words": 0, "chapters": 0, "books": 0},
            "7d":  {"time": 0, "words": 0, "chapters": 0, "books": 0},
            "30d": {"time": 0, "words": 0, "chapters": 0, "books": 0},
            "all": {"time": 0, "words": 0, "chapters": 0, "books": 0, "heatmap": []},
            "trend": {"dates": [], "times": []}
        }
        
        books_sets = {"24h": set(), "7d": set(), "30d": set(), "all": set()}
        
        # ✅ [修复点] 使用 self.load() 获取当前用户数据
        data = self.load()
        daily = data.get("daily_stats", {})
        
        # 1. 生成最近30天的完整日期列表
        for i in range(29, -1, -1):
            day = today - timedelta(days=i)
            d_str = day.strftime('%Y-%m-%d')
            rec = daily.get(d_str, {})
            summary["trend"]["dates"].append(d_str[5:])
            summary["trend"]["times"].append(int(rec.get("time", 0) / 60))

        # 2. 遍历历史数据
        for date_str, rec in daily.items():
            # ... (这部分逻辑保持不变，不需要改) ...
            try:
                rec_date = datetime.strptime(date_str, '%Y-%m-%d')
                delta = (today - rec_date).days
                
                t, w, c = rec.get("time", 0), rec.get("words", 0), rec.get("chapters", 0)
                b_list = rec.get("books", [])

                # All Time
                summary["all"]["time"] += t
                summary["all"]["words"] += w
                summary["all"]["chapters"] += c
                books_sets["all"].update(b_list)
                
                # 热力图
                if t > 0:
                    summary["all"]["heatmap"].append({"date": date_str, "count": int(t/60)})

                # 区间统计
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
# === 引入新依赖 (放在文件顶部) ===
from curl_cffi import requests as cffi_requests # 需要 pip install curl_cffi
from lxml import html as lxml_html # 需要 pip install lxml

# ... (其他导入保持不变)

# === app.py 中的 NovelCrawler 类 (增强版 v3) ===

# === 确保 app.py 头部有这些 import ===
# from curl_cffi import requests as cffi_requests
# from bs4 import BeautifulSoup
# from lxml import html as lxml_html
# import re
# import time
# import random
# from urllib.parse import urljoin
# from concurrent.futures import ThreadPoolExecutor

# === app.py 中的 NovelCrawler 类 (调试修正版) ===

# 确保文件头部有这些：
# from curl_cffi import requests as cffi_requests
# from bs4 import BeautifulSoup
# from lxml import html as lxml_html
# import re, time, random
# from urllib.parse import urljoin
# from concurrent.futures import ThreadPoolExecutor

# ==========================================
# 1. SearchHelper - 负责找书、生成Key
# ==========================================
class SearchHelper:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 5
        self.proxies = getproxies() # 自动获取系统代理

    def get_pinyin_key(self, text):
        clean_text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        clean_text = re.sub(r'(小说|笔趣阁|最新章节|全文阅读)', '', clean_text)
        try:
            initials = lazy_pinyin(clean_text, style=Style.FIRST_LETTER)
            key = ''.join(initials).lower()
            return key[:15] if key else "temp"
        except: return "temp"

    def _is_junk(self, title, url):
        bad_domains = ['facebook.com', 'twitter.com', 'zhihu.com', 'douban.com', 'baidu.com', '52pojie', '163.com']
        bad_keywords = ['工具', '破解', '软件', '下载', '视频', '剧透', '百科', 'APP']
        if any(d in url.lower() for d in bad_domains): return True
        if any(k in title.lower() for k in bad_keywords): return True
        return False

    def _do_ddg_search(self, keyword):
        url = "https://html.duckduckgo.com/html/"
        data = {'q': f"{keyword} 笔趣阁 目录"}
        try:
            resp = cffi_requests.post(url, data=data, impersonate=self.impersonate, timeout=self.timeout, proxies=self.proxies)
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            for link in soup.find_all('a', class_='result__a'):
                title, href = link.get_text(strip=True), link.get('href')
                if not href.startswith('http') or self._is_junk(title, href): continue
                results.append({'title': re.split(r'(-|_|\|)', title)[0].strip(), 'url': href, 'suggested_key': self.get_pinyin_key(keyword), 'source': 'DuckDuckGo 🦆'})
                if len(results) >= 8: break
            return results
        except: return None

    def _do_bing_search(self, keyword):
        url = "https://www.bing.com/search"
        params = {'q': f"{keyword} 笔趣阁 目录", 'setmkt': 'en-US'}
        try:
            resp = cffi_requests.get(url, params=params, impersonate=self.impersonate, timeout=self.timeout, proxies=self.proxies)
            soup = BeautifulSoup(resp.content, 'html.parser')
            links = soup.select('li.b_algo h2 a') or soup.select('h2 a')
            results = []
            for link in links:
                title, href = link.get_text(strip=True), link.get('href')
                if not href or not href.startswith('http') or self._is_junk(title, href): continue
                results.append({'title': re.split(r'(-|_|\|)', title)[0].strip(), 'url': href, 'suggested_key': self.get_pinyin_key(keyword), 'source': 'Bing 🌐'})
                if len(results) >= 8: break
            return results
        except: return []

    def search_bing(self, keyword):
        return self._do_ddg_search(keyword) or self._do_bing_search(keyword)

# ==========================================
# 2. NovelCrawler - 负责抓取目录和正文
# ==========================================
class NovelCrawler:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 15
        self.proxies = getproxies()

    def _fetch_page_smart(self, url, retry=3):
        for i in range(retry):
            try:
                headers = {"Referer": url, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"}
                resp = cffi_requests.get(url, impersonate=self.impersonate, timeout=self.timeout, headers=headers, allow_redirects=True, proxies=self.proxies)
                # 智能编码：meta优先，失败后尝试GBK
                try:
                    tree = lxml_html.fromstring(resp.content)
                    charset = tree.xpath('//meta[contains(@content, "charset")]/@content') or tree.xpath('//meta/@charset')
                    enc = 'utf-8'
                    if charset:
                        match = re.search(r'charset=([\w-]+)', str(charset[0]), re.I)
                        enc = match.group(1) if match else charset[0]
                    return resp.content.decode(enc)
                except:
                    for e in ['utf-8', 'gbk', 'gb18030']:
                        try: return resp.content.decode(e)
                        except: continue
                return resp.content.decode('utf-8', errors='replace')
            except: time.sleep(1)
        return None

    def _clean_text_lines(self, text):
        if not text: return []
        junk = [r"一秒记住", r"最新章节", r"笔趣阁", r"上一章", r"下一章", r"加入书签", r"投推荐票", r"本章未完"]
        lines = []
        for line in text.split('\n'):
            line = line.replace('\xa0', ' ').strip()
            if not line or len(line) < 2: continue
            if len(line) < 40 and any(re.search(p, line, re.I) for p in junk): continue
            if "{" in line and "function" in line: continue
            lines.append(line)
        return lines

    def _extract_content_smart(self, soup):
        # 兼容神秘复苏 (id="txt") 和通用 ID
        for cid in ['txt', 'content', 'chaptercontent', 'BookText', 'showtxt', 'nr1', 'read-content']:
            div = soup.find(id=cid)
            if div:
                for a in div.find_all('a'): a.decompose() # 剔除正文内的干扰链接
                return self._clean_text_lines(div.get_text('\n'))
        # 文本密度算法兜底
        best_div, max_score = None, 0
        for div in soup.find_all('div'):
            if div.get('id') and re.search(r'(nav|foot|header)', str(div.get('id')), re.I): continue
            txt = div.get_text(strip=True)
            score = len(txt) - (len(div.find_all('a')) * 5)
            if score > max_score: max_score, best_div = score, div
        if best_div:
            return self._clean_text_lines(best_div.get_text('\n'))
        return ["正文解析失败"]

    def _parse_chapters_from_soup(self, soup, base_url):
        links, max_links = [], 0
        for container in soup.find_all(['div', 'ul', 'dl', 'tbody']):
            if container.get('class') and 'nav' in str(container.get('class')): continue
            curr = []
            for a in container.find_all('a'):
                txt, href = a.get_text(strip=True), a.get('href')
                if href and (re.search(r'(\d+|第.+[章节回])', txt) or len(txt) > 5):
                    full = urljoin(base_url, href)
                    if full: curr.append({'title': txt, 'url': full})
            if len(curr) > max_links: max_links, links = len(curr), curr
        return links

    def get_toc(self, toc_url):
        print(f"[Crawler] TOC: {toc_url}")
        html = self._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        all_chaps = self._parse_chapters_from_soup(soup, toc_url)
        # 处理分页
        pages = set()
        for s in soup.find_all('select'):
            for o in s.find_all('option'):
                val = o.get('value')
                if val:
                    full = urljoin(toc_url, val)
                    if full.rstrip('/') != toc_url.rstrip('/'): pages.add(full)
        if pages:
            with ThreadPoolExecutor(max_workers=5) as exe:
                results = exe.map(lambda u: self._parse_chapters_from_soup(BeautifulSoup(self._fetch_page_smart(u) or "", 'html.parser'), toc_url), sorted(list(pages)))
                for sub in results:
                    urls = set(c['url'] for c in all_chaps)
                    for c in sub:
                        if c['url'] not in urls: all_chaps.append(c); urls.add(c['url'])
        return {'title': (soup.find('h1').get_text(strip=True) if soup.find('h1') else "目录"), 'chapters': all_chaps}

    def run(self, url):
        html = self._fetch_page_smart(url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        data = {'content': self._extract_content_smart(soup), 'title': (soup.find('h1').get_text(strip=True) if soup.find('h1') else "未知"), 'prev': None, 'next': None, 'toc_url': None}
        # 兼容神秘复苏 ID
        for aid in ['pb_prev', 'pb_next', 'pb_mulu']:
            tag = soup.find(id=aid)
            if tag and tag.get('href'):
                u = urljoin(url, tag['href'])
                if 'prev' in aid: data['prev'] = u
                elif 'next' in aid: data['next'] = u
                elif 'mulu' in aid: data['toc_url'] = u
        # 文本兜底匹配
        if not data['next'] or not data['prev']:
            for a in soup.find_all('a'):
                t, h = a.get_text(strip=True), a.get('href')
                if not h: continue
                u = urljoin(url, h)
                if not data['prev'] and '上一章' in t: data['prev'] = u
                elif not data['next'] and '下一章' in t: data['next'] = u
        return data

    def get_first_chapter(self, toc_url):
        res = self.get_toc(toc_url)
        return res['chapters'][0]['url'] if res and res['chapters'] else None
db = IsolatedDB()
crawler = NovelCrawler()
cache = CacheManager()
downloader = DownloadManager()
tag_manager = IsolatedTagManager()
stats_manager = IsolatedStatsManager()
searcher = SearchHelper()
epub_handler = EpubHandler()

# ================= 路由部分 =================

@app.route('/login')
def login():
    return redirect(f"{AUTH_SERVER}/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}")
@app.route('/stats')
@login_required
def stats_page():
    return render_template('stats.html')
@app.route('/api/resolve_head', methods=['POST'])
@login_required
def api_resolve_head():
    toc_url = request.json.get('url')
    if not toc_url: return jsonify({"status": "error"})
    
    # 调用爬虫获取第一章
    try:
        first_url = crawler.get_first_chapter(toc_url)
        if first_url:
            return jsonify({"status": "success", "url": first_url})
        else:
            # 如果解析失败，原样返回目录链接，不影响用户使用
            return jsonify({"status": "success", "url": toc_url})
    except Exception as e:
        print(f"Resolve Error: {e}")
        return jsonify({"status": "success", "url": toc_url})
@app.route('/callback')
def callback():
    code = request.args.get('code')
    try:
        resp = requests.post(f"{AUTH_SERVER}/oauth/token", json={
            'grant_type': 'authorization_code', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'code': code
        }).json()
        if 'access_token' in resp:
            user_info = requests.get(f"{AUTH_SERVER}/api/user", headers={'Authorization': f"Bearer {resp['access_token']}"}).json()
            session.permanent = True # 开启持久化会话
            session['user'] = user_info
            return redirect(url_for('index'))
    except Exception as e: print(e)
    return "Login Failed", 400

@app.route('/logout')
def logout(): session.clear(); return redirect('/')

@app.route('/api/me')
def api_me(): return jsonify(session.get('user', {"username": None}))

@app.route('/')
@login_required
def index(): return send_file(os.path.join(BASE_DIR, 'index.html'))

# 核心业务 API
@app.route('/list', methods=['POST'])
@login_required
def list_all(): return jsonify(db.list_all())

@app.route('/find', methods=['POST'])
@login_required
def find(): return jsonify(db.find(request.json.get('key', '')))

@app.route('/insert', methods=['POST'])
@login_required
def insert(): return jsonify(db.insert(request.json.get('key'), request.json.get('value')))

@app.route('/manifest.json')
def serve_manifest():
    return send_file('manifest.json')

@app.route('/sw.js')
def serve_sw():
    return send_file('sw.js')

@app.route('/update', methods=['POST'])
@login_required
def update(): return jsonify(db.update(request.json.get('key'), request.json.get('value')))

@app.route('/remove', methods=['POST'])
@login_required
def remove(): return jsonify(db.remove(request.json.get('key')))

@app.route('/rollback', methods=['POST'])
@login_required
def rollback(): return jsonify(db.rollback())

# 辅助 API
@app.route('/api/get_value', methods=['POST'])
@login_required
def get_val():
    val = db.get_val(request.json.get('key'))
    return jsonify({"status": "success", "value": val}) if val else jsonify({"status": "error"})

@app.route('/purecss/<path:path>')
def send_pure_assets(path):
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'purecss'), path)

@app.route('/api/last_read', methods=['GET', 'POST'])
@login_required
def handle_last_read():
    if request.method == 'GET': return jsonify({"status": "success", "key": db.get_val('@last_read')})
    return jsonify(db.insert('@last_read', request.json.get('key')))

# 标签与统计
@app.route('/api/tags/list')
@login_required
def api_tags_list(): return jsonify({"status": "success", "data": tag_manager.get_all()})

@app.route('/api/tags/update', methods=['POST'])
@login_required
def api_tags_update():
    return jsonify({"status": "success", "tags": tag_manager.update_tags(request.json.get('key'), request.json.get('tags', []))})

@app.route('/api/analyze_stats')
@login_required
def api_analyze_stats(): return jsonify({"status": "success", "summary": stats_manager.get_summary(), "keywords": []})

@app.route('/api/stats/heartbeat', methods=['POST'])
@login_required
def api_heartbeat():
    d = request.json
    stats_manager.update(60 if d.get('is_heartbeat') else 0, d.get('words', 0), 1 if d.get('words', 0) > 0 else 0, d.get('book_key'))
    return jsonify({"status": "success"})

# 工具类 API
@app.route('/api/search_novel', methods=['POST'])
@login_required
def api_search(): return jsonify({"status": "success", "data": searcher.search_bing(request.json.get('keyword'))})

@app.route('/api/upload_epub', methods=['POST'])
@login_required
def api_upload_epub():
    if 'file' not in request.files: return jsonify({"status": "error"})
    f = request.files['file']
    fname = epub_handler.save_file(f)
    key = searcher.get_pinyin_key(os.path.splitext(fname)[0])
    val = f"epub:{fname}:toc"
    db.insert(key, val)
    return jsonify({"status": "success", "key": key, "value": val})

@app.route('/api/download', methods=['POST'])
@login_required
def start_download_route():
    d = request.json
    toc = cache.get(d['toc_url']) or crawler.get_toc(d['toc_url'])
    if not toc: return jsonify({"status": "error"})
    return jsonify({"status": "success", "task_id": downloader.start_download(d['book_name'], toc['chapters'])})

@app.route('/api/download/status')
@login_required
def dl_status(): return jsonify(downloader.get_status(request.args.get('task_id')))

@app.route('/api/download/file')
@login_required
def dl_file():
    t = downloader.get_status(request.args.get('task_id'))
    return send_from_directory(DL_DIR, t['filename'], as_attachment=True) if t else ("Not Found", 404)

# 页面路由
@app.route('/read')
@login_required
def read_mode():
    u, k = request.args.get('url'), request.args.get('key', '')
    if not u.startswith('epub:') and not is_safe_url(u):
        return "Security Error: Illegal URL", 403
    if u.startswith('epub:'):
        parts = u.split(':')
        if parts[2] == 'toc': return redirect(url_for('toc_page', url=u, key=k))
        data = epub_handler.get_chapter_content(parts[1], int(parts[2]))
    else:
        data = cache.get(u) or crawler.run(u)
        if data: cache.set(u, data)
    return render_template('reader.html', article=data, current_url=u, db_key=k)

@app.route('/toc')
@login_required
def toc_page():
    u = request.args.get('url')
    k = request.args.get('key', '')
    
    # === 检查这行是否存在 ===
    force = request.args.get('force') 
    
    data = None
    # === 检查这里：只有在该变量不存在时才读缓存 ===
    if not force:
        data = cache.get(u)
        
    if not data:
        data = crawler.get_toc(u)
        if data: 
            cache.set(u, data)
            
    return render_template('toc.html', toc=data, toc_url=u, db_key=k)

if __name__ == '__main__':
    app.run(debug=False, port=5000, host='0.0.0.0')