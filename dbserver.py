from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, send_from_directory
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
import threading # 新增：用于后台下载
from pypinyin import pinyin, lazy_pinyin, Style # 新增：拼音库
import glob # 用于查找文件列表
from datetime import datetime, timedelta # 用于生成时间戳

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

# === 新增：Bing 搜索与拼音工具 (修复版) ===

import html as html
class SearchHelper:
    def __init__(self):
        # 伪装成真实的 PC 浏览器，这非常关键！
        self.headers = {
            "User-Agent": "'User-Agent':'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36 Edg/134.0.0.0',",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.bing.com/",
            # 添加一个假的 Cookie 有助于绕过部分反爬
            "Cookie": "SRCHHPGUSR=CW=1920&CH=1080&DPR=1&UTC=480; _EDGE_S=F=1; MUID=1234567890;" 
        }

    def get_pinyin_key(self, text):
        """将中文转换为拼音首字母 (例如: 凡人修仙传 -> frxxz)"""
        # 清理标题中的多余字符
        clean_text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        clean_text = re.sub(r'(最新章节|全文阅读|无弹窗|笔趣阁|顶点|小说|在线阅读|目录|官方)', '', clean_text)
        
        # 获取首字母
        try:
            initials = lazy_pinyin(clean_text, style=Style.FIRST_LETTER)
            key = ''.join(initials).lower()
            return key if key else "temp"
        except:
            return "temp"

    def search_bing(self, keyword):
        """搜索 Bing 并提取结果"""
        # 关键词加上 "目录" 提高命中率，过滤掉无关页面
        search_url = f"https://www.bing.com/search?q={keyword} 在线阅读&adppc=EdgeStart&PC=HCTS&mkt=zh-CN"
        
        print(f"[Search] Searching for: {search_url}")
        
        try:
            response = requests.get(search_url, headers=self.headers, timeout=10, verify=False)
            # 自动识别编码，防止乱码
            response.encoding = response.apparent_encoding 
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            results = []
            
            # 针对你提供的 HTML 文件结构：
            # <li class="b_algo"> -> <h2> -> <a href="...">
            items = soup.select(".b_algo")
            
            if not items:
                print("[Search] No items found in specific selector, trying fallback...")
                items = soup.select('li.b_algo') # 保底尝试

            for item in items:
                # 1. 找标题容器 h2
                # h2 = item.find('h2')
                h2 = item.select('.b_tpcn')[0]
                if not h2: continue
                
                # 2. 找链接 a
                link = h2.find('a')
                print(item)
                if not link: continue
                url = link.get('href')
                # get_text 会自动去掉 <strong> 等标签，只留纯文本
                title = link.get_text(strip=True)
                
                # 3. 简单的过滤：只要 http 开头的链接，且排除一些明显的广告或无关链接
                if url and url.startswith('http'):
                    # 排除 Bing 的相关搜索链接 (search?q=...)
                    if "bing.com/search" in url:
                        continue

                    results.append({
                        'title': title,
                        'url': url,
                        # 预先计算好可能的 Key
                        'suggested_key': self.get_pinyin_key(keyword) 
                    })
                    
                if len(results) >= 10: # 只取前10个结果
                    break
            
            print(f"[Search] Found {len(results)} results.")
            print(search_url)
            return results
            
        except Exception as e:
            print(f"[Search] Error: {e}")
            # 如果出错，返回空列表，前端会提示
            return []
# --- 1. 爬虫模块 (防封加强版) ---
class NovelCrawler:
    def __init__(self):
        # 准备一堆 User-Agent 轮换，伪装成不同的人
        self.ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        ]

    def _get_random_headers(self):
        return {
            "User-Agent": random.choice(self.ua_list),
            "Referer": "https://www.google.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }

    def fetch_page(self, url, retry=2):
        """带有重试和随机Header的下载"""
        for i in range(retry + 1):
            try:
                # 随机延迟，模拟人类操作 (下载时很重要)
                # 放在这里可能会影响普通阅读速度，所以下载逻辑单独做延迟，
                # 这里只做基础的请求
                response = requests.get(url, headers=self._get_random_headers(), timeout=15, verify=False)
                response.raise_for_status()
                response.encoding = response.apparent_encoding
                return response.text
            except Exception as e:
                print(f"[Crawler] Error fetching {url} (Try {i+1}): {e}")
                time.sleep(2) # 失败后稍微等一下
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
        # ... (保持你之前最新的 get_toc 代码，完全不用变) ...
        # 为了节省篇幅，这里复用你之前的逻辑，请确保你复制了包含"智能过滤"的那个版本
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
                is_chapter_likely = re.search(r'(第[0-9零一二三四五六七八九十百千万]+[章回节卷])|([0-9]+)', title)
                if not is_chapter_likely:
                    if title.endswith('小说') or title.endswith('文库'): continue
                    if any(x in title for x in ['点击榜', '推荐榜', '收藏榜', '新书榜', '排行榜', '搜索', '登录', '注册', '首页', '书架', '帮助', '客户端', 'TXT']): continue
                    if len(title) <= 3 and not is_chapter_likely: continue
                full_url = self._get_absolute_url(toc_url, href)
                if full_url: chapters.append({'title': title, 'url': full_url})
        return {'title': self._get_smart_title(soup) or "目录", 'chapters': chapters}

    def run(self, url):
        # ... (保持你之前最新的 run 代码，包含 toc_url 强力保底逻辑) ...
        html = self.fetch_page(url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        data = {'content': [], 'title': '', 'prev': None, 'next': None, 'toc_url': None}
        data['title'] = self._get_smart_title(soup)
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
            if url.endswith('.html') or url.endswith('.htm') or url.endswith('.php'): data['toc_url'] = url.rsplit('/', 1)[0] + '/'
            elif url.endswith('/'): data['toc_url'] = url.rstrip('/').rsplit('/', 1)[0] + '/'
            else: data['toc_url'] = url.rsplit('/', 1)[0] + '/'
        return data

# --- 2. 数据库逻辑 (保持不变) ---
# --- 2. 数据库逻辑 (带版本控制版) ---
class SimpleDB:
    def __init__(self, db_name="mydatabase"):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.filename = os.path.join(self.base_dir, db_name + ".db")
        
        # 创建备份目录
        self.backup_dir = os.path.join(self.base_dir, "backups")
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)
            
        self.data = {}
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue
                        # 兼容带冒号的内容，只分割第一个冒号
                        parts = line.split(':', 1)
                        if len(parts) == 2: self.data[parts[0]] = parts[1]
            except Exception as e:
                print(f"[DB] Load Error: {e}")

    def _manage_backups(self):
        """核心功能：创建备份并限制数量"""
        if not os.path.exists(self.filename):
            return

        # 1. 生成带时间戳的备份文件名 (精确到毫秒，防止操作太快文件名冲突)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"mydatabase_{timestamp}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)

        try:
            # 2. 复制当前数据库文件作为备份
            shutil.copy2(self.filename, backup_path)
            print(f"[Backup] Created: {backup_name}")

            # 3. 清理旧备份 (保留最新的10个)
            # 获取所有 .bak 文件
            pattern = os.path.join(self.backup_dir, "mydatabase_*.bak")
            backups = sorted(glob.glob(pattern)) # 默认按文件名排序（也就是按时间排序）
            
            max_versions = 10
            if len(backups) > max_versions:
                # 计算需要删除的数量
                to_delete = backups[:len(backups) - max_versions]
                for f in to_delete:
                    os.remove(f)
                    print(f"[Backup] Pruned old version: {os.path.basename(f)}")

        except Exception as e:
            print(f"[Backup] Error: {e}")

    def save(self):
        # === 在保存新数据之前，先对旧数据进行备份 ===
        self._manage_backups()

        # === 正常的保存逻辑 ===
        temp_file = self.filename + ".tmp"
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                # 排序后写入，保持文件整洁
                for key in sorted(self.data.keys()):
                    f.write(f"{key}:{self.data[key]}\n")
            
            # 原子操作：先写临时文件，成功后再重命名覆盖
            if os.path.exists(self.filename):
                os.remove(self.filename)
            os.rename(temp_file, self.filename)
        except Exception as e:
            print(f"[DB] Save Error: {e}")

    # 以下 CRUD 方法保持不变，它们会自动调用上面的 save()
    def insert(self, key, value):
        self.data[key] = value
        self.save() 
        return {"status": "success", "message": f"Saved: {key}", "data": {key: value}}

    def update(self, key, value):
        if key in self.data:
            self.data[key] = value
            self.save()
            return {"status": "success", "message": f"Updated: {key}", "data": {key: value}}
        return {"status": "error", "message": "Key not found!"}

    def remove(self, key):
        if key in self.data:
            del self.data[key]
            self.save()
            return {"status": "success"}
        return {"status": "error"}

    def list_all(self):
        user_data = {k: v for k, v in self.data.items() if not k.startswith('@')}
        return {"status": "success", "data": user_data}

    def find(self, term):
        res = {k:v for k,v in self.data.items() if term.lower() in k.lower() or term.lower() in v.lower()}
        return {"status": "success", "data": res} if res else {"status": "error"}

# --- 3. 下载管理器 (新增核心模块) ---
class DownloadManager:
    def __init__(self):
        self.downloads = {} # 存储下载任务状态
        self.downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
        if not os.path.exists(self.downloads_dir):
            os.makedirs(self.downloads_dir)

    def start_download(self, book_name, chapters):
        """开启一个后台线程进行下载"""
        task_id = hashlib.md5((book_name + str(time.time())).encode()).hexdigest()
        
        # 初始化任务状态
        self.downloads[task_id] = {
            'book_name': book_name,
            'total': len(chapters),
            'current': 0,
            'status': 'running',
            'filename': f"{self._sanitize_filename(book_name)}.txt",
            'start_time': time.time()
        }

        # 启动线程
        thread = threading.Thread(target=self._download_worker, args=(task_id, chapters))
        thread.daemon = True # 设置为守护线程，主程序退出时它也会退出
        thread.start()
        
        return task_id

    def _sanitize_filename(self, filename):
        return re.sub(r'[\\/*?:"<>|]', "", filename)

    def _download_worker(self, task_id, chapters):
        """后台下载逻辑"""
        task = self.downloads[task_id]
        filepath = os.path.join(self.downloads_dir, task['filename'])
        
        print(f"[Download] Starting task {task_id} for {task['book_name']}")

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                # 写入书名
                f.write(f"=== {task['book_name']} ===\n\n")
                
                for index, chap in enumerate(chapters):
                    url = chap['url']
                    title = chap['title']
                    
                    # 1. 尝试从缓存读取
                    article_data = cache.get(url)
                    
                    if not article_data:
                        # 2. 缓存没有，联网抓取
                        # 随机延迟，防止封IP (1.5s - 3.5s)
                        time.sleep(random.uniform(1.5, 3.5))
                        try:
                            article_data = crawler.run(url)
                            # 抓取成功，写入缓存
                            if article_data and article_data.get('content'):
                                cache.set(url, article_data)
                        except Exception as e:
                            print(f"[Download] Error crawling {title}: {e}")
                    
                    # 3. 写入文件
                    if article_data and article_data.get('content'):
                        f.write(f"\n\n=== {title} ===\n\n")
                        # content 是个列表，拼接起来
                        if isinstance(article_data['content'], list):
                            f.write('\n\n'.join(article_data['content']))
                        else:
                            f.write(article_data['content'])
                    else:
                        f.write(f"\n\n=== {title} (下载失败) ===\n\n")

                    # 更新进度
                    task['current'] = index + 1
            
            task['status'] = 'completed'
            print(f"[Download] Task {task_id} completed!")

        except Exception as e:
            task['status'] = 'error'
            task['error_msg'] = str(e)
            print(f"[Download] Task {task_id} failed: {e}")

    def get_status(self, task_id):
        return self.downloads.get(task_id)

# --- 4. Flask 服务器 ---

app = Flask(__name__)
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
app.template_folder = template_dir

db = SimpleDB("mydatabase")
crawler = NovelCrawler()
cache = CacheManager()
downloader = DownloadManager() # 初始化下载管理器

searcher = SearchHelper()

@app.route('/api/search_novel', methods=['POST'])
def api_search():
    keyword = request.json.get('keyword')
    if not keyword:
        return jsonify({"status": "error", "message": "请输入关键词"})
    
    results = searcher.search_bing(keyword)
    return jsonify({"status": "success", "data": results})

@app.route('/api/get_pinyin', methods=['POST'])
def api_pinyin():
    """辅助接口：如果用户手动修改了标题，可以重新生成Key"""
    text = request.json.get('text')
    key = searcher.get_pinyin_key(text)
    return jsonify({"status": "success", "key": key})

@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'))

# ... (保留 /read, /toc 等路由，这里只列出新增的) ...
@app.route('/read')
def read_mode():
    target_url = request.args.get('url')
    db_key = request.args.get('key', '')
    force_update = request.args.get('force') == 'true'
    if not target_url: return "Error: No URL provided."
    article_data = None
    if not force_update: article_data = cache.get(target_url)
    if not article_data:
        print(f"[Crawler] Fetching: {target_url}")
        article_data = crawler.run(target_url)
        if article_data and article_data.get('content'): cache.set(target_url, article_data)
    if not article_data: return "Error: Failed to fetch content."
    return render_template('reader.html', article=article_data, current_url=target_url, db_key=db_key)

@app.route('/toc')
def toc_page():
    toc_url = request.args.get('url')
    db_key = request.args.get('key', '')
    force_update = request.args.get('force') == 'true'
    if not toc_url: return "Error: No TOC URL provided."
    toc_data = None
    if not force_update: toc_data = cache.get(toc_url)
    if not toc_data:
        print(f"[Crawler] Fetching TOC: {toc_url}")
        toc_data = crawler.get_toc(toc_url)
        if toc_data and toc_data['chapters']: cache.set(toc_url, toc_data)
    if not toc_data: return f"Error: Failed to fetch TOC. <a href='{toc_url}'>Original Site</a>"
    return render_template('toc.html', toc=toc_data, toc_url=toc_url, db_key=db_key)

# === 新增：下载相关 API ===

# === 新增：精确获取 Key 对应的 Value (用于同步检测) ===
@app.route('/api/get_value', methods=['POST'])
def api_get_value():
    key = request.json.get('key')
    if key in db.data:
        return jsonify({"status": "success", "value": db.data[key]})
    return jsonify({"status": "error", "message": "Key not found"})

@app.route('/api/download', methods=['POST'])
def start_download():
    """启动下载任务"""
    data = request.json
    toc_url = data.get('toc_url')
    book_name = data.get('book_name', '未知小说')
    
    # 1. 再次获取目录 (确保最新)
    # 优先查缓存，没有则现抓
    toc_data = cache.get(toc_url)
    if not toc_data:
        toc_data = crawler.get_toc(toc_url)
    
    if not toc_data or not toc_data.get('chapters'):
        return jsonify({"status": "error", "message": "无法获取章节目录"})

    # 2. 启动后台任务
    task_id = downloader.start_download(book_name, toc_data['chapters'])
    
    return jsonify({"status": "success", "task_id": task_id})

@app.route('/api/download/status')
def download_status():
    """查询下载进度"""
    task_id = request.args.get('task_id')
    status = downloader.get_status(task_id)
    if not status:
        return jsonify({"status": "error", "message": "Task not found"})
    return jsonify(status)

@app.route('/api/download/file')
def download_file():
    """下载生成的 TXT 文件"""
    task_id = request.args.get('task_id')
    status = downloader.get_status(task_id)
    if not status or status['status'] != 'completed':
        return "File not ready", 404
    
    return send_from_directory(downloader.downloads_dir, status['filename'], as_attachment=True)

# ... (保持 insert, update 等 API 不变) ...
@app.route('/insert', methods=['POST'])
def insert(): return jsonify(db.insert(request.json.get('key'), request.json.get('value')))
@app.route('/update', methods=['POST'])
def update(): return jsonify(db.update(request.json.get('key'), request.json.get('value')))
@app.route('/remove', methods=['POST'])
def remove(): return jsonify(db.remove(request.json.get('key')))
@app.route('/find', methods=['POST'])
def find(): return jsonify(db.find(request.json.get('key', '')))

# === 新增：深度分析接口 ===
@app.route('/api/analyze_stats', methods=['GET'])
def api_analyze_stats():
    try:
        # 1. 基础数据
        all_books = [k for k in db.data.keys() if not k.startswith('@')]
        total_books = len(all_books)
        
        # 2. 缓存分析 (计算字数和活跃度)
        cache_files = glob.glob(os.path.join(cache.cache_dir, "*.json"))
        total_chapters = len(cache_files)
        
        # 估算总字数 (通过文件大小估算，避免打开所有文件导致慢)
        # 假设 JSON 中 70% 是正文，UTF-8 中文平均 3 字节
        total_size = sum(os.path.getsize(f) for f in cache_files)
        estimated_words = int(total_size * 0.7 / 3)
        
        # 3. 热力图数据 (过去30天活跃度)
        # 读取缓存文件的修改时间 (mtime)
        activity = {}
        now = time.time()
        days_30 = 30 * 24 * 3600
        
        for f in cache_files:
            mtime = os.path.getmtime(f)
            if now - mtime < days_30:
                date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                activity[date_str] = activity.get(date_str, 0) + 1
        
        # 填补最近30天的数据，没有阅读的日子填0
        heatmap_data = []
        today = datetime.now()
        for i in range(29, -1, -1):
            day = today - timedelta(days=i)
            date_str = day.strftime('%Y-%m-%d')
            count = activity.get(date_str, 0)
            heatmap_data.append({"date": date_str, "count": count})

        # 4. 口味提取 (简单的关键词频率)
        keywords = {}
        target_words = ["修仙", "重生", "系统", "穿越", "都市", "玄幻", "言情", "末世", "高武", "反派", "直播", "无限", "神豪", "娱乐"]
        
        # 从书名(Key)和Value(URL里的标题猜测)中提取
        # 这里简单分析 DB 中的 Key (通常是拼音) 和 cache 中的 title
        # 为了简单且丰富，我们分析 cache 中记录的书名
        
        # 随机抽样 50 个缓存文件来分析书名，避免全量遍历太慢
        sample_files = random.sample(cache_files, min(len(cache_files), 50))
        for f in sample_files:
            try:
                with open(f, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)
                    title = data.get('title', '')
                    for word in target_words:
                        if word in title:
                            keywords[word] = keywords.get(word, 0) + 1
            except: pass
            
        # 格式化关键词
        top_keywords = sorted(keywords.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return jsonify({
            "status": "success",
            "stats": {
                "books": total_books,
                "chapters": total_chapters,
                "words": estimated_words,
                "heatmap": heatmap_data,
                "keywords": top_keywords
            }
        })
    except Exception as e:
        print(f"Analysis Error: {e}")
        return jsonify({"status": "error", "message": str(e)})
@app.route('/list', methods=['POST'])
def list_all(): 
    # print("[Debug] ", db.list_all())
    return jsonify(db.list_all())
# === 新增：多端同步“最后阅读”接口 ===
@app.route('/api/last_read', methods=['GET', 'POST'])
def handle_last_read():
    # 获取最后阅读的书
    if request.method == 'GET':
        last_key = db.data.get('@last_read')
        return jsonify({"status": "success", "key": last_key})
    
    # 设置最后阅读的书
    if request.method == 'POST':
        key = request.json.get('key')
        if key:
            # 使用特殊的 Key 存储，不影响普通书签
            db.insert('@last_read', key) 
            return jsonify({"status": "success"})
        return jsonify({"status": "error"})



if __name__ == '__main__':
    if not os.path.exists(template_dir): os.makedirs(template_dir)
    print("Server running on http://0.0.0.0:5000") # 提示改为 0.0.0.0
    # host='0.0.0.0' 允许局域网其他设备访问
    app.run(debug=True, port=5000, host='0.0.0.0')