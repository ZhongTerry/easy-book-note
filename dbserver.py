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

# 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data") # 用户数据隔离目录
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LIB_DIR = os.path.join(BASE_DIR, "library")
DL_DIR = os.path.join(BASE_DIR, "downloads")

# 自动创建必要目录
for d in [USER_DATA_DIR, CACHE_DIR, LIB_DIR, DL_DIR]:
    if not os.path.exists(d): os.makedirs(d)

# === 认证中心配置 ===
CLIENT_ID = '5d0c0b8a21fec049a146' 
CLIENT_SECRET = '8664201fad421f54fa6f5da92e76cb604ca70056'
# AUTH_SERVER = 'http://127.0.0.1:5124'
AUTH_SERVER = os.environ.get('server', 'http://127.0.0.1:5124')
REDIRECT_URI = os.environ.get('callback', 'http://127.0.0.1:5000/callback')

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
class SearchHelper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bing.com/"
        }

    def get_pinyin_key(self, text):
        clean_text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        clean_text = re.sub(r'(最新章节|全文阅读|无弹窗|笔趣阁|顶点|小说|在线阅读|目录|官方)', '', clean_text)
        try:
            initials = lazy_pinyin(clean_text, style=Style.FIRST_LETTER)
            key = ''.join(initials).lower()
            return key if key else "temp"
        except: return "temp"

    def search_bing(self, keyword):
        search_url = f"https://www.bing.com/search?q={keyword} 在线阅读"
        print(f"[Search] Searching for: {search_url}")
        try:
            response = requests.get(search_url, headers=self.headers, timeout=10, verify=False)
            response.encoding = response.apparent_encoding 
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            items = soup.select(".b_algo")
            for item in items:
                h2 = item.select_one('h2') or item.select_one('.b_tpcn')
                if not h2: continue
                link = h2.find('a')
                if not link: continue
                url = link.get('href')
                title = link.get_text(strip=True)
                if url and url.startswith('http') and "bing.com" not in url:
                    results.append({'title': title, 'url': url, 'suggested_key': self.get_pinyin_key(keyword)})
                if len(results) >= 10: break
            return results
        except Exception as e:
            print(f"[Search] Error: {e}")
            return []

# --- 1. 爬虫模块 (保持原版复杂逻辑) ---
class NovelCrawler:
    def __init__(self):
        self.ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15"
        ]

    def _get_random_headers(self):
        return {"User-Agent": random.choice(self.ua_list), "Referer": "https://www.google.com/"}

    def fetch_page(self, url, retry=2):
        for i in range(retry + 1):
            try:
                response = requests.get(url, headers=self._get_random_headers(), timeout=15, verify=False)
                response.raise_for_status()
                response.encoding = response.apparent_encoding
                return response.text
            except Exception as e:
                time.sleep(2)
        return None

    def _get_absolute_url(self, base_url, relative_url):
        if not relative_url or 'javascript' in relative_url or relative_url.startswith('#'): return None
        return urljoin(base_url, relative_url)

    def _clean_text(self, soup_element):
        if not soup_element: return ["内容提取失败或需要付费阅读。"]
        element = soup_element.__copy__()
        for tag in element.select('script, style, iframe, ins, .ads, .section-opt, .bar, .tp, .bottem, .bottom, div[align="center"]'):
            tag.decompose()
        lines = []
        junk_keywords = ["上一章", "下一章", "返回列表", "加入书签", "阅读模式", "转/码", "APP", "http", "笔趣阁"]
        for line in element.get_text('\n').split('\n'):
            line = line.strip()
            if not line: continue
            is_junk = False
            for junk in junk_keywords:
                if junk in line and len(line) < 30: is_junk = True; break
            if not is_junk: lines.append(line)
        return lines

    def _get_smart_title(self, soup):
        specific_h1 = soup.find('h1', class_='title') or soup.find('h1', class_='bookname') or soup.find(id='chapter-title')
        if specific_h1: return specific_h1.get_text(strip=True)
        text_area = soup.find(id='mlfy_main_text')
        if text_area and text_area.find('h1'): return text_area.find('h1').get_text(strip=True)
        for h1 in soup.find_all('h1'):
            text = h1.get_text(strip=True)
            if text in ["笔趣阁", "书斋阁", "有度中文网", "全本小说网"] or "logo" in h1.get('class', []): continue
            return text
        if soup.title: return re.split(r'[_\-|]', soup.title.get_text(strip=True))[0]
        return "未知章节"

    def get_toc(self, toc_url):
        html = self.fetch_page(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup.select('script, style, footer, .footer, .header, .nav, .navbar, .top, .search'): tag.decompose()
        best_container = None
        max_links = 0
        candidates = soup.find_all(['div', 'ul', 'dl', 'tbody', 'section'])
        for container in candidates:
            links = container.find_all('a')
            count = len(links)
            if count > 10 and count > max_links:
                valid_count = sum(1 for a in links if len(a.get_text(strip=True)) > 1)
                if valid_count > count * 0.4: max_links = count; best_container = container
        if not best_container: best_container = soup.body
        chapters = []
        if best_container:
            for a in best_container.find_all('a'):
                title = a.get_text(strip=True)
                href = a.get('href')
                if not href or not title or len(title) < 2: continue
                if href.startswith('javascript') or href.startswith('#'): continue
                if not re.search(r'(第[0-9零一二三四五六七八九十百千万]+[章回节卷])|([0-9]+)', title):
                    if len(title) <= 3: continue
                full_url = self._get_absolute_url(toc_url, href)
                if full_url: chapters.append({'title': title, 'url': full_url})
        return {'title': self._get_smart_title(soup) or "目录", 'chapters': chapters}

    def run(self, url):
        html = self.fetch_page(url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        data = {'content': [], 'title': self._get_smart_title(soup), 'prev': None, 'next': None, 'toc_url': None}
        content_div = None
        for cid in ['content', 'chaptercontent', 'BookText', 'TextContent', 'showtxt']:
            content_div = soup.find(id=cid); 
            if content_div: break
        if not content_div: content_div = soup.find(class_='content')
        data['content'] = self._clean_text(content_div)
        next_match = re.search(r'url_next\s*=\s*["\'](.*?)["\']', html)
        prev_match = re.search(r'url_preview\s*=\s*["\'](.*?)["\']', html)
        if next_match:
            data['next'] = self._get_absolute_url(url, next_match.group(1))
            data['prev'] = self._get_absolute_url(url, prev_match.group(1)) if prev_match else None
        else:
            p_tag = soup.find(id='prev_url') or soup.find(id='pb_prev') or soup.find('a', string=re.compile(r'上一[章页]'))
            n_tag = soup.find(id='next_url') or soup.find(id='pb_next') or soup.find('a', string=re.compile(r'下一[章页]'))
            data['prev'] = self._get_absolute_url(url, p_tag.get('href')) if p_tag else None
            data['next'] = self._get_absolute_url(url, n_tag.get('href')) if n_tag else None
        toc_tag = soup.find(id='info_url') or soup.find('a', string=re.compile(r'^(目录|章节目录|全文阅读)$'))
        if toc_tag: data['toc_url'] = self._get_absolute_url(url, toc_tag.get('href'))
        if not data.get('toc_url'):
            data['toc_url'] = url.rsplit('/', 1)[0] + '/'
        return data

# --- 1.5 EPUB 处理器 (保持不变) ---
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
class DownloadManager:
    def __init__(self):
        self.downloads = {}
        if not os.path.exists(DL_DIR): os.makedirs(DL_DIR)

    def start_download(self, book_name, chapters):
        task_id = hashlib.md5((book_name + str(time.time())).encode()).hexdigest()
        self.downloads[task_id] = {
            'book_name': book_name, 'total': len(chapters), 'current': 0, 
            'status': 'running', 'filename': f"{re.sub(r'[\\/*?:|<>]', '', book_name)}.txt"
        }
        thread = threading.Thread(target=self._worker, args=(task_id, chapters))
        thread.daemon = True
        thread.start()
        return task_id

    def _worker(self, task_id, chapters):
        task = self.downloads[task_id]
        filepath = os.path.join(DL_DIR, task['filename'])
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"=== {task['book_name']} ===\n\n")
                for index, chap in enumerate(chapters):
                    # 复用全局爬虫和缓存
                    data = cache.get(chap['url'])
                    if not data:
                        time.sleep(random.uniform(1.0, 3.0)) # 避雷针
                        data = crawler.run(chap['url'])
                        if data and data['content']: cache.set(chap['url'], data)
                    
                    if data and data['content']:
                        f.write(f"\n\n=== {chap['title']} ===\n\n")
                        f.write('\n'.join(data['content']) if isinstance(data['content'], list) else data['content'])
                    
                    task['current'] = index + 1
            task['status'] = 'completed'
        except Exception as e:
            task['status'] = 'error'; task['error_msg'] = str(e)

    def get_status(self, task_id): return self.downloads.get(task_id)

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

# --- 初始化所有服务 ---
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
    u, k = request.args.get('url'), request.args.get('key', '')
    if u.startswith('epub:'): data = epub_handler.get_toc(u.split(':')[1])
    else:
        data = cache.get(u) or crawler.get_toc(u)
        if data: cache.set(u, data)
    return render_template('toc.html', toc=data, toc_url=u, db_key=k)

if __name__ == '__main__':
    app.run(debug=False, port=5000, host='0.0.0.0')