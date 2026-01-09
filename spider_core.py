import time
import random
import re
import os
import importlib.util
import hashlib
from urllib.parse import urljoin, urlparse 
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
    def _is_valid_novel_site(self, url):
        """
        [新增] 白名单校验：只允许长得像小说站的 URL 通过
        用于对抗 Bing 国内版的垃圾结果
        """
        u = url.lower()
        # 1. 必须包含 http
        if not u.startswith('http'): return False
        
        # 2. 排除知名垃圾站
        bad_domains = ['zhihu', 'douban', 'baidu', 'bilibili', 'video', 'news', '163.com', 'qq.com', 'sohu']
        if any(d in u for d in bad_domains): return False
        
        # 3. [核心] 必须包含小说站常见特征
        valid_signs = ['book', 'novel', 'read', 'shu', 'biqu', 'bqg', 'txt', '88', 'wx', 'du', 'yuedu', 'chapter']
        # 或者 URL 结构包含数字 (通常是书ID)
        has_id = bool(re.search(r'\d+', u))
        
        if any(s in u for s in valid_signs) or has_id:
            return True
        return False
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
        data = {'q': f"{keyword} 笔趣阁"}
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
    def _do_bing_cn_search(self, keyword):
        """
        [新增] Bing 国内版专用引擎 (直连可用)
        """
        print(f"[Search] Trying Bing CN (Direct): {keyword}")
        # 关键词强制加上 "笔趣阁"，这在国内最好用
        query = f"{keyword} 笔趣阁 在线阅读"
        url = "https://cn.bing.com/search"
        params = {'q': query}
        
        try:
            # 注意：不使用 proxies，强制直连
            resp = cffi_requests.get(
                url, params=params, 
                impersonate=self.impersonate, 
                timeout=8
            )
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # 宽容解析
            links = soup.select('li.b_algo h2 a') or soup.select('h2 a')
            results = []
            
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                
                # 严格的白名单过滤
                if not self._is_valid_novel_site(href):
                    continue

                results.append({
                    'title': self._clean_title(title),
                    'url': href,
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': 'Bing CN 🇨🇳'
                })
                if len(results) >= 8: break
            return results
        except Exception as e:
            print(f"[Search] Bing CN Error: {e}")
            return []
    def _do_360_search(self, keyword):
        """
        [主力] 360搜索 + 多线程并发解密
        """
        print(f"[Search] 🔍 [调试模式] 仅尝试 360搜索: {keyword}")
        url = "https://www.so.com/s"
        # 关键词加“目录”，结果更精准
        params = {'q': f"{keyword} 免费阅读 目录"} 
        
        try:
            resp = cffi_requests.get(
                url, params=params, 
                impersonate=self.impersonate, 
                timeout=self.timeout
            )
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            raw_results = []
            # 360 结果选择器
            links = soup.select('ul.result li.res-list h3 a')
            
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('data-url') or link.get('href')
                
                if not href: continue
                
                # 尝试从 URL 参数提取 (某些 360 链接是 ...?url=http%3A%2F%2F...)
                if "so.com/link" in href:
                    try:
                        from urllib.parse import parse_qs, urlparse
                        qs = parse_qs(urlparse(href).query)
                        if 'url' in qs: href = qs['url'][0]
                    except: pass

                if self._is_junk(title, href): continue
                
                # 先存下来，稍后并发解密
                raw_results.append({
                    'title': self._clean_title(title),
                    'url': href,
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': '360 🟢'
                })
                if len(raw_results) >= 8: break
            
            if not raw_results:
                print("[Search] 360 未找到初步结果")
                return []

            # 多线程并发解密真实 URL
            print(f"[Search] 正在并发解析 {len(raw_results)} 个 360 链接...")
            final_results = []
            
            with ThreadPoolExecutor(max_workers=8) as exe:
                future_to_item = {
                    exe.submit(self._resolve_real_url, item['url']): item 
                    for item in raw_results
                }
                
                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        real_url = future.result()
                        # 再次校验解密后的 URL 是否为小说站
                        if self._is_valid_novel_site(real_url):
                            item['url'] = real_url
                            final_results.append(item)
                    except: pass
            
            return final_results

        except Exception as e:
            print(f"[Search] 360 Error: {e}")
            return []
    def _resolve_real_url(self, url):
        """
        [新增] 解析 360/百度的加密跳转链接
        原理：发送请求但不跟随跳转 (allow_redirects=False)，直接读取 Location 头
        """
        # 如果不是加密链接，直接返回
        if "so.com/link" not in url and "baidu.com/link" not in url:
            return url
            
        try:
            # 必须禁止自动跳转，否则会下载整个目标网页，浪费流量和时间
            resp = cffi_requests.get(
                url, 
                impersonate=self.impersonate, 
                timeout=5, 
                allow_redirects=False 
            )
            
            # 检查状态码是否为 301/302 重定向
            if resp.status_code in [301, 302]:
                # 获取真实地址 (Location 头)
                real_url = resp.headers.get('Location') or resp.headers.get('location')
                if real_url:
                    return real_url
        except Exception as e: # <--- 这里加了空格，修复了语法错误
            print(f"[Search] 解析跳转失败: {e}")
            pass
            
        # 如果解析失败，为了不让程序崩溃，原样返回加密链接
        # 虽然这会导致前端可能打不开，但总比没有好
        return url
    def _do_sogou_search(self, keyword):
        print(f"[Search] 🚀 Bing 失败，正在尝试搜狗搜索: {keyword}")
        query = f"{keyword} 笔趣阁"
        url = "https://www.sogou.com/web"
        params = {'query': query}
        
        try:
            # 搜狗需要一个比较真实的 Referer
            headers = {
                "Referer": "https://www.sogou.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
            }
            resp = cffi_requests.get(
                url, params=params, 
                impersonate=self.impersonate, 
                headers=headers,
                timeout=10
            )
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # 搜狗的结构比较特殊，通常在 .rb-tit a 或 h3 a
            links = soup.select('.rb-tit a') or soup.select('h3 a')
            results = []
            
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                
                # 搜狗的 href 往往是经过混淆的 /link?url=...
                if not href: continue
                if not href.startswith('http'):
                    href = urljoin("https://www.sogou.com", href)

                # 简单过滤垃圾结果
                if self._is_junk(title, href): continue
                if not self._is_valid_novel_site(href): continue

                results.append({
                    'title': re.split(r'(-|_|\|)', title)[0].strip(),
                    'url': href,
                    'suggested_key': self.get_pinyin_key(keyword),
                    'source': 'Sogou 🐶'
                })
                if len(results) >= 8: break
            return results
        except Exception as e:
            print(f"[Search] Sogou Error: {e}")
            return []
    def _do_bing_search(self, keyword):
        url = "https://www.bing.com/search"
        params = {'q': f"{keyword} 笔趣阁", 'setmkt': 'en-US'}
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
    def _clean_title(self, title):
        """
        清洗标题：去除类似 "- 笔趣阁", "_无弹窗" 等后缀
        """
        if not title: return "未知标题"
        # 使用正则分割 - _ | 等符号，只取第一部分
        return re.split(r'(-|_|\|)', title)[0].strip()

    def _is_junk(self, title, url):
        """
        判断是否为垃圾结果（非小说内容）
        """
        t = title.lower()
        u = url.lower()
        
        # 1. 排除知名非小说域名
        bad_domains = ['zhihu.com', 'douban.com', 'baike.baidu.com', 'csdn.net', 'cnblogs.com', 'bilibili.com', 'tieba.baidu.com', '163.com', 'sohu.com', 'sina.com']
        if any(d in u for d in bad_domains): return True
        
        # 2. 排除明显非小说标题关键词
        bad_keywords = ['下载', 'txt下载', '精校版', '教程', '百科', '资讯', '手游', '攻略', '视频', '在线观看']
        if any(k in t for k in bad_keywords): return True
        
        return False
    # def search_bing(self, keyword):
    #     # 1. 策略 A：如果有代理，首选 DuckDuckGo 和 Bing 国际版
    #     # (这两个结果最干净，优先级最高)
    #     if self.proxies:
    #         res = self._do_ddg_search(keyword)
    #         if res: return res
            
    #         res = self._do_bing_search(keyword)
    #         if res: return res
            
    #     # 2. 策略 B：国内直连策略 (Bing CN -> 360 -> 百度)
        
    #     # 优先级 1: Bing 国内版 (cn.bing.com)
    #     # 尝试直连 Bing，如果服务器 IP 没被微软拉黑，这个结果最好
    #     res = self._do_bing_cn_search(keyword)
    #     if res and len(res) > 0:
    #         return res

    #     # 优先级 2: 360搜索 (So.com)
    #     # 如果 Bing 挂了（返回空），尝试 360（带多线程解密，机房IP通过率高）
    #     res = self._do_360_search(keyword)
    #     if res: return res
        
    #     # 优先级 3: 百度搜索 (Baidu)
    #     # 最后兜底，收录全但可能有广告或验证码
    #     return self._do_baidu_search(keyword)
    def search_bing(self, keyword):
        return self._do_360_search(keyword)
    def _resolve_real_url(self, url):
        """
        [新增] 解析 360/百度的加密跳转链接
        原理：发送请求但不跟随跳转 (allow_redirects=False)，直接读取 Location 头
        """
        # 如果不是加密链接，直接返回
        if "so.com/link" not in url and "baidu.com/link" not in url:
            return url
            
        try:
            # 必须禁止自动跳转，否则会下载整个目标网页，浪费流量和时间
            resp = cffi_requests.get(
                url, 
                impersonate=self.impersonate, 
                timeout=5, 
                allow_redirects=False 
            )
            
            # 检查状态码是否为 301/302 重定向
            if resp.status_code in [301, 302]:
                # 获取真实地址 (Location 头)
                real_url = resp.headers.get('Location') or resp.headers.get('location')
                if real_url:
                    return real_url
        except Exception as e: # <--- 这里加了空格，修复了语法错误
            print(f"[Search] 解析跳转失败: {e}")
            pass
            
        # 如果解析失败，为了不让程序崩溃，原样返回加密链接
        # 虽然这会导致前端可能打不开，但总比没有好
        return url

    # === [核心新增 2] 百度搜索 (Baidu) - 收录最全，作为备用 ===
    def _do_baidu_search(self, keyword):
        print(f"[Search] 🔍 尝试 百度搜索: {keyword}")
        url = "https://www.baidu.com/s"
        # 技巧：wd 必须带 "最新章节"，否则全是贴吧
        params = {'wd': f"{keyword} 小说 最新章节"}
        
        try:
            # 百度对 User-Agent 非常敏感，且对 Referer 有校验
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
                "Referer": "https://www.baidu.com/"
            }
            # 必须不带代理访问百度国内版，否则可能跳到验证码
            resp = cffi_requests.get(
                url, params=params, 
                impersonate=self.impersonate,
                headers=headers,
                timeout=6
            )
            
            # 检测是否被百度拦截
            if "wappass.baidu.com" in resp.url or "验证码" in resp.text:
                print("[Search] ⚠️ 触发百度验证码，跳过")
                return []

            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            
            # 百度的结果块通常是 c-container
            containers = soup.select('div.c-container')
            
            for box in containers:
                try:
                    # 提取标题链接
                    title_elem = box.select_one('h3 a') or box.select_one('a')
                    if not title_elem: continue
                    
                    title = title_elem.get_text(strip=True)
                    href = title_elem.get('href') # 这是百度的加密链接
                    
                    # 提取下方显示的真实域名 (辅助判断)
                    footer_text = box.get_text()
                    
                    # 强力过滤
                    if self._is_junk(title, ""): continue # URL是加密的，暂时只能检查标题
                    
                    # 百度特色：广告通常有 '广告' 字样
                    if "广告" in footer_text: continue

                    # 既然拿不到真实URL（需要再次请求解密，太慢），
                    # 我们这里做一个大胆的策略：
                    # 直接返回这个加密链接。
                    # 因为你的 NovelCrawler.run() 能够处理 302 跳转！
                    
                    results.append({
                        'title': self._clean_title(title),
                        'url': href, # 这是一个 http://www.baidu.com/link?url=...
                        'suggested_key': self.get_pinyin_key(keyword),
                        'source': 'Baidu 🔵'
                    })
                    if len(results) >= 6: break
                except: pass
                
            return results
        except Exception as e:
            print(f"[Search] Baidu Error: {e}")
            return []
# ==========================================
# 3. 小说爬虫 (NovelCrawler - 修复KeyError版)
# ==========================================
class NovelCrawler:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 15
        self.proxies = getproxies()
    # spider_core.py -> NovelCrawler 类内部
    # ==========================================
    # [新增] 智能换源核心逻辑
    # ==========================================
    # === [调试增强版] 搜索并返回可用源列表 ===
    def search_alternative_sources(self, book_name, target_chapter_id):
        print(f"\n[Switch] 🚀 启动换源流程")
        print(f"[Switch] 目标书名:《{book_name}》 (如果这是拼音，搜索绝对会失败！)")
        print(f"[Switch] 目标章节ID: {target_chapter_id}")
        
        # 1. 搜索
        from spider_core import searcher 
        search_results = searcher.search_bing(book_name)
        
        if not search_results:
            print("[Switch] ❌ 搜索引擎返回 0 个结果。请检查书名是否正确。")
            return []
            
        print(f"[Switch] 🔍 搜索引擎返回了 {len(search_results)} 个备选源")
        for i, res in enumerate(search_results):
            print(f"   [{i+1}] {res['title']} -> {res['url']}")

        valid_sources = []
        
        # 2. 定义验证任务 (带详细日志)
        def check_source(result):
            toc_url = result['url']
            domain = urlparse(toc_url).netloc
            print(f"[Switch] ⚡ 开始检查源: {domain} ...")
            
            try:
                # 抓取目录
                toc = self.get_toc(toc_url)
                if not toc or not toc.get('chapters'):
                    print(f"[Switch] ⚠️ 源 {domain} 目录解析失败或为空")
                    return None
                
                # 3. 寻找匹配 ID
                # 倒序查找
                # print(f"[Switch] 源 {domain} 共有 {len(toc['chapters'])} 章，正在比对 ID...")
                
                # 既然我们已经有了 parse_chapter_id，我们直接看能不能对上
                # 为了调试，我们打印一下该源最后一章的 ID，看看偏离多远
                last_chap = toc['chapters'][-1]
                # print(f"   -> {domain} 最后一章: ID={last_chap.get('id')} ({last_chap.get('name')})")

                for chap in reversed(toc['chapters']):
                    if chap.get('id') == target_chapter_id:
                        print(f"[Switch] ✅ 命中目标! [{domain}] -> {chap['name']}")
                        return {
                            "source": domain,
                            "url": chap['url'],
                            "title": chap['name'],
                            "toc_url": toc_url
                        }
            except Exception as e:
                print(f"[Switch] ❌ 检查源 {domain} 时发生异常: {e}")
            return None

        # 3. 并发验证
        candidates = search_results[:6]
        print(f"[Switch] 正在并发检查前 {len(candidates)} 个结果...")
        
        with ThreadPoolExecutor(max_workers=6) as exe:
            futures = [exe.submit(check_source, res) for res in candidates]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    valid_sources.append(res)
        
        print(f"[Switch] 🏁 流程结束，共找到 {len(valid_sources)} 个可用源")
        return valid_sources
    def _get_book_name(self, soup):
        """
        通用的小说名识别逻辑
        """
        # 1. 尝试从常见面包屑导航中提取
        # 匹配包含 'path', 'breadcrumb', 'crumb' 的 class 或 id
        path_box = soup.find(class_=re.compile(r'path|crumb|breadcrumb', re.I)) or \
                   soup.find(id=re.compile(r'path|crumb|breadcrumb', re.I))
        
        if path_box:
            links = path_box.find_all('a')
            # 逻辑：首页 > 分类 > 书名 > 章节名，通常倒数第二个或第三个是书名
            if len(links) >= 3:
                # 针对书香阁这种：首页(0) > 分类(1) > 书名(2) > 章节
                return links[2].get_text(strip=True)
            elif len(links) == 2:
                return links[1].get_text(strip=True)

        # 2. 尝试从 Meta Keywords 提取 (第一个词通常是书名)
        meta_kw = soup.find('meta', attrs={'name': 'keywords'})
        if meta_kw:
            kw = meta_kw.get('content', '').split(',')[0]
            if kw and len(kw) < 20: return kw

        # 3. 尝试从 Title 标签拆分
        if soup.title:
            t_text = soup.title.get_text(strip=True)
            # 常见格式：章节名_书名_站点名 或 书名_章节名
            if "_" in t_text:
                parts = t_text.split('_')
                for p in parts:
                    if "第" not in p and "章" not in p and "节" not in p:
                        # 剔除常见的后缀
                        name = re.sub(r'(小说|全文|阅读|最新章节|笔趣阁).*', '', p)
                        if len(name) > 1: return name.strip()

        return "未知书名"
    def search_and_switch_source(self, book_name, target_chapter_id):
        """
        根据书名和目标章节ID，全网搜索备选源，并寻找匹配的章节链接
        """
        print(f"[Switch] 正在为《{book_name}》第 {target_chapter_id} 章寻找新源...")
        
        # 1. 全网搜索备选源 (复用 SearchHelper)
        # 搜索关键词加上 "目录"，提高命中率
        from spider_core import searcher # 确保引用
        search_results = searcher.search_bing(book_name)
        
        if not search_results:
            print("[Switch] 未搜索到任何结果")
            return None

        # 2. 定义单个源的验证任务
        def check_source(result):
            toc_url = result['url']
            domain = urlparse(toc_url).netloc
            
            # 简单过滤：如果是当前正在使用的源(略)，或者明显不是小说站的，可以在这里过滤
            # 这里先不做复杂过滤，信任 SearchHelper 的黑名单
            
            try:
                # 抓取目录 (复用 get_toc，它会自动进行 ID 解析和排序)
                toc = self.get_toc(toc_url)
                if not toc or not toc.get('chapters'):
                    return None
                
                # 3. 在目录中二分查找或遍历寻找目标 ID
                # 因为我们已经排好序了，理论上二分更快，但列表不长，遍历也行
                for chap in toc['chapters']:
                    if chap.get('id') == target_chapter_id:
                        print(f"[Switch] ✅ 在 [{domain}] 找到匹配章节: {chap['name']}")
                        return {
                            "new_url": chap['url'],
                            "source_name": domain,
                            "chapter_title": chap['name']
                        }
            except Exception as e:
                # print(f"[Switch] 检查源 {domain} 失败: {e}")
                pass
            return None

        # 3. 并发验证 (速度至上)
        # 我们同时检查前 5 个搜索结果
        candidates = search_results[:6] 
        found_target = None
        
        with ThreadPoolExecutor(max_workers=6) as exe:
            futures = [exe.submit(check_source, res) for res in candidates]
            
            for future in as_completed(futures):
                res = future.result()
                if res:
                    found_target = res
                    # 只要找到一个能用的，立马停止其他任务（虽然线程池没法立刻kill，但我们可以break返回）
                    # 实际上为了最快响应，谁先返回就用谁
                    break
        
        return found_target
    def resolve_start_url(self, url):
        """
        [新增] 智能入口解析：如果给的是目录，自动转为第一章
        """
        print(f"[SmartURL] Analyzing: {url}")
        
        # 1. 特征预判：如果 URL 以 .html 结尾且包含数字，大概率是章节，直接返回
        # (这能节省一次网络请求)
        if re.search(r'\d+\.html$', url) and "index" not in url:
            return url
            
        # 2. 爬取页面分析
        # 这里的 run 会自动识别目录链接 (toc_url)
        # 我们利用 get_toc 方法，看看它是不是一个目录页
        
        try:
            # 尝试当做目录抓取
            toc_data = self.get_toc(url)
            
            # 如果抓到了大量章节，说明它确实是目录
            if toc_data and len(toc_data['chapters']) > 5:
                first_chap = toc_data['chapters'][0]['url']
                print(f"[SmartURL] 检测到目录页，自动跳转第一章: {first_chap}")
                return first_chap
                
            # 如果不是目录，说明可能是一个不带 .html 后缀的章节页 (如 xbqg77)
            # 或者爬虫没解析对，为了安全，原样返回
            return url
            
        except Exception as e:
            print(f"[SmartURL] Resolve Error: {e}")
            return url
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
        print(f"\n[Run] 🚀 开始处理 URL: {url}")
        
        # 1. 尝试匹配插件
        adapter = plugin_mgr.find_match(url)
        if adapter:
            print(f"[Run] ✨ 匹配到适配器: {adapter.__class__.__name__}")
            result = adapter.run(self, url)
            # 打印插件返回的书名
            print(f"[Run] 📦 插件返回书名: {result.get('book_name', '未获取')}")
            return result
        
        print(f"[Run] 🌐 未找到插件，使用通用逻辑...")
        # 2. 如果没插件，执行通用逻辑
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