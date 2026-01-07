import time
import random
import re
import os
import importlib.util
import hashlib
from urllib.parse import urljoin
from urllib.request import getproxies
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from lxml import html as lxml_html
from pypinyin import lazy_pinyin, Style
from concurrent.futures import ThreadPoolExecutor, as_completed
from ebooklib import epub
from werkzeug.utils import secure_filename
from shared import BASE_DIR, LIB_DIR

# ==========================================
# 0. 辅助工具
# ==========================================
def parse_chapter_id(text):
    if not text: return -1
    text = text.strip()
    match = re.search(r'(?:第)?\s*([0-9零一二三四五六七八九十百千万]+)\s*[章节回幕]', text)
    if match: return _smart_convert_int(match.group(1))
    match = re.search(r'^(\d+)', text)
    if match: return int(match.group(1))
    return -1

def _smart_convert_int(s):
    try: return int(s)
    except: pass
    common_map = {'零':0, '一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10, '百':100, '千':1000, '万':10000, '两':2}
    if len(s) == 1 and s in common_map: return common_map[s]
    res = 0
    unit = 1
    temp = 0
    for char in reversed(s):
        if char in common_map:
            val = common_map[char]
            if val >= 10:
                if val > unit: unit = val
                else: unit *= val
            else: temp += val * unit
    if temp == 0 and '十' in s: temp = 10
    return temp if temp > 0 else 0

# ==========================================
# 1. 插件管理器
# ==========================================
class AdapterManager:
    def __init__(self, folder="adapters"):
        self.folder = os.path.join(BASE_DIR, folder)
        self.adapters = []
        if not os.path.exists(self.folder): os.makedirs(self.folder)
        self.load_plugins()

    def load_plugins(self):
        self.adapters = []
        for f in os.listdir(self.folder):
            if f.endswith(".py") and f != "__init__.py":
                try:
                    spec = importlib.util.spec_from_file_location(f[:-3], os.path.join(self.folder, f))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    for n in dir(mod):
                        obj = getattr(mod, n)
                        if isinstance(obj, type) and "Adapter" in n: self.adapters.append(obj())
                except: pass
        print(f"[System] 已加载 {len(self.adapters)} 个站点适配插件")

    def find_match(self, url):
        for a in self.adapters:
            if hasattr(a, 'can_handle') and a.can_handle(url): return a
        return None

plugin_mgr = AdapterManager()

# ==========================================
# 2. 搜索助手
# ==========================================
class SearchHelper:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 10
        self.proxies = self._get_proxies()
    
    def _get_proxies(self):
        try: return getproxies()
        except: return None

    def get_pinyin_key(self, text):
        clean = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        clean = re.sub(r'(小说|笔趣阁|最新章节|全文阅读)', '', clean)
        try:
            s = lazy_pinyin(clean, style=Style.FIRST_LETTER)
            k = ''.join(s).lower()
            return k[:15] if k else "temp"
        except: return "temp"

    def _is_junk(self, title, url):
        t = title.lower()
        u = url.lower()
        bad_domains = ['facebook', 'twitter', 'zhihu', 'douban', 'baidu', 'baike', 'csdn', 'cnblogs', 'youtube', 'bilibili', '52pojie', '163.com', 'sohu', 'microsoft', 'google', 'apple', 'amazon']
        if any(d in u for d in bad_domains): return True
        bad_keywords = ['工具', '破解', '软件', '下载', '教程', '视频', '剧透', '百科', '资讯', '手游', '官网', 'APP']
        if any(k in t for k in bad_keywords): return True
        return False

    def _do_ddg_search(self, keyword):
        url = "https://html.duckduckgo.com/html/"
        data = {'q': f"{keyword} 笔趣阁 目录"}
        try:
            resp = cffi_requests.post(url, data=data, impersonate=self.impersonate, timeout=self.timeout, proxies=self.proxies)
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            for link in soup.find_all('a', class_='result__a'):
                title = link.get_text(strip=True)
                href = link.get('href')
                if not href.startswith('http'): continue
                if self._is_junk(title, href): continue
                results.append({
                    'title': re.split(r'(-|_|\|)', title)[0].strip(),
                    'url': href,
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': 'DuckDuckGo 🦆'
                })
                if len(results) >= 8: break
            return results
        except: return None

    def _do_bing_search(self, keyword):
        url = "https://www.bing.com/search"
        params = {'q': f"{keyword} 笔趣阁 目录", 'setmkt': 'en-US'}
        try:
            resp = cffi_requests.get(url, params=params, impersonate=self.impersonate, timeout=self.timeout, proxies=self.proxies)
            soup = BeautifulSoup(resp.content, 'html.parser')
            links = soup.select('li.b_algo h2 a') or soup.select('li h2 a') or soup.select('h2 a')
            results = []
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                if not href.startswith('http') or self._is_junk(title, href): continue
                results.append({
                    'title': re.split(r'(-|_|\|)', title)[0].strip(),
                    'url': href,
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': 'Bing 🌐'
                })
                if len(results) >= 8: break
            return results
        except: return []

    def search_bing(self, keyword):
        return self._do_ddg_search(keyword) or self._do_bing_search(keyword)

# ==========================================
# 3. 小说爬虫 (NovelCrawler - 修复KeyError版)
# ==========================================
class NovelCrawler:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 15
        self.proxies = getproxies()

    def _fetch_page_smart(self, url, retry=3):
        """基础请求：增强了对 lxml 解析错误的捕获"""
        for i in range(retry):
            try:
                headers = {
                    "Referer": url, 
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
                }
                resp = cffi_requests.get(url, impersonate=self.impersonate, timeout=self.timeout, headers=headers, allow_redirects=True, proxies=self.proxies)
                
                # 1. 尝试 lxml 解析 (速度快，但对编码敏感)
                try:
                    # [修复] 增加 parser 参数，容错率更高
                    tree = lxml_html.fromstring(resp.content, parser=lxml_html.HTMLParser(encoding='utf-8'))
                    charset = tree.xpath('//meta[contains(@content, "charset")]/@content') or tree.xpath('//meta/@charset')
                    enc = 'utf-8'
                    if charset:
                        match = re.search(r'charset=([\w-]+)', str(charset[0]), re.I)
                        enc = match.group(1) if match else charset[0]
                    return resp.content.decode(enc)
                except Exception:
                    # 如果 lxml 失败，安静地进入下面的暴力尝试，不打印错误
                    pass
                
                # 2. 暴力尝试常见编码
                for e in ['utf-8', 'gb18030', 'gbk', 'big5']:
                    try: return resp.content.decode(e)
                    except: continue
                
                # 3. 最后兜底
                return resp.content.decode('utf-8', errors='replace')
            except: 
                time.sleep(1)
        return None

    def _get_smart_title(self, soup):
        h1_title = soup.find('h1', class_=re.compile(r'title|chapter|book|name', re.I))
        if h1_title: return h1_title.get_text(strip=True)
        h1s = soup.find_all('h1')
        for h in h1s:
            txt = h.get_text(strip=True)
            if len(txt) <= 4 or any(x in txt for x in ["笔趣阁", "小说网", "阅读器"]):
                if "logo" in str(h.get('class', '')).lower(): continue
                if h.find_parent(['nav', 'header']): continue
            return txt
        if soup.title: return re.split(r'[_—|-]', soup.title.get_text(strip=True))[0].strip()
        return "未知章节"

    def _clean_text_lines(self, text):
        if not text: return []
        junk = [r"一秒记住", r"最新章节", r"笔趣阁", r"上一章", r"下一章", r"加入书签", r"投推荐票", r"本章未完", r"未完待续", r"ps:"]
        lines = []
        for line in text.split('\n'):
            line = line.replace('\xa0', ' ').strip()
            if not line or len(line) < 2: continue
            if len(line) < 50 and any(re.search(p, line, re.I) for p in junk): continue
            if "{" in line and "function" in line: continue
            lines.append(line)
        return lines

    def _extract_content_smart(self, soup):
        for cid in ['txt', 'content', 'chaptercontent', 'BookText', 'showtxt', 'nr1', 'read-content']:
            div = soup.find(id=cid)
            if div:
                for a in div.find_all('a'): a.decompose()
                return self._clean_text_lines(div.get_text('\n'))
        best_div, max_score = None, 0
        for div in soup.find_all('div'):
            if div.get('id') and re.search(r'(nav|foot|header|menu)', str(div.get('id')), re.I): continue
            txt = div.get_text(strip=True)
            score = len(txt) - (len(div.find_all('a')) * 5)
            if score > max_score: max_score, best_div = score, div
        return self._clean_text_lines(best_div.get_text('\n')) if best_div else ["正文解析失败"]

    def _parse_chapters_from_soup(self, soup, base_url):
        links = []
        max_valid_links = 0
        containers = soup.find_all(['div', 'ul', 'dl', 'tbody'])
        if not containers: containers = [soup.body]
        
        junk_keywords = ['最新章节', '全文阅读', '无弹窗', '小说', '笔趣阁', '加入书架', '投推荐票', '作家', '作者']

        for container in containers:
            if container.get('class') and any(x in str(container.get('class')) for x in ['nav', 'footer', 'header', 'hot', 'recommend']): continue
            temp_links = []
            for a in container.find_all('a'):
                raw_text = a.get_text(strip=True)
                href = a.get('href')
                if not href: continue
                if any(k in raw_text for k in junk_keywords) and not re.search(r'\d', raw_text): continue
                
                chap_id = parse_chapter_id(raw_text)
                is_valid = False
                if chap_id > 0: is_valid = True
                elif len(raw_text) > 2 and any(x in raw_text for x in ['章', '节', '回', '幕']) and not any(k in raw_text for k in junk_keywords): is_valid = True
                
                if is_valid:
                    full_url = urljoin(base_url, href)
                    match_name = re.search(r'(?:第)?\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回](.*)', raw_text)
                    pure_name = match_name.group(1).strip() if match_name else raw_text
                    if full_url:
                        # 注意：这里我们生成字典时不带 'title' 键，统一由 _standardize_chapters 处理
                        temp_links.append({'id': chap_id, 'raw_title': raw_text, 'name': pure_name, 'url': full_url})
            
            if len(temp_links) > max_valid_links: max_valid_links = len(temp_links); links = temp_links
        return links

    def _standardize_chapters(self, raw_chapters):
        unique = {c['url']: c for c in raw_chapters}
        processed_list = []
        for c in unique.values():
            raw_title = c.get('title') or c.get('raw_title') or ""
            if any(x in raw_title for x in ['最新章节', '全文阅读', '无弹窗', 'txt下载']) and not re.search(r'\d', raw_title): continue
            chap_id = parse_chapter_id(raw_title)
            pure_name = re.sub(r'^(?:第)?\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回]', '', raw_title).strip()
            pure_name = re.sub(r'^\d+\s*\.?\s*', '', pure_name).strip()
            
            c['id'] = chap_id
            c['name'] = pure_name or raw_title
            c['raw_title'] = raw_title
            c['title'] = raw_title # [核心修复] 补上这个键，防止后端报错
            processed_list.append(c)
            
        numbered = [c for c in processed_list if c['id'] > 0]
        others = [c for c in processed_list if c['id'] <= 0]
        numbered.sort(key=lambda x: x['id'])
        
        if len(numbered) > 10:
            final_chapters = numbered
            prologues = [c for c in others if "序" in c['raw_title'] or "引" in c['raw_title']]
            final_chapters = prologues + final_chapters
        else: final_chapters = others + numbered
        return final_chapters

    def get_toc(self, toc_url):
        adapter = plugin_mgr.find_match(toc_url)
        if adapter: data = adapter.get_toc(self, toc_url)
        else: data = self._general_toc_logic(toc_url)
        
        if not data or not data.get('chapters'): return None
        final_chapters = self._standardize_chapters(data['chapters'])
        return {'title': data['title'], 'chapters': final_chapters}

    def _general_toc_logic(self, toc_url):
        html = self._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        raw_chapters = self._parse_chapters_from_soup(soup, toc_url)
        
        pages = set()
        for s in soup.find_all('select'):
            for o in s.find_all('option'):
                v = o.get('value')
                if v:
                    f = urljoin(toc_url, v)
                    if f.rstrip('/') != toc_url.rstrip('/'): pages.add(f)
        if pages:
            with ThreadPoolExecutor(max_workers=5) as exe:
                results = exe.map(lambda u: self._parse_chapters_from_soup(BeautifulSoup(self._fetch_page_smart(u) or "", 'html.parser'), toc_url), sorted(list(pages)))
                for sub in results: raw_chapters.extend(sub)
        return {'title': self._get_smart_title(soup), 'chapters': raw_chapters}

    def get_latest_chapter(self, toc_url):
        toc_data = self.get_toc(toc_url)
        if not toc_data or not toc_data.get('chapters'): return None
        chapters = toc_data['chapters']
        last_chapter = chapters[-1]
        # 兼容性处理
        return {
            "title": last_chapter.get('name', last_chapter.get('raw_title', '未知章节')),
            "url": last_chapter['url'],
            "id": last_chapter.get('id', -1),
            "total_chapters": len(chapters)
        }

    def run(self, url):
        adapter = plugin_mgr.find_match(url)
        if adapter: return adapter.run(self, url)
        return self._general_run_logic(url)
    
    def _general_run_logic(self, url):
        base_url = url
        if "_" in url:
            normalized = re.sub(r'_\d+\.html', '.html', url)
            if normalized != url: base_url = normalized
        combined_content = []
        first_page_meta = None
        current_url = base_url
        visited_urls = {url, base_url}
        max_pages, page_count = 8, 0
        original_title = ""
        chap_id_match = re.search(r'/(\d+)(?:_\d+)?\.html', base_url)
        current_chap_id = chap_id_match.group(1) if chap_id_match else ""
        while page_count < max_pages:
            html = self._fetch_page_smart(current_url)
            if not html: break
            soup = BeautifulSoup(html, 'html.parser')
            current_title = self._get_smart_title(soup)
            if page_count == 0: original_title = current_title
            elif current_title != original_title and len(current_title) > 3: break
            content = self._extract_content_smart(soup)
            if content and original_title in content[0]: content = content[1:]
            combined_content.extend(content)
            next_page_url, next_chapter_url, prev_chapter_url, toc_url = None, None, None, None
            for a in soup.find_all('a'):
                txt = a.get_text(strip=True).replace(' ', '')
                href = a.get('href')
                if not href or href.startswith('javascript'): continue
                full = urljoin(current_url, href)
                if "下一页" in txt or "下—页" in txt or re.search(r'\(\d+/\d+\)', txt):
                    if current_chap_id and current_chap_id in href: next_page_url = full
                    else: next_chapter_url = full
                elif "下一章" in txt or "下章" in txt: next_chapter_url = full
                if page_count == 0:
                    if "上一章" in txt or "上章" in txt: prev_chapter_url = full
                    elif "上一页" in txt or "上页" in txt:
                        if current_chap_id and current_chap_id not in href: prev_chapter_url = full
                if "目录" in txt: toc_url = full
            for aid in ['pb_prev', 'prev_url', 'pb_next', 'next_url', 'pb_mulu']:
                tag = soup.find(id=aid)
                if not tag or not tag.get('href'): continue
                t_url = urljoin(current_url, tag['href'])
                if 'prev' in aid and page_count == 0 and not prev_chapter_url:
                    if current_chap_id and current_chap_id not in tag['href']: prev_chapter_url = t_url
                elif 'next' in aid and not next_chapter_url:
                    if current_chap_id and current_chap_id in tag['href']: next_page_url = t_url
                    else: next_chapter_url = t_url
                elif 'mulu' in aid and not toc_url: toc_url = t_url
            if page_count == 0: first_page_meta = {'title': original_title, 'prev': prev_chapter_url, 'toc_url': toc_url}
            if next_page_url and next_page_url not in visited_urls:
                current_url = next_page_url
                visited_urls.add(next_page_url)
                page_count += 1
            else:
                first_page_meta['next'] = next_chapter_url
                break
        if first_page_meta:
            first_page_meta['content'] = combined_content
            return first_page_meta
        return None

    def get_first_chapter(self, toc_url):
        res = self.get_toc(toc_url)
        return res['chapters'][0]['url'] if res and res['chapters'] else None

# ... (EpubHandler 保持不变) ...
class EpubHandler:
    def __init__(self):
        self.lib_dir = LIB_DIR
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

# 实例化对象
crawler_instance = NovelCrawler()
searcher = SearchHelper()
epub_handler = EpubHandler()