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
from curl_cffi import requests as cffi_requests, CurlHttpVersion

# ==========================================
# 0. 辅助工具 (中文数字转阿拉伯数字 - 增强版)
# ==========================================
def parse_chapter_id(text):
    if not text: return -1
    text = text.strip()
    
    # 1. 优先匹配纯数字 (例如: "49. 章节名" 或 "第49章")
    match_num = re.search(r'(?:第)?\s*(\d+)\s*[章节回幕\.]', text)
    if match_num: 
        return int(match_num.group(1))
        
    # 2. 匹配中文数字 (例如: "第十一章")
    # 注意：这里把两、千、万等都加全了
    match_cn = re.search(r'(?:第)?\s*([零一二两三四五六七八九十百千万]+)\s*[章节回幕]', text)
    if match_cn: 
        return _smart_convert_int(match_cn.group(1))
        
    # 3. 实在不行，匹配开头的数字 (例如 "123 章节名")
    match_start = re.search(r'^(\d+)', text)
    if match_start: 
        return int(match_start.group(1))
        
    return -1

def _smart_convert_int(s):
    """
    将中文数字转换为阿拉伯数字 (支持: 十一 -> 11, 一百零五 -> 105)
    """
    # 尝试直接转数字 (防止传入的是 "123")
    try: return int(s)
    except: pass

    # 映射表
    cn_nums = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, 
               '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    cn_units = {'十': 10, '百': 100, '千': 1000, '万': 10000}

    # [核心修复] 特殊处理以"十"开头的数字 (如: 十一 => 一十一, 十五 => 一十五)
    if s.startswith('十'):
        s = '一' + s

    result = 0
    temp_val = 0 # 暂存当前读取的数字
    
    for char in s:
        if char in cn_nums:
            temp_val = cn_nums[char]
        elif char in cn_units:
            unit = cn_units[char]
            if unit >= 10000:
                # 处理"万"这种大单位，先结算前面的
                result = (result + temp_val) * unit
                temp_val = 0
            else:
                # 处理"十/百/千"
                result += temp_val * unit
                temp_val = 0
    
    # 加上最后剩下的个位数
    result += temp_val
    return result
    

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
from functools import lru_cache
import requests
# from curl_cffi import requests as CurlHttpVersion
# ==========================================
# 2. 搜索助手
# ==========================================


# ==========================================
# 2. 搜索助手 (调试增强版)
# ==========================================import re
import time
from urllib.parse import urlparse, parse_qs, unquote, urljoin
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from pypinyin import lazy_pinyin, Style
from concurrent.futures import ThreadPoolExecutor, as_completed

class SearchHelper:
    def __init__(self):
        # [Owllook 配置] 模拟 Chrome 指纹，这是过盾的关键
        self.impersonate = "chrome110" 
        self.timeout = 10
        
        # [Owllook 移植] 域名黑名单 (Black Domain)
        # 来源: owllook/config/config.py
        self.black_domains = {
            'baidu.com', 'tieba.baidu.com', 'zhidao.baidu.com', 'wenku.baidu.com',
            # 'so.com', 'baike.so.com', 'wenda.so.com',
            'zhihu.com', 'douban.com', '163.com', 'qq.com', 'sina.com.cn',
            'amazon.cn', 'dangdang.com', 'jd.com', 'tmall.com', 'taobao.com',
            # 'qidian.com', 'zongheng.com', '17k.com', 'faloo.com', 'jjwxc.net',
            'facebook.com', 'twitter.com', 'youtube.com', 'bilibili.com'
        }
        self.plugins = []
        self._load_search_plugins()
        self.sites = [
            {
                "name": "笔趣阁.cc", 
                "url": "https://www.biquge.cc", 
                "search": "/search.php", 
                "param": "q", 
                "encoding": "gbk" # GBK编码站点
            },
            {
                "name": "笔趣卡", 
                "url": "https://www.bqgka.com", 
                "search": "/search.php", 
                "param": "q", 
                "encoding": "utf-8"
            },
            {
                "name": "52小说", 
                "url": "https://www.52bqg.cc", 
                "search": "/modules/article/search.php", 
                "param": "searchkey", 
                "encoding": "gbk"
            },
            {
                "name": "新笔趣阁", 
                "url": "https://www.xbiquge.so", 
                "search": "/search.php", 
                "param": "keyword", 
                "encoding": "utf-8"
            },
            {
                "name": "23小说", 
                "url": "https://www.23us.so", 
                "search": "/files/article/search.html", 
                "param": "searchkey", 
                "encoding": "gbk"
            }
        ]

    def _search_single_site(self, site, keyword):
        """搜索单个站点"""
        results = []
        try:
            # 1. 编码处理
            if site['encoding'] == 'gbk':
                # GBK 站点通常需要手动编码参数
                kw_val = keyword.encode('gbk')
            else:
                kw_val = keyword

            params = {site['param']: kw_val}
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': site['url']
            }

            # 2. 发起请求 (短超时，快速失败)
            resp = requests.get(
                f"{site['url']}{site['search']}", 
                params=params, 
                headers=headers, 
                timeout=6, 
                verify=False
            )
            
            # 3. 强制设置编码防止乱码
            resp.encoding = site['encoding']
            
            # 4. 通用解析逻辑
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # 尝试匹配常见的笔趣阁列表结构
            items = []
            # 结构A: .result-list .result-item (xbiquge类)
            items.extend(soup.select('.result-list .result-item'))
            # 结构B: .grid tr (杰奇CMS类)
            items.extend(soup.select('tr')) 
            # 结构C: .novelslist2 li (部分老站)
            items.extend(soup.select('li'))

            for item in items:
                try:
                    # 尝试寻找链接
                    link = item.find('a', href=True)
                    if not link: continue
                    
                    href = link['href']
                    title = link.get_text(strip=True)
                    
                    # 过滤无效链接
                    if not title or len(title) < 2: continue
                    if "小说" in title and len(title) > 20: continue # 过滤导航栏
                    
                    # 模糊匹配：只有包含关键词才收录 (防止解析到页眉页脚)
                    if keyword not in title: continue

                    # 提取作者 (尝试找附近的文本)
                    text_content = item.get_text()
                    author = "未知"
                    if "作者：" in text_content:
                        author = text_content.split("作者：")[1].split()[0].strip()
                    elif item.find_next_sibling('td'): # 表格结构作者在下一列
                        author = item.find_next_sibling('td').get_text(strip=True)

                    # URL 补全
                    if not href.startswith('http'):
                        href = urljoin(site['url'], href)
                    
                    # 修正目录页 (部分站点搜出来是详情页 /book/123/，需要转 /123/)
                    # 这里保持原样，交给爬虫核心去纠错，或者简单替换
                    
                    results.append({
                        'title': title,
                        'url': href,
                        'source': f"{site['name']} 📚",
                        'description': f"作者: {author}"
                    })
                    
                    if len(results) >= 3: break # 每个站只取前3个
                except: continue

        except Exception as e:
            # print(f"[Universal] {site['name']} Error: {e}")
            pass
            
        return results

    def search(self, keyword):
        print(f"[Plugin] 🚀 启动笔趣阁聚合搜索 ({len(self.sites)}个源)...")
        all_results = []
        
        # 线程池并发搜索所有源
        with ThreadPoolExecutor(max_workers=5) as exe:
            futures = [exe.submit(self._search_single_site, site, keyword) for site in self.sites]
            
            for future in as_completed(futures):
                res = future.result()
                if res:
                    all_results.extend(res)
        
        return all_results

    def _load_search_plugins(self):
        """动态加载 search_plugins 目录下的所有插件"""
        plugin_dir = os.path.join(BASE_DIR, 'search_plugins')
        if not os.path.exists(plugin_dir):
            os.makedirs(plugin_dir)
            return

        print(f"[System] 正在加载搜索插件...")
        for filename in os.listdir(plugin_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                try:
                    # 动态导入模块
                    module_name = filename[:-3]
                    file_path = os.path.join(plugin_dir, filename)
                    
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # 寻找插件类 (约定类名为 SourceWorker)
                    if hasattr(module, 'SourceWorker'):
                        plugin_instance = module.SourceWorker()
                        self.plugins.append(plugin_instance)
                        print(f"  -> 已加载源: {plugin_instance.source_name}")
                except Exception as e:
                    print(f"  -> 插件 {filename} 加载失败: {e}")
        
        print(f"[System] 共加载 {len(self.plugins)} 个直连搜索源")
    
    def get_pinyin_key(self, text):
        clean = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        clean = re.sub(r'(小说|笔趣阁|最新章节|全文阅读)', '', clean)
        try:
            s = lazy_pinyin(clean, style=Style.FIRST_LETTER)
            k = ''.join(s).lower()
            return k[:15] if k else "temp"
        except: return "temp"

    def _clean_title(self, title):
        if not title: return "未知标题"
        return re.split(r'(-|_|\|)', title)[0].strip()

    def _is_valid_result(self, title, url):
        """
        [Owllook 移植] 结果校验逻辑
        """
        if not url or not url.startswith('http'): return False
        
        netloc = urlparse(url).netloc
        
        # 1. 黑名单校验
        for domain in self.black_domains:
            if domain in netloc: return False
            
        # 2. 必须是 html 结尾或者是目录页 (Owllook 偏好)
        # if '.html' not in url and not url.endswith('/'): return False
        
        # 3. 关键词校验
        bad_keywords = ['下载', 'txt', '精校', '百科', '手游', '视频', '在线观看']
        if any(k in title.lower() for k in bad_keywords): return False
        
        return True

    def _get_real_url(self, url):
        """
        [Owllook 移植] 解析真实 URL (Get Real URL)
        核心：处理百度和360的加密跳转链接
        """
        # 如果不是加密链，直接返回
        if "baidu.com/link" not in url and "so.com/link" not in url:
            return url
            
        try:
            # 1. 尝试 HEAD 请求 (Owllook 策略: async with client.head...)
            # 禁止自动跳转，只看 Location
            resp = cffi_requests.head(
                url, 
                impersonate=self.impersonate, 
                timeout=5, 
                allow_redirects=False
            )
            
            if resp.status_code in [301, 302]:
                real_url = resp.headers.get('Location') or resp.headers.get('location')
                if real_url and "baidu.com" not in real_url and "so.com" not in real_url:
                    return real_url

            # 2. 如果 HEAD 失败，尝试 GET (针对 360 的 JS 跳转)
            resp = cffi_requests.get(
                url,
                impersonate=self.impersonate,
                timeout=8,
                allow_redirects=False
            )
            
            if resp.status_code == 200:
                html = resp.text
                # 360 特有的 JS 跳转提取
                js_match = re.search(r"window\.location\.replace\(['\"](.+?)['\"]", html)
                if js_match: return js_match.group(1)
                
                meta_match = re.search(r'url=([^"]+)"', html, re.IGNORECASE)
                if meta_match: return meta_match.group(1)

        except Exception: 
            pass
            
        return url

    # ==========================================
    # 引擎 1: 360搜索 (SoNovels)
    # ==========================================
    def _do_so_search(self, keyword):
        print(f"[Search] 🔍 启动 Owllook-360 引擎: {keyword}")
        url = "https://www.so.com/s"
        # Owllook 参数: ie=utf-8, src=noscript_home, shb=1
        params = {'q': keyword, 'ie': 'utf-8', 'src': 'noscript_home', 'shb': 1, 'pn': 1}
        
        try:
            res = []
            for i in range(1, 3) :
                params['pn'] = i
                resp = cffi_requests.get(url, params=params, impersonate=self.impersonate, timeout=self.timeout)
                soup = BeautifulSoup(resp.content, 'html.parser')
                
                raw_results = []
                # Owllook 选择器: .res-list
                items = soup.select('.res-list')
                print(len(items))
                for item in items:
                    try:
                        title_tag = item.select_one('h3 a')
                        if not title_tag: continue
                        
                        title = title_tag.get_text(strip=True)
                        href = title_tag.get('href')
                        
                        # Owllook: 针对不同的请求进行 url 的提取
                        if "www.so.com/link?m=" in href:
                            href = title_tag.get('data-mdurl') or href
                        if "www.so.com/link?url=" in href:
                            qs = parse_qs(urlparse(href).query)
                            if 'url' in qs: href = qs['url'][0]
                        
                        # if self._is_valid_result(title, href):
                        if True:
                            raw_results.append({
                                'title': self._clean_title(title),
                                'url': href, # 可能是加密链，稍后解析
                                'suggested_key': self.get_pinyin_key(keyword),
                                'source': '360 (Owllook)'
                            })

                    except: continue
                    for item in raw_results :
                        res.append(item)
                    if len(raw_results) >= 10: break
            return self._concurrent_resolve(res)
        except Exception as e:
            print(f"[Search] So Error: {e}")
            return []
            

        
    def _resolve_real_url(self, url):
        """
        [核心修复] 解析真实 URL
        针对服务器 IP，360 经常返回一个 200 OK 的中间页，而不是 302 跳转
        """
        if "so.com/link" not in url and "baidu.com/link" not in url:
            return url
            
        try:
            # 这里使用标准 requests，因为处理重定向和 header 比较方便且稳定
            # timeout 设置短一点，快速失败
            resp = requests.get(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'},
                timeout=6, 
                allow_redirects=False, # 禁止自动跳转，我们要拦截第一跳
                verify=False
            )
            
            # 情况 1: 标准 302 跳转
            if resp.status_code in [301, 302]:
                return resp.headers.get('Location') or url
            
            # 情况 2: 服务器 IP 常见的 "正在跳转..." 中间页
            if resp.status_code == 200:
                html = resp.text
                # 提取 window.location.replace("...")
                js_match = re.search(r"window\.location\.replace\(['\"](.+?)['\"]", html)
                if js_match: 
                    return js_match.group(1)
                
                # 提取 <meta http-equiv="refresh" content="0;url=...">
                meta_match = re.search(r'url=([^"]+)"', html, re.IGNORECASE)
                if meta_match: 
                    return meta_match.group(1)

        except Exception:
            pass
            
        # 解析失败返回原加密链接，后续会被清洗掉
        return url
    # ==========================================
    # 引擎 2: 百度搜索 (BaiduNovels)
    # ==========================================
    def _do_baidu_search(self, keyword):
        print(f"[Search] 🔍 启动 Owllook-Baidu 引擎: {keyword}")
        url = "https://www.baidu.com/s"
        
        # [Owllook 参数]
        # rn: 每页条数 (Owllook 设为 15，我们设 10)
        # vf_bl: 1 (这个参数很重要，有时能减少广告)
        params = {'wd': f"{keyword} 小说 最新章节", 'ie': 'utf-8', 'rn': 10, 'vf_bl': 1}
        
        try:
            # 百度反爬较严，必须带 Referer
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
                'Referer': 'https://www.baidu.com/'
            }
            # 使用 curl_cffi 模拟指纹，通过率比 requests 高
            resp = cffi_requests.get(url, params=params, headers=headers, impersonate=self.impersonate, timeout=8)
            
            if "安全验证" in resp.text or "wappass" in resp.url:
                print("[Search] 百度触发验证码")
                return []
                
            soup = BeautifulSoup(resp.content, 'html.parser')
            raw_results = []
            
            # [Owllook 选择器]
            # 兼容旧版 .result 和新版 .c-container
            items = soup.select('div.result') or soup.select('div.c-container')
            
            for item in items:
                try:
                    # 提取标题链接 (h3.t a 是百度经典结构)
                    title_tag = item.select_one('h3.t a') or item.select_one('h3 a') or item.select_one('a')
                    if not title_tag: continue
                    
                    title = title_tag.get_text(strip=True)
                    href = title_tag.get('href') # 这是一个加密链接
                    
                    if not href: continue

                    if self._is_valid_result(title, href):
                        raw_results.append({
                            'title': self._clean_title(title),
                            'url': href,
                            'suggested_key': self.get_pinyin_key(keyword),
                            'source': 'Baidu (Owllook)'
                        })
                except: continue
                if len(raw_results) >= 8: break
            
            # 百度链接全是加密的，必须并发解密
            return self._concurrent_resolve(raw_results)

        except Exception as e:
            print(f"[Search] Baidu Error: {e}")
            return []
    # ==========================================
    # 引擎 3: 必应搜索 (BingNovels)
    # ==========================================
    def _do_bing_search(self, keyword):
        print(f"[Search] 🔍 启动 Owllook-Bing 引擎: {keyword}")
        url = "https://www.bing.com/search"
        
        # [Owllook 参数]
        # ensearch=0: 强制中文搜索逻辑
        params = {'q': f"{keyword} 小说 目录", 'ensearch': 0}
        
        try:
            # Bing 需要 Referer
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
                'Referer': 'https://www.bing.com/'
            }
            resp = cffi_requests.get(url, params=params, headers=headers, impersonate=self.impersonate, timeout=10)
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []
            
            # [Owllook 选择器]
            # .b_algo 是 Bing 搜索结果的标准容器
            items = soup.select('li.b_algo')
            
            for item in items:
                try:
                    title_tag = item.select_one('h2 a')
                    if not title_tag: continue
                    
                    title = title_tag.get_text(strip=True)
                    href = title_tag.get('href')
                    
                    if not href: continue

                    # 过滤掉百度百科等在 Bing 中的结果
                    if "baike.baidu.com" in href: continue

                    if self._is_valid_result(title, href):
                        results.append({
                            'title': self._clean_title(title),
                            'url': href,
                            'suggested_key': self.get_pinyin_key(keyword),
                            'source': 'Bing (Owllook)'
                        })
                except: continue
                if len(results) >= 8: break
            
            return results

        except Exception as e:
            print(f"[Search] Bing Error: {e}")
            return []
    def _do_direct_source_search(self, keyword):
        if not self.plugins:
            return []
            
        print(f"[Search] 🧱 启动直连插件搜索 (共{len(self.plugins)}个): {keyword}")
        all_results = []
        
        # 使用线程池并发调用所有插件
        with ThreadPoolExecutor(max_workers=len(self.plugins)) as exe:
            future_to_plugin = {
                exe.submit(plugin.search, keyword): plugin 
                for plugin in self.plugins
            }
            
            for future in as_completed(future_to_plugin):
                plugin = future_to_plugin[future]
                try:
                    res = future.result()
                    if res:
                        # 给结果补上 pinyin_key (插件里可能没加)
                        for item in res:
                            if 'suggested_key' not in item:
                                item['suggested_key'] = self.get_pinyin_key(keyword)
                        all_results.extend(res)
                        print(f"  -> {plugin.source_name} 贡献了 {len(res)} 条结果")
                except Exception as e:
                    print(f"  -> {plugin.source_name} 运行时异常: {e}")

        return all_results
    # ==========================================
    # 辅助: 并发解析真实地址
    # ==========================================
    def _concurrent_resolve(self, raw_results):
        if not raw_results: return []
        print(f"[Search] 并发解析 {len(raw_results)} 个链接...")
        
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
                    # 确保解析出来的是 http 且不是加密链
                    if (real_url.startswith('http') and 
                        "baidu.com/link" not in real_url and 
                        "so.com/link" not in real_url and 
                        self._is_valid_result(item['title'], real_url)):
                        
                        item['url'] = real_url
                        final_results.append(item)
                except: pass
        
        return final_results
    # ==========================================
    # 统一入口
    # ==========================================
    # ... (前面的 _do_so_search, _do_baidu_search 等保持不变) ...

    # === [核心升级] 全网并发聚合搜索 (Aggregated Search) ===
    def search_bing(self, keyword):
        print(f"\n[Search] 🚀 启动全网并发聚合搜索: {keyword}")
        start_time = time.time()
        
        # 1. 定义参赛选手 (所有搜索引擎一起上)
        search_funcs = [
            self._do_direct_source_search,
            self._do_so_search,             # 360 (主力)
            # self._do_baidu_search,          # 百度 (互补)
            # self.search,
              # 直连 (兜底+高质量)
            # self._do_bing_search            # Bing (国际源)
        ]

        # 如果有代理，把 DDG 也加上
        # if self.proxies:
            # search_funcs.insert(0, self._do_ddg_search)

        all_results = []
        seen_urls = set()  # 用于 URL 去重
        
        # 2. 开启线程池，最大并发数 = 引擎数量
        # 注意：这里不仅搜索引擎并发，内部解析真实链接也是并发的(嵌套并发)，速度极快
        with ThreadPoolExecutor(max_workers=len(search_funcs)) as exe:
            # 提交所有搜索任务
            future_to_name = {
                exe.submit(func, keyword): func.__name__ 
                for func in search_funcs
            }
            
            # 3. 收集结果 (谁先回来谁先上榜，或者等全部回来)
            for future in as_completed(future_to_name):
                engine_name = future_to_name[future]
                try:
                    results = future.result()
                    if results:
                        print(f"  [Aggregator] {engine_name} 贡献了 {len(results)} 条结果")
                        
                        for item in results:
                            url = item['url']
                            # 简单去重逻辑 (去掉协议头和尾部斜杠进行比对)
                            clean_url = url.replace('https://', '').replace('http://', '').rstrip('/')
                            
                            if clean_url not in seen_urls:
                                seen_urls.add(clean_url)
                                all_results.append(item)
                                
                except Exception as e:
                    print(f"  [Aggregator] {engine_name} 异常: {e}")

        # 4. 结果排序优化 (可选)
        # 目前是按“谁快谁排前面”的自然顺序。
        # 如果你想让直连源 (XBiquge) 始终排在前面，可以在这里对 all_results sort 一下
        # 例如: all_results.sort(key=lambda x: 0 if 'XBiquge' in x['source'] else 1)

        print(f"[Search] 聚合完成，耗时 {time.time() - start_time:.2f}s，共获取 {len(all_results)} 个有效源\n")
        return all_results


class SearchHelperOld:
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
    # === [核心新增] Owllook 聚合搜索 (基于 HTML 解析) ===
    # === Owllook 聚合搜索 (标准 Requests 版) ===
    def _do_owllook_search(self, keyword):
        print(f"[Search] 🦉 尝试 Owllook 聚合搜索: {keyword}")
        url = "https://www1.owlook.com.cn/search"
        params = {'wd': keyword}
        
        try:
            # 使用标准 requests，模拟普通浏览器头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Connection': 'keep-alive'
            }
            
            # verify=False 可以防止因为证书问题导致的连接中断
            resp = requests.get(
                url, 
                params=params, 
                headers=headers,
                timeout=15,
                verify=False 
            )
            
            # 编码处理
            resp.encoding = 'utf-8'
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            
            # ... (下面的解析逻辑完全保持不变) ...
            items = soup.select('.result_item')
            
            for item in items:
                try:
                    # 1. 提取真实源链接
                    source_link_tag = item.select_one('.netloc a[href^="http"]')
                    if not source_link_tag: continue
                    
                    href = source_link_tag.get('href')
                    
                    # 2. 提取标题
                    main_link = item.select_one('li a')
                    if not main_link: continue
                    
                    full_text = main_link.get_text(strip=True)
                    parts = full_text.split('--')
                    title = parts[1] if len(parts) >= 2 else full_text
                    
                    clean_title = self._clean_title(title)
                    
                    if not href or self._is_junk(clean_title, href): continue
                    if not self._is_valid_novel_site(href): continue

                    results.append({
                        'title': clean_title,
                        'url': href,
                        'suggested_key': self.get_pinyin_key(keyword),
                        'source': 'Owllook 🦉'
                    })
                    
                except Exception: continue
                if len(results) >= 10: break
            
            return results

        except Exception as e:
            print(f"[Search] Owllook Error: {e}")
            return []
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
            
            # ... 前面的代码保持不变 ...
            
            if not raw_results:
                print("[Search] 360 未找到初步结果")
                return []

            # [修改] 改为单线程串行解析
            print(f"[Search] 正在顺序解析 {len(raw_results)} 个 360 链接...")
            final_results = []
            
            for item in raw_results:
                try:
                    # 直接调用函数，而不是提交给线程池
                    real_url = self._resolve_real_url(item['url'])
                    # print(111)
                    # [重要] 只有当 URL 不再包含 "so.com/link" 时，才算解析成功
                    # 并且要符合小说站白名单
                    if "so.com/link" not in real_url and self._is_valid_novel_site(real_url):
                        item['url'] = real_url
                        final_results.append(item)
                        # print(f"[Search] 解析成功: {real_url}") # 调试用
                    else:
                        # print(f"[Search] 丢弃无效链接: {real_url}") # 调试用
                        pass
                except Exception as e:
                    print(f"[Search] 单项解析出错: {e}")
                    pass
            
            return final_results

        except Exception as e:
            print(f"[Search] 360 Error: {e}")
            return []
    # def _resolve_real_url(url) :
        # print("[fff]")
        # return url
    def _resolve_real_url(self, url):
        # print("1111111")
        """
        [增强版] 解析 360/百度的加密跳转链接
        支持：302 Header 跳转、Meta Refresh 跳转、JS Window.location 跳转
        """
        # 如果本身就是直链，直接返回
        if "so.com" not in url:
            return url
            
        try:
            print("111")
            # 1. 第一次尝试：禁止重定向，看 Header
            # 这里的 timeout 设置稍长一点，防止网络波动
            resp = cffi_requests.get(
                url, 
                impersonate=self.impersonate, 
                timeout=8, 
                allow_redirects=False 
            )
            
            # 情况 A: 标准 301/302 跳转
            if resp.status_code in [301, 302]:
                real_url = resp.headers.get('Location') or resp.headers.get('location')
                print(real_url)
                if real_url:
                    print(f"[Resolve] 302跳转成功: {real_url[:40]}...")
                    return real_url
            
            # 情况 B: 200 OK，但是是一个中间跳转页 (360 经常干这个)
            if resp.status_code == 200:
                html = resp.text
                # B1. 尝试提取 JS 跳转: window.location.replace("...")
                # 360 的特征通常是 window.location.replace
                import re
                js_match = re.search(r"window\.location\.replace\(['\"](.+?)['\"]", html)
                if js_match:
                    real_url = js_match.group(1)
                    print(f"[Resolve] JS提取成功: {real_url[:40]}...")
                    return real_url
                
                # B2. 尝试提取 Meta Refresh: <meta http-equiv="refresh" content="0;url=...">
                meta_match = re.search(r'url=([^"]+)"', html, re.IGNORECASE)
                if meta_match:
                    real_url = meta_match.group(1)
                    print(f"[Resolve] Meta提取成功: {real_url[:40]}...")
                    return real_url

        except Exception as e:
            print(f"[Resolve] 解析出错: {e}")
            pass
            
        # 如果所有手段都失效，为了防止前端报错，还是返回原链接
        # 但大概率这个链接前端也打不开，所以最好是在 _do_360_search 里过滤掉
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
    @lru_cache(maxsize=100) 
    def search_bing_cached(self, keyword):
        """带缓存的搜索入口，避免重复联网"""
        print(f"[Search Cache] Miss, fetching: {keyword}")
        return self.search_bing(keyword)
    def search_bing(self, keyword):
        # 1. 优先尝试 Owllook (聚合源，质量最高，且提供直链)
        res = self._do_owllook_search(keyword)
        if res: return res
        # 2. 360
        res = self._do_360_search(keyword)
        if res: return res
        # 3. 百度/Bing...
        return self._do_bing_cn_search(keyword)
    # def _resolve_real_url(self, url):
    #     """
    #     [新增] 解析 360/百度的加密跳转链接
    #     原理：发送请求但不跟随跳转 (allow_redirects=False)，直接读取 Location 头
    #     """
    #     # 如果不是加密链接，直接返回
    #     if "so.com/link" not in url and "baidu.com/link" not in url:
    #         return url
            
    #     try:
    #         # 必须禁止自动跳转，否则会下载整个目标网页，浪费流量和时间
    #         resp = cffi_requests.get(
    #             url, 
    #             impersonate=self.impersonate, 
    #             timeout=5, 
    #             allow_redirects=False 
    #         )
            
    #         # 检查状态码是否为 301/302 重定向
    #         if resp.status_code in [301, 302]:
    #             # 获取真实地址 (Location 头)
    #             real_url = resp.headers.get('Location') or resp.headers.get('location')
    #             if real_url:
    #                 return real_url
    #     except Exception as e: # <--- 这里加了空格，修复了语法错误
    #         print(f"[Search] 解析跳转失败: {e}")
    #         pass
            
    #     # 如果解析失败，为了不让程序崩溃，原样返回加密链接
    #     # 虽然这会导致前端可能打不开，但总比没有好
    #     return url

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
        print(f"\n[Switch] 🚀 极速换源: 《{book_name}》 (ID: {target_chapter_id})")
        
        # 1. 搜索 (带缓存)
        from spider_core import searcher 
        # 使用 search_bing_cached 而不是 search_bing
        search_results = searcher.search_bing_cached(book_name)
        
        if not search_results:
            return []

        print(f"[Switch] 🔍 缓存/搜索返回 {len(search_results)} 个源，开始极速验证...")
        valid_sources = []
        
        # 2. 验证任务 (极速版)
        def check_source(result):
            toc_url = result['url']
            domain = urlparse(toc_url).netloc
            
            try:
                # [关键] 开启 fast_mode=True
                # 超时 5秒，不重试。如果 5秒没拉下来目录，说明这个源太慢，直接丢弃！
                toc = self.get_toc(toc_url, fast_mode=True)
                
                if not toc or not toc.get('chapters'):
                    return None
                
                # 倒序查找，效率更高
                for chap in reversed(toc['chapters']):
                    if chap.get('id') == target_chapter_id:
                        # print(f"[Switch] ✅ 命中: {domain}")
                        return {
                            "source": domain,
                            "url": chap['url'],
                            "title": chap['name'],
                            "toc_url": toc_url
                        }
            except: pass
            return None

        # 3. 并发验证 (最大 8 线程)
        # 只取前 5 个结果验证，因为后面的通常质量低且浪费时间
        candidates = search_results[:5] 
        
        with ThreadPoolExecutor(max_workers=8) as exe:
            futures = [exe.submit(check_source, res) for res in candidates]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    valid_sources.append(res)
        
        print(f"[Switch] 🏁 耗时操作结束，找到 {len(valid_sources)} 个有效源")
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
        if url.startswith('epub:'):
            return url
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
    def _fetch_page_smart(self, url, retry=None, timeout=None):
        """
        基础请求：支持自定义重试次数和超时时间
        配合 get_toc 的 fast_mode 使用
        """
        # 1. 参数决断：如果未传入，则使用实例变量或默认值
        # 这样设计是为了让 get_toc 中临时修改 self.timeout 能生效
        current_retry = retry if retry is not None else 3
        current_timeout = timeout if timeout is not None else self.timeout

        for i in range(current_retry):
            try:
                headers = {
                    "Referer": url, 
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
                }
                
                # 发起请求
                resp = cffi_requests.get(
                    url, 
                    impersonate=self.impersonate, 
                    timeout=current_timeout,  # <--- 关键：使用动态超时
                    headers=headers, 
                    allow_redirects=True, 
                    proxies=self.proxies
                )
                
                # === 编码智能识别逻辑 ===
                
                # A. 尝试 lxml 解析 meta 标签 (最准)
                try:
                    tree = lxml_html.fromstring(resp.content, parser=lxml_html.HTMLParser(encoding='utf-8'))
                    charset = tree.xpath('//meta[contains(@content, "charset")]/@content') or tree.xpath('//meta/@charset')
                    enc = 'utf-8'
                    if charset:
                        match = re.search(r'charset=([\w-]+)', str(charset[0]), re.I)
                        enc = match.group(1) if match else charset[0]
                    return resp.content.decode(enc)
                except Exception:
                    pass
                
                # B. 暴力尝试常见中文编码
                for e in ['utf-8', 'gb18030', 'gbk', 'big5']:
                    try: return resp.content.decode(e)
                    except: continue
                
                # C. 最后兜底
                return resp.content.decode('utf-8', errors='replace')

            except Exception as e: 
                # 只有不是最后一次重试时才 sleep
                if i == current_retry - 1: 
                    # print(f"[Fetch] 最终失败: {url} | Err: {e}")
                    return None 
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

    def get_toc(self, toc_url, fast_mode=False):
        """
        fast_mode=True: 不重试，超时短，专用于换源检测
        """
        # 参数设置
        if toc_url.startswith('epub:'):
            return None
        timeout = 5 if fast_mode else 15
        retry = 1 if fast_mode else 3

        adapter = plugin_mgr.find_match(toc_url)
        if adapter: 
            # 注意：如果适配器里的 get_toc 调用了 _fetch_page_smart，
            # 我们需要修改适配器才能生效，或者我们在这里 monkey patch 一下？
            # 简单起见，我们假设适配器调用的是 self._fetch_page_smart
            # 我们可以临时把 self.timeout 改了，虽然不优雅但有效
            
            old_timeout = self.timeout
            self.timeout = timeout # 临时修改全局超时
            try:
                data = adapter.get_toc(self, toc_url)
            finally:
                self.timeout = old_timeout # 恢复
        else: 
            # 通用逻辑，直接传参
            # 我们需要修改 _general_toc_logic 接受参数，或者像上面一样改 self.timeout
             # 这里复用上面的逻辑修改 timeout 属性最稳妥
             pass
        
        # 为了不修改所有适配器代码，我们采用修改实例属性的方式来实现 Fast Mode
        # 上面的逻辑其实只对 adapter 有效，对通用逻辑需要下面这段：
        
        # 重新写一段通用的 get_toc 调用逻辑：
        old_timeout = self.timeout
        self.timeout = timeout
        
        try:
             # 这里调用原来的逻辑
             if adapter: 
                 data = adapter.get_toc(self, toc_url)
             else:
                 # 修改 _general_toc_logic 内部调用的 _fetch_page_smart
                 # 由于 _fetch_page_smart 现在用的是参数默认值，我们需要它读取 self.timeout
                 # 请确保你的 _fetch_page_smart 默认 timeout=self.timeout
                 
                 # 或者我们简单粗暴重写 _fetch_page_smart 让他优先用参数，没有参数用 self.timeout
                 data = self._general_toc_logic(toc_url)
        except Exception:
            return None
        finally:
            self.timeout = old_timeout # 恢复默认 15s

        if not data or not data.get('chapters'): return None
        if data.get('manual_sort') is True: return data
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
# spider_core.py

class EpubHandler:
    def __init__(self):
        self.lib_dir = LIB_DIR
        if not os.path.exists(self.lib_dir): os.makedirs(self.lib_dir)
        # 分页阈值：每页大约 3000 字
        self.CHUNK_SIZE = 3000 

    def save_file(self, file_obj):
        filename = secure_filename(file_obj.filename)
        if not filename: filename = f"book_{int(time.time())}.epub"
        filepath = os.path.join(self.lib_dir, filename)
        file_obj.save(filepath)
        return filename

    def _flatten_toc(self, toc, flat_list=None):
        """递归展平 TOC 结构"""
        if flat_list is None: flat_list = []
        for item in toc:
            if isinstance(item, (list, tuple)):
                # 这是一个章节节点
                section = item[0]
                children = item[1] if len(item) > 1 else []
                
                # 获取 href (ebooklib 的对象比较复杂，需要提取 href)
                href = section.href if hasattr(section, 'href') else ''
                title = section.title if hasattr(section, 'title') else '无标题'
                
                if href:
                    flat_list.append({'title': title, 'href': href})
                
                # 递归处理子章节
                if children:
                    self._flatten_toc(children, flat_list)
            elif hasattr(item, 'href'):
                # 简单节点
                flat_list.append({'title': item.title, 'href': item.href})
        return flat_list

    def get_toc(self, filename):
        filepath = os.path.join(self.lib_dir, filename)
        if not os.path.exists(filepath): return None
        try:
            book = epub.read_epub(filepath)
            title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else filename
            
            # 1. 尝试解析逻辑目录 (NCX/TOC)
            raw_toc = book.toc
            chapters = []
            
            if raw_toc:
                # 使用递归展平的目录
                flat_toc = self._flatten_toc(raw_toc)
                # 映射到我们的 URL 格式: epub:filename:href:page_index
                # 注意：这里我们用 href 作为标识符，而不是 spine index，因为 spine index 不直观
                for i, item in enumerate(flat_toc):
                    chapters.append({
                        'title': item['title'], 
                        # 使用 href 作为定位符
                        'url': f"epub:{filename}:{item['href']}:0" 
                    })
            else:
                # 兜底：如果没有 TOC，还是用 Spine
                for i, item in enumerate(book.spine):
                    chapters.append({
                        'title': f"第 {i+1} 节", 
                        'url': f"epub:{filename}:{item[0]}:0" # item[0] 是 item_id
                    })

            return {'title': title, 'chapters': chapters}
        except Exception as e: 
            print(f"EPUB TOC Error: {e}")
            return None

    def get_chapter_content(self, filename, item_identifier, page_index=0):
        """
        :param item_identifier: 可以是 href (如 chapter1.html) 或 item_id
        :param page_index: 分页索引，0 开始
        """
        filepath = os.path.join(self.lib_dir, filename)
        try:
            book = epub.read_epub(filepath)
            
            # 1. 寻找对应的 Item
            target_item = None
            # 先尝试通过 href 找
            for item in book.get_items():
                if item.get_name() == item_identifier:
                    target_item = item
                    break
            # 如果没找到，尝试通过 ID 找
            if not target_item:
                target_item = book.get_item_with_id(item_identifier)
            
            if not target_item:
                return {'title': '错误', 'content': ['未找到该章节内容']}

            # 2. 解析内容
            soup = BeautifulSoup(target_item.get_content(), 'html.parser')
            
            # 尝试获取章节标题
            title_tag = soup.find(['h1', 'h2'])
            current_title = title_tag.get_text(strip=True) if title_tag else "未知章节"
            
            # 提取正文并清洗
            raw_lines = [p.get_text(strip=True) for p in soup.find_all(['p', 'div']) if p.get_text(strip=True)]
            
            # 3. [核心] 执行长章节分页逻辑
            # 将所有行合并成大文本，再重新切分，或者直接按行数切分
            # 这里采用“按字符数聚合后切分”的策略，体验更好
            full_text = "\n".join(raw_lines)
            total_len = len(full_text)
            
            # 如果内容非常短，不分页
            if total_len <= self.CHUNK_SIZE:
                chunks = [raw_lines]
            else:
                # 简单粗暴分页：按行累加，超过阈值就切
                chunks = []
                current_chunk = []
                current_count = 0
                for line in raw_lines:
                    current_chunk.append(line)
                    current_count += len(line)
                    if current_count >= self.CHUNK_SIZE:
                        chunks.append(current_chunk)
                        current_chunk = []
                        current_count = 0
                if current_chunk: chunks.append(current_chunk)

            # 4. 校验页码
            if page_index >= len(chunks): page_index = len(chunks) - 1
            if page_index < 0: page_index = 0
            
            final_content = chunks[page_index]
            
            # 5. 构建上一页/下一页链接
            # 逻辑：
            # - 如果还有下一页 (sub-page)，Next 指向 page_index + 1
            # - 如果没有下一页，Next 指向 下一个文件的第 0 页 (需要计算 TOC 顺序)
            
            # 这里简化处理：我们只处理内部翻页。跨章翻页需要知道 TOC 的顺序。
            # 为了实现跨章，我们需要重新获取一次 TOC 列表来定位
            
            prev_url = None
            next_url = None
            
            # 内部翻页
            if page_index > 0:
                prev_url = f"epub:{filename}:{item_identifier}:{page_index-1}"
            if page_index < len(chunks) - 1:
                next_url = f"epub:{filename}:{item_identifier}:{page_index+1}"
            
            # 跨文件翻页 (如果内部没翻页了)
            if not prev_url or not next_url:
                toc_data = self.get_toc(filename) # 这步可能略耗时，但为了准确性必须做
                if toc_data:
                    chapters = toc_data['chapters']
                    # 找到当前章节在列表中的索引
                    # 构造当前的 URL 前缀进行匹配
                    current_base = f"epub:{filename}:{item_identifier}"
                    
                    curr_idx = -1
                    for i, chap in enumerate(chapters):
                        if chap['url'].startswith(current_base):
                            curr_idx = i
                            break
                    
                    if curr_idx != -1:
                        # 跨章上一页
                        if not prev_url and curr_idx > 0:
                            # 上一章的链接 (默认跳到第0页，如果想跳到最后一页比较麻烦，暂定第0页)
                            prev_url = chapters[curr_idx - 1]['url']
                        
                        # 跨章下一页
                        if not next_url and curr_idx < len(chapters) - 1:
                            next_url = chapters[curr_idx + 1]['url']

            return {
                'title': f"{current_title} ({page_index+1}/{len(chunks)})" if len(chunks)>1 else current_title,
                'content': final_content,
                'prev': prev_url,
                'next': next_url,
                'toc_url': f"epub:{filename}:toc"
            }

        except Exception as e: 
            return {'title': 'Error', 'content': [f"EPUB Error: {str(e)}"]}
# 实例化对象
crawler_instance = NovelCrawler()
searcher = SearchHelper()
epub_handler = EpubHandler()