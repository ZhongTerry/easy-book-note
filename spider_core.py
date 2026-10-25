import time
import random
import re
import os
import importlib.util
import hashlib
import uuid
import zipfile
from contextvars import ContextVar
from urllib.parse import urljoin, urlparse, quote
from difflib import SequenceMatcher
from urllib.request import getproxies
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from lxml import html as lxml_html
from pypinyin import lazy_pinyin, Style
from concurrent.futures import ThreadPoolExecutor, as_completed
from ebooklib import epub
from werkzeug.utils import secure_filename
from recognition import PageType, RecognitionEngine, SourceHealthTracker, find_chapter_match, get_payload_issue, is_cacheable_payload
from recognition.chapter_numbers import parse_chapter_number
from services.searxng_search import SearxngNovelSearch
# [确保这里有 CACHE_DIR]
from shared import BASE_DIR, LIB_DIR, CACHE_DIR, debug, info, warn, error, is_safe_url

# Bump this whenever a worker result depends on a changed recognition contract.
# Older workers are then ignored by the master, which falls back to local crawl
# instead of silently returning an incomplete chapter. v3: site-rule layer.
CRAWLER_PROTOCOL_VERSION = 3
_toc_request_options = ContextVar('toc_request_options', default=None)

# ==========================================
# 0. 辅助工具 (中文数字转阿拉伯数字 - 增强版)
# ==========================================
# spider_core.py

def _remote_request(endpoint, payload):
    """
    远程爬取请求（带延迟自动记录）
    返回: (data, worker_uuid, latency_ms) 或 None
    """
    # [关键修复] Worker节点执行时跳过远程请求，直接返回None降级到本地爬取
    import os
    if os.environ.get('FORCE_LOCAL_CRAWL') == '1':
        return None  # Worker节点强制本地爬取
    
    # 1. 检查 Redis 是否可用
    try:
        from managers import cluster_manager
        if not cluster_manager.use_redis:
            return None # 没 Redis 只能跑本地
    except: 
        return None

    # 2. 构造任务
    import uuid
    import json
    import time
    
    task_id = str(uuid.uuid4())
    task_package = {
        "id": task_id,
        "endpoint": endpoint, # 'run' 或 'toc'
        "payload": payload,
        "timestamp": time.time()
    }

    # 3. 写入队列 (LPUSH 左进)
    try:
        cluster_manager.r.lpush("crawler:queue:pending", json.dumps(task_package))
    except Exception as e:
        error("Cluster", f"Redis 写入失败: {e}")
        return None

    # 4. 阻塞等待结果 (轮询 Redis) - 记录延迟
    start_time = time.time()
    result_key = f"crawler:result:{task_id}"
    
    while time.time() - start_time < 25:
        res = cluster_manager.r.get(result_key)
        if res:
            # 计算延迟
            latency_ms = int((time.time() - start_time) * 1000)
            
            # 删除结果（读完即焚）
            cluster_manager.r.delete(result_key)
            json_res = json.loads(res)
            
            if json_res.get('status') == 'success':
                if json_res.get('crawler_protocol_version') != CRAWLER_PROTOCOL_VERSION:
                    warn(
                        "Cluster",
                        "Worker recognition protocol is outdated; falling back to local crawl",
                    )
                    return None
                data = json_res.get('data')
                worker_uuid = json_res.get('worker_uuid', 'unknown')
                
                # [核心] 自动记录延迟到权重系统
                url = payload.get('url', '')
                if url and worker_uuid != 'unknown':
                    cluster_manager.record_latency(url, worker_uuid, latency_ms)
                    info("Cluster", f"✅ 任务完成 {task_id[:8]} 耗时{latency_ms}ms by {worker_uuid[:8]}")
                
                return data
            else:
                # 远程报错也记录（给一个惩罚性延迟）
                worker_uuid = json_res.get('worker_uuid', 'unknown')
                url = payload.get('url', '')
                if url and worker_uuid != 'unknown':
                    cluster_manager.record_latency(url, worker_uuid, -1)  # -1表示错误
                    error("Cluster", f"❌ 任务失败 {task_id[:8]} by {worker_uuid[:8]}")
                return None
        
        time.sleep(0.2) # 每 0.2 秒看一眼

    warn("Spider", f"[Cluster] ⚠️ 任务 {task_id[:8]} 等待超时 (无 Worker 接单)")
    return None
def parse_chapter_id(text):
    return parse_chapter_number(text) or -1


# ==========================================
# 1. 插件管理器
# ==========================================
# spider_core.py

class AdapterManager:
    def __init__(self, folder="adapters"):
        self.folder = os.path.join(BASE_DIR, folder)
        self.adapters = []
        if not os.path.exists(self.folder): os.makedirs(self.folder)
        self.load_plugins()

    def load_plugins(self):
        self.adapters = []
        info("Spider", f"📂 [AdapterManager] 扫描目录: {self.folder}")

        for f in os.listdir(self.folder):
            if f.endswith(".py") and f != "__init__.py":
                file_path = os.path.join(self.folder, f)
                module_name = f[:-3]
                
                try:
                    # 动态加载模块
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    
                    found_in_file = False
                    
                    # 遍历模块内所有属性
                    for n in dir(mod):
                        # 跳过内置属性
                        if n.startswith('__'): continue
                        
                        # 1. 名字匹配 (必须包含 Adapter)
                        if "Adapter" in n:
                            obj = getattr(mod, n)
                            
                            # 调试日志：看看这个到底是啥
                            # print(f"    🔎 [Debug] 检查 {n}: 类型={type(obj)}")

                            # 2. 宽松检查：只要是可调用的 (类或函数)，且不是基础类型
                            if callable(obj) and not isinstance(obj, (str, int, bool)):
                                try:
                                    # 尝试实例化
                                    instance = obj()
                                    
                                    # 3. 鸭子类型检查：必须有 can_handle 方法
                                    if hasattr(instance, 'can_handle'):
                                        self.adapters.append(instance)
                                        info("Spider", f"✅ [Adapter] 成功挂载: {n} (来自 {f})")
                                        found_in_file = True
                                    else:
                                        # print(f"    ⚠️ {n} 缺少 can_handle 方法，跳过")
                                        pass
                                        
                                except Exception as e:
                                    pass
                            else:
                                pass
                    
                    if not found_in_file:
                        # 只有当文件里一个都没找到时才警告
                        # 很多时候文件里可能只有辅助类，所以这里可以忽略
                        pass
                        
                except Exception as e:
                    error("Spider", f"❌ [Adapter] 加载文件 {f} 崩溃: {e}")
        
        info("Spider", f"[AdapterManager] 插件扫描完成，共生效 {len(self.adapters)} 个适配器")

    def find_match(self, url):
        info("Spider", f"url: {url}")
        if not url: return None
        for a in self.adapters:
            try:
                info("Spider", url)
                if a.can_handle(url): 
                    info("Spider", f"🎯 适配器命中: {a.__class__.__name__}")
                    return a
            except: 
                error("Spider", "Err")
                pass
        return None

plugin_mgr = AdapterManager()
from functools import lru_cache
import requests

class SearchHelper:
    def __init__(self):
        # Search traffic is intentionally centralized in the configured SearXNG
        # instance. Legacy direct-source/search-engine code remains below only
        # as dormant migration history and is no longer initialized or called.
        self.searxng = SearxngNovelSearch()

    def search_bing_cached(self, keyword):
        """Compatibility entry point backed only by the SearXNG TTL cache."""
        return self.searxng.search(keyword, verifier=self._verify_candidate)

    def search_concurrent(self, keyword, callback=None):
        """Search exclusively through SearXNG while preserving task callbacks."""
        return self.searxng.search(keyword, callback, verifier=self._verify_candidate)

    def _verify_candidate(self, candidate):
        """Classify a search result without persisting it into the user's shelf."""
        url = candidate.get('url', '')
        requested_title = candidate.get('_search_keyword', '')
        if not is_safe_url(url):
            return {'status': 'unsafe', 'status_detail': '链接未通过安全校验'}

        # Most SearXNG novel queries yield catalog pages. Keep this fast path
        # bounded before using the more expensive full-page reader pipeline.
        toc = crawler_instance.get_toc(url, fast_mode=True)
        if toc and toc.get('chapters'):
            if not self._catalog_is_reliable(toc, requested_title):
                return {
                    'status': 'unavailable',
                    'status_detail': '目录章节不足或无法确认属于目标书籍',
                }
            return {
                'status': 'ready_catalog',
                'status_detail': f"已识别目录，{len(toc['chapters'])} 章",
                'content_type': 'catalog',
            }

        payload = crawler_instance.run(url)
        issue = get_payload_issue(payload)
        if issue:
            status = 'blocked' if issue['code'] == 'SOURCE_CHALLENGE' else 'unavailable'
            return {'status': status, 'status_detail': issue['message']}
        page_type = (payload or {}).get('page_type', '')
        if page_type == PageType.TOC.value:
            chapters = (payload or {}).get('chapters') or []
            if not self._catalog_is_reliable(payload, requested_title):
                return {
                    'status': 'unavailable',
                    'status_detail': '目录章节不足或无法确认属于目标书籍',
                }
            return {
                'status': 'ready_catalog',
                'status_detail': f'已识别目录，{len(chapters)} 章',
                'content_type': 'catalog',
            }
        if page_type == PageType.CHAPTER.value:
            return {
                'status': 'ready_chapter',
                'status_detail': '已识别可阅读正文',
                'content_type': 'chapter',
            }
        return {'status': 'unavailable', 'status_detail': '未识别到可靠目录或正文'}

    @staticmethod
    def _catalog_is_reliable(toc, requested_title):
        """Require enough chapters and matching page metadata, not merely links."""
        if len(toc.get('chapters') or []) < 3:
            return False

        def compact(value):
            return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', str(value or '')).lower()

        requested = compact(requested_title)
        # A two-character search is too ambiguous to validate a catalog safely.
        if len(requested) < 3:
            return False
        metadata = ' '.join(str(toc.get(key) or '') for key in (
            'title', 'book_name', 'name', 'desc', 'description',
        ))
        observed = compact(metadata)
        return requested in observed or (len(observed) >= 4 and observed in requested)

    
    def get_pinyin_key(self, text):
        clean = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        clean = re.sub(r'(小说|笔趣阁|最新章节|全文阅读)', '', clean)
        try:
            s = lazy_pinyin(clean, style=Style.FIRST_LETTER)
            k = ''.join(s).lower()
            return k[:15] if k else "temp"
        except: return "temp"

    # === [核心升级] 全网并发聚合搜索 (Aggregated Search) ===
    def search_bing(self, keyword):
        """Forced source-switch entry point; bypasses the SearXNG TTL cache."""
        return self.searxng.search(
            keyword,
            verifier=self._verify_candidate,
            bypass_cache=True,
        )


# ==========================================
# 3. 小说爬虫 (NovelCrawler - 修复KeyError版)
# ==========================================
class NovelCrawler:
    def __init__(self):
        import threading
        self.impersonate = "chrome110"
        self.timeout = 15
        self.proxies = getproxies()
        self.recognition_engine = RecognitionEngine()
        self.source_health = SourceHealthTracker()
        # [新增] 任务去重机制：防止同一 URL 被重复爬取
        self._active_tasks = {}  # {url: {'event': threading.Event(), 'result': None, 'error': None}}
        self._task_lock = threading.Lock()

    def _recognize_payload(self, payload, url, declared_type=None):
        """Attach the canonical recognition contract without breaking legacy fields."""
        return self.recognition_engine.normalize_payload(payload, url, declared_type)

    def _recognize_toc_payload(self, payload, url):
        return self._recognize_payload(payload, url, PageType.TOC)

    def _source_cooldown_payload(self, url):
        cooldown_seconds = self.source_health.cooldown_remaining(url)
        return {
            'title': '',
            'page_type': PageType.UNKNOWN.value,
            'recognition_confidence': 0.0,
            'recognition': {
                'page_type': PageType.UNKNOWN.value,
                'confidence': 0.0,
                'warnings': ['source_cooldown'],
                'evidence': [],
                'cooldown_seconds': cooldown_seconds,
            },
        }

    def _record_source_result(self, url, result):
        issue = get_payload_issue(result)
        if not issue:
            self.source_health.record_success(url)
        elif issue['code'] == 'SOURCE_CHALLENGE':
            self.source_health.record_failure(url, 'challenge')
        else:
            self.source_health.record_failure(url, 'fetch_or_recognition_failure')

    def _normalize_title(self, text):
        if not text:
            return ""
        text = re.sub(r'[\s\u3000]+', '', text)
        text = re.sub(r'[\-—_·•:：,，。．!！?？~～\[\]【】\(\)（）<>《》"\']', '', text)
        return text.strip().lower()

    def _pick_best_match(self, candidates, target_title):
        if not candidates:
            return None
        target_norm = self._normalize_title(target_title)
        best = None
        best_score = 0.0
        for item in candidates:
            title = item.get('title') or item.get('book_name')
            if not title:
                continue
            score = SequenceMatcher(None, target_norm, self._normalize_title(title)).ratio()
            if score > best_score:
                best_score = score
                best = item.copy()
                best['match_score'] = score
        return best

    def _fetch_qidian_meta(self, book_name):
        try:
            url = f"https://www.qidian.com/so/{quote(book_name)}.html"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.qidian.com/"
            }
            resp = cffi_requests.get(url, headers=headers, impersonate=self.impersonate, timeout=8, allow_redirects=True, proxies=self.proxies)
            html = resp.text if hasattr(resp, 'text') else resp.content.decode('utf-8', errors='replace')
            soup = BeautifulSoup(html, 'html.parser')

            items = []
            for li in soup.select('#result-list li.res-book-item'):
                title_tag = li.select_one('h3.book-info-title a')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                author_tag = li.select_one('p.author a.name')
                author = author_tag.get_text(strip=True) if author_tag else ''
                intro_tag = li.select_one('p.intro')
                desc = intro_tag.get_text(strip=True) if intro_tag else ''
                img_tag = li.select_one('div.book-img-box img')
                cover = img_tag.get('src', '') if img_tag else ''
                if cover.startswith('//'):
                    cover = 'https:' + cover
                href = title_tag.get('href') or ''
                if href.startswith('//'):
                    href = 'https:' + href
                elif href.startswith('/'):
                    href = 'https://www.qidian.com' + href

                items.append({
                    'title': title,
                    'author': author,
                    'desc': desc,
                    'cover': cover,
                    'url': href,
                    'source': 'qidian'
                })

            best = self._pick_best_match(items, book_name)
            if best:
                return {
                    'cover': best.get('cover', ''),
                    'author': best.get('author', ''),
                    'desc': best.get('desc', ''),
                    'book_name': best.get('title', ''),
                    'source': 'qidian',
                    'match_score': best.get('match_score', 0)
                }
        except Exception as e:
            error("Meta", f"Qidian search failed: {e}")
        return None

    def _fetch_fanqie_meta(self, book_name):
        try:
            from adapters.fanqie_adapter import FanqieLocalAdapter
            from spider_core import searcher

            results = searcher.search_bing_cached(book_name) or []
            fanqie_candidates = [
                result for result in results
                if 'fanqienovel.com' in (result.get('url') or '').lower()
            ]
            best = self._pick_best_match(fanqie_candidates, book_name)
            if not best:
                return None

            adapter = FanqieLocalAdapter()
            meta = adapter.get_meta(self, best.get('url'))
            if meta:
                meta['source'] = 'fanqie'
                meta['match_score'] = best.get('match_score', 0)
                return meta
        except Exception as e:
            error("Meta", f"Fanqie search failed: {e}")
        return None

    def get_meta_from_qidian_fanqie(self, book_name):
        qidian_meta = self._fetch_qidian_meta(book_name)
        fanqie_meta = self._fetch_fanqie_meta(book_name)

        candidates = [m for m in [qidian_meta, fanqie_meta] if m]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x.get('match_score', 0), reverse=True)
        return candidates[0]
    # spider_core.py -> NovelCrawler 类内部
    # ==========================================
    # [新增] 智能换源核心逻辑
    # ==========================================
    # === [调试增强版] 搜索并返回可用源列表 ===
    def search_alternative_sources(self, book_name, target_chapter_id):
        info("Spider", f"\n[Switch] 🚀 极速换源: 《{book_name}》 (ID: {target_chapter_id})")
        
        # 1. 搜索 (带缓存)
        from spider_core import searcher 
        # 使用 search_bing_cached 而不是 search_bing
        search_results = searcher.search_bing_cached(book_name)
        
        if not search_results:
            return []

        info("Spider", f"[Switch] 🔍 缓存/搜索返回 {len(search_results)} 个源，开始极速验证...")
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
        
        info("Spider", f"[Switch] 🏁 耗时操作结束，找到 {len(valid_sources)} 个有效源")
        return valid_sources

    def find_best_match(self, toc_url, target_id, target_title):
        """
        在指定源(toc_url)中查找最佳匹配章节
        仅在章节号和标题能形成可靠证据时返回候选章节。
        """
        info("Switch", f"🔎 正在新源 {toc_url} 查找章节: ID={target_id}, Title={target_title}")
        
        try:
            # 快速获取目录 (fast_mode, 超时较短)
            toc = self.get_toc(toc_url, fast_mode=True)
            if not toc or not toc.get('chapters'):
                error("Switch", "❌ 目录获取失败或为空")
                return None 

            match = find_chapter_match(toc['chapters'], target_id, target_title)
            if not match:
                warn("Switch", "未找到足够可靠的章节匹配，拒绝自动跳转")
                return None
            info("Switch", f"✅ 可靠匹配 ({match['match_strategy']}, {match['match_confidence']:.2f}): {match.get('title') or match.get('name')}")
            return match['url']
            
        except Exception as e:
            info("Switch", f"匹配过程出错: {e}")
            return None

# ...existing code...
    def _get_book_name(self, soup):
        """
        通用的小说名识别逻辑 (增强版)
        """
        def _clean_candidate(name):
            if not name:
                return None
            name = re.sub(r'[\s\u3000]+', ' ', name).strip()
            # 去作者后缀或装饰符 (书名(作者) / 【书名】等)
            name = re.sub(r'[\(（\[【<].*?[\)）\]】>]', '', name).strip()
            # 去常见噪声后缀
            name = re.sub(r'(最新章节|全文阅读|无错版|无弹窗|免费阅读|小说全集|小说下载|全文免费阅读|章节列表|最新)$', '', name).strip()
            return name or None

        def _is_chapter_like(text):
            return bool(re.search(r'第\s*[0-9零一二两三四五六七八九十百千万]+\s*[章节回幕节话]', text or ''))

        def _is_noise(text):
            if not text:
                return True
            if len(text) < 2 or len(text) > 40:
                return True
            if _is_chapter_like(text):
                return True
            bad_keywords = ['笔趣', '小说', '阅读', '章节', '目录', '无弹窗', '下载', '作者', '手机版', '站', '网']
            return any(k in text for k in bad_keywords)

        # 策略 A: OG/Twitter 元信息 (最稳)
        for prop in ['og:novel:book_name', 'og:title']:
            meta = soup.find('meta', property=prop)
            if meta:
                candidate = _clean_candidate(meta.get('content', ''))
                if candidate and not _is_noise(candidate):
                    return candidate
        meta_tw = soup.find('meta', attrs={'name': 'twitter:title'})
        if meta_tw:
            candidate = _clean_candidate(meta_tw.get('content', ''))
            if candidate and not _is_noise(candidate):
                return candidate

        # [新增] 策略 0: 从页面底部的脚本 lastread.set(...) 提取
        # 很多笔趣阁模版都有这个 script
        # 格式: lastread.set(id, zid, '书名', '章节名', '作者', ...)
        try:
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'lastread.set' in script.string:
                    # 匹配单引号或双引号包裹的第三个参数
                    match = re.search(r"lastread\.set\([^,]+,[^,]+,\s*['\"]([^'\"]+)['\"]", script.string)
                    if match:
                        info("Spider", f"[Smart Title] 🎯 从 JS lastread 提取成功: {match.group(1)}")
                        candidate = _clean_candidate(match.group(1))
                        if candidate and not _is_noise(candidate):
                            return candidate
        except: pass

        # 策略 B: 结构化书名区域 (h1 / book-name 等)
        for sel in ['h1', '.book-name', '#bookName', '.info h1', '.detail h1', '.book-info h1', '.detail-box h1']:
            for tag in soup.select(sel):
                candidate = _clean_candidate(tag.get_text(strip=True))
                if candidate and not _is_noise(candidate):
                    return candidate

        # [新增] 策略 1: 智能分析 <title> 标签
        # 常见的 title 格式: "章节名_书名_作者_网站名" 或 "书名_作者_网站名"
        if soup.title and soup.title.string:
            title_text = soup.title.string
            parts = re.split(r'[\_\-\|｜—]', title_text) # 用 _ 或 - 等切分
            parts = [p.strip() for p in parts if p.strip()]
            meta_kw = soup.find('meta', attrs={'name': 'keywords'})
            kw_content = meta_kw.get('content', '') if meta_kw else ''
            
            # 如果切分后有 3 部分以上 (如: 第477章..._学霸..._十月廿二_新笔趣阁)
            # 通常书名在倒数第三个 (如果是4段) 或 倒数第二个 (如果是3段)
            # 这里的逻辑比较这玄学，我们尝试提取最像书名的部分
            if len(parts) >= 3:
                # 倒数第三个通常是书名 (如果是 章节_书名_作者_网名)
                # 倒数第二个通常是书名 (如果是 章节_书名_网名)
                # 我们优先找倒数第三个，如果它是空的或者太短，再看别的
                
                # 排除列表: 常见的网站后缀
                exclude_keywords = ['笔趣', '小说', '最新', '章节', '无弹窗', '阅读', '下载', '作者']
                
                # 从后往前找，找到第一个不包含上述关键字且长度适中的部分
                candidates = []
                for part in reversed(parts):
                    p = _clean_candidate(part)
                    if not p: continue
                    if any(k in p for k in exclude_keywords): continue
                    if _is_noise(p): continue
                    candidates.append(p)
                    
                    # 它是书名的概率很大，但要排除作者名
                    # 我们可以配合 meta keywords 验证
                    if kw_content and p in kw_content:
                        info("Smart Title", f"🎯 从 Title+Keywords 锁定书名: {p}")
                        return p
                    
                    # 如果没有meta验证，简单的长度判断
                    if 1 < len(p) < 30: 
                        candidates.append(p)

                if candidates:
                    # 取最长且最像书名的
                    return max(candidates, key=len)

            # 标题不足分段时：尝试关键词提取
            meta_kw = soup.find('meta', attrs={'name': 'keywords'})
            if meta_kw:
                kw_content = meta_kw.get('content', '')
                for token in re.split(r'[,，;；\s]+', kw_content):
                    candidate = _clean_candidate(token)
                    if candidate and not _is_noise(candidate):
                        return candidate

        # 1. 尝试从常见面包屑导航中提取
        # 匹配包含 'path', 'breadcrumb', 'crumb' 的 class 或 id
# ...existing code...
        # [增强] 策略 2: 搜寻 con_top 或类似的非标准面包屑
        # 很多站用 .con_top > a 
        con_top = soup.find(class_='con_top')
        if con_top:
            text = con_top.get_text()
            if '>' in text:
                links = con_top.find_all('a')
                if len(links) >= 2:
                    # 假设结构: 首页 > 书名 > 章节
                     # 或者是: 首页 > 分类 > 书名 > 章节
                    # 取倒数第二个 link 的文本通常是书名 (因为最后一个是章节，或者没有链接)
                    
                    # 倒叙遍历链接
                    for link in reversed(links):
                        lt = link.get_text(strip=True)
                        if "小说" in lt or "首页" in lt: continue
                        # 过滤掉显然是分类的 (2个字)
                        if len(lt) == 2: continue
                        
                        return lt

        # 策略 C: 通用面包屑 (breadcrumb/path/crumb)
        for crumb in soup.select('.breadcrumb, .breadcrumbs, .path, .crumb, [id*="breadcrumb"], [class*="breadcrumb"], [class*="crumb"], [class*="path"]'):
            links = crumb.find_all('a')
            for link in reversed(links):
                candidate = _clean_candidate(link.get_text(strip=True))
                if candidate and not _is_noise(candidate):
                    return candidate

        # 最后兜底 ...
        return None
    def search_and_switch_source(self, book_name, target_chapter_id):
        """
        根据书名和目标章节ID，全网搜索备选源，并寻找匹配的章节链接
        """
        info("Switch", f"正在为《{book_name}》第 {target_chapter_id} 章寻找新源...")
        
        # 1. 全网搜索备选源 (复用 SearchHelper)
        # 搜索关键词加上 "目录"，提高命中率
        from spider_core import searcher # 确保引用
        search_results = searcher.search_bing(book_name)
        
        if not search_results:
            info("Switch", "未搜索到任何结果")
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
                        info("Switch", f"✅ 在 [{domain}] 找到匹配章节: {chap['name']}")
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
        info("SmartURL", f"Analyzing: {url}")
        
        # 1. 特征预判：如果 URL  以 .html 结尾且包含数字，大概率是章节，直接返回
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
                info("SmartURL", f"检测到目录页，自动跳转第一章: {first_chap}")
                return first_chap
                
            # 如果不是目录，说明可能是一个不带 .html 后缀的章节页 (如 xbqg77)
            # 或者爬虫没解析对，为了安全，原样返回
            return url
            
        except Exception as e:
            error("SmartURL", f"Resolve Error: {e}")
            return url
    def _fetch_page_smart(self, url, retry=None, timeout=None):
        """
        基础请求：支持自定义重试次数和超时时间
        配合 get_toc 的 fast_mode 使用
        """
        # Request-local defaults preserve adapter compatibility without changing
        # the shared crawler instance while other requests are in flight.
        options = _toc_request_options.get()
        current_retry = retry if retry is not None else (options[1] if options else 3)
        current_timeout = timeout if timeout is not None else (options[0] if options else self.timeout)

        headers = {
            "Referer": url,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
        }
        for i in range(current_retry):
            try:
                resp = cffi_requests.get(
                    url, 
                    impersonate=self.impersonate, 
                    timeout=current_timeout,  # <--- 关键：使用动态超时
                    headers=headers, 
                    allow_redirects=True, 
                    proxies=self.proxies
                )
                
                return self._decode_page_response(resp)

            except Exception as e: 
                # 只有不是最后一次重试时才 sleep
                if i == current_retry - 1: 
                    break
                time.sleep(1)

        # curl_cffi provides the preferred browser fingerprint, but network
        # resolution can fail independently of the normal HTTP client.
        try:
            resp = requests.get(
                url,
                headers=headers,
                timeout=current_timeout,
                allow_redirects=True,
                proxies=self.proxies or None,
            )
            return self._decode_page_response(resp)
        except Exception:
            return None

    @staticmethod
    def _decode_page_response(resp):
        """Decode a crawler response consistently across HTTP clients."""
        content = resp.content
        try:
            tree = lxml_html.fromstring(content, parser=lxml_html.HTMLParser(encoding='utf-8'))
            charset = tree.xpath('//meta[contains(@content, "charset")]/@content') or tree.xpath('//meta/@charset')
            encoding = 'utf-8'
            if charset:
                match = re.search(r'charset=([\w-]+)', str(charset[0]), re.I)
                encoding = match.group(1) if match else charset[0]
            return content.decode(encoding)
        except Exception:
            pass
        for encoding in ['utf-8', 'gb18030', 'gbk', 'big5']:
            try:
                return content.decode(encoding)
            except Exception:
                continue
        return content.decode('utf-8', errors='replace')

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
        # 记录原始顺序，用于无ID场景
        for i, c in enumerate(raw_chapters):
            if 'origin_idx' not in c: c['origin_idx'] = i

        unique = {}
        for c in raw_chapters:
            url = c['url']
            if url not in unique or (not unique[url].get('id') and c.get('id')):
                unique[url] = c
        
        processed_list = []
        for c in unique.values():
            raw_title = c.get('title') or c.get('raw_title') or ""
            if any(x in raw_title for x in ['最新章节', '全文阅读', '无弹窗', 'txt下载']) and not re.search(r'\d', raw_title): continue
            
            # 只有在没有预设 ID 时才解析
            chap_id = c.get('id')
            if chap_id is None or chap_id <= 0:
                chap_id = parse_chapter_id(raw_title)
            
            pure_name = re.sub(r'^(?:第)?\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回]', '', raw_title).strip()
            pure_name = re.sub(r'^\d+\s*\.?\s*', '', pure_name).strip()
            
            c['id'] = chap_id
            c['name'] = pure_name or raw_title
            c['raw_title'] = raw_title
            c['title'] = raw_title # [核心修复] 补上这个键，防止后端报错
            processed_list.append(c)
            
        # Volumes restart chapter numbers on many sources.  Keep volume order
        # from the source and only sort a volume when every entry is numbered.
        if any(c.get('volume') for c in processed_list):
            groups = []
            group_by_volume = {}
            for chapter in processed_list:
                volume = chapter.get('volume') or '__unsectioned__'
                if volume not in group_by_volume:
                    group_by_volume[volume] = []
                    groups.append(group_by_volume[volume])
                group_by_volume[volume].append(chapter)
            final_chapters = []
            for group in groups:
                if group and all(chapter['id'] > 0 for chapter in group):
                    group.sort(key=lambda chapter: (chapter['id'], chapter.get('origin_idx', 0)))
                else:
                    group.sort(key=lambda chapter: chapter.get('origin_idx', 0))
                final_chapters.extend(group)
            return final_chapters

        numbered = [c for c in processed_list if c['id'] > 0]
        others = [c for c in processed_list if c['id'] <= 0]
        
        # 排序策略：优先按 ID，ID 相同（或都小于等于0）按原始出现顺序
        numbered.sort(key=lambda x: (x['id'], x.get('origin_idx', 0)))
        others.sort(key=lambda x: x.get('origin_idx', 0))
        
        if len(numbered) > 10:
            final_chapters = numbered
            prologues = [c for c in others if "序" in c['raw_title'] or "引" in c['raw_title']]
            final_chapters = prologues + final_chapters
        else: final_chapters = others + numbered
        return final_chapters
    def _get_book_meta(self, soup, base_url):
        meta = {"cover": "", "author": "未知作者", "desc": ""}
        
        # 1. 封面
        og_img = soup.find('meta', property='og:image')
        if og_img: meta['cover'] = urljoin(base_url, og_img.get('content', ''))
        else:
            # 兜底找 img
            img = soup.find('div', class_=re.compile(r'cover|img|book-img')).find('img') if soup.find('div', class_=re.compile(r'cover|img|book-img')) else None
            if img: meta['cover'] = urljoin(base_url, img.get('src', ''))

        # 2. 作者
        og_author = soup.find('meta', property='og:novel:author')
        if og_author: meta['author'] = og_author.get('content', '')
        else:
            for tag in soup.find_all(['p', 'span', 'div']):
                txt = tag.get_text(strip=True)
                if txt.startswith('作者：') or txt.startswith('作者:'):
                    meta['author'] = txt.replace('作者：', '').replace('作者:', '').strip()
                    break
        
        # 3. 简介
        og_desc = soup.find('meta', property='og:description')
        if og_desc: meta['desc'] = og_desc.get('content', '')[:100] + '...'

        info("Meta", f"cover={meta['cover']}")
        return meta
    def get_toc(self, url, fast_mode=False, no_cache=False):
        token = _toc_request_options.set((5, 1) if fast_mode else (15, 3))
        try:
            return self._get_toc(url, fast_mode=fast_mode, no_cache=no_cache)
        finally:
            _toc_request_options.reset(token)

    def _get_toc(self, url, fast_mode=False, no_cache=False):
        """
        获取目录
        :param no_cache: 如果为 True，强制忽略本地缓存文件
        """
        if not url: return None
        
        url_hash = hashlib.md5(url.encode()).hexdigest()
        # [修复] 使用 CACHE_DIR 而不是 managers.CACHE_DIR
        cache_path = os.path.join(CACHE_DIR, f"{url_hash}.json")
        
        # 1. 尝试读缓存 (如果没开启 no_cache)
        if not no_cache and os.path.exists(cache_path):
             try:
                # 检查过期时间 (例如 12 小时)
                if time.time() - os.path.getmtime(cache_path) < 43200: 
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if data and data.get('chapters'):
                            info("Crawler", f"✅ 命中本地目录缓存: {url}")
                            return self._recognize_toc_payload(data, url)
             except: pass
             
        # 2. 尝试远程集群获取目录
        payload = {'url': url}
        if no_cache:
            payload['no_cache'] = True
            
        remote_data = _remote_request('toc', payload)
        if remote_data:
            info("Crawler", f"📥 远程获取目录成功，写入本地缓存")
            # 写入缓存
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(remote_data, f, ensure_ascii=False, indent=2)
            except: pass
            return self._recognize_toc_payload(remote_data, url)
        
        # 3. 降级到本地获取
        info("Spider", f"[Crawler] 🌐 远程不可用，本地获取目录 (强制刷新={no_cache}): {url}")
        
        adapter = plugin_mgr.find_match(url)
        data = None
        final_meta = {"cover": "", "author": "", "desc": "", "tags": []}


        try:
            if adapter: 
                # 1. 调用适配器获取目录 (标准操作)
                data = adapter.get_toc(self, url)
                info("TOC", f"adapter={adapter.__class__.__name__} data={data}")
                
                # 2. [新增功能] 检查并调用适配器的 get_meta 方法
                if hasattr(adapter, 'get_meta'):
                    try:
                        info("Crawler", f"⚡ 优先调用适配器元数据接口: {adapter.__class__.__name__}")
                        plugin_meta = adapter.get_meta(self, url)
                        
                        if plugin_meta:
                            # 优先使用适配器返回的数据 (如果非空)
                            if plugin_meta.get('cover'): final_meta['cover'] = plugin_meta['cover']
                            if plugin_meta.get('author'): final_meta['author'] = plugin_meta['author']
                            if plugin_meta.get('desc'): final_meta['desc'] = plugin_meta['desc']
                            if plugin_meta.get('tags'): final_meta['tags'] = plugin_meta['tags']
                            info("Meta", f"adapter_meta cover={final_meta['cover']}")
                    except Exception as e:
                        warn("Crawler", f"⚠️ 适配器 get_meta 执行出错: {e}")

                # 3. 兜底：如果 get_meta 没实现或没返回，尝试从 get_toc 的结果里找
                if data:
                    if not final_meta['cover'] and data.get('cover'): final_meta['cover'] = data['cover']
                    if not final_meta['author'] and data.get('author'): final_meta['author'] = data['author']
                    if not final_meta['desc'] and data.get('desc'): final_meta['desc'] = data.get('desc')
                    if not final_meta['tags'] and data.get('tags'): final_meta['tags'] = data['tags']
                    info("Meta", f"toc_meta cover={final_meta['cover']}")
            else:
                # 通用逻辑
                data = self._general_toc_logic(url)
                info("TOC", f"general data={data}")
                if data:
                    final_meta['cover'] = data.get('cover', '')
                    final_meta['author'] = data.get('author', '')
                    final_meta['desc'] = data.get('desc', '')
                    info("Meta", f"general_meta cover={final_meta['cover']}")

        except Exception as e:
            warn("TOC", f"目录获取失败: source={url} error={type(e).__name__}: {e}")
            return None

        if not data or not data.get('chapters'):
            info("TOC", f"empty or no chapters: data={data}")
            return None
        
        if data.get('manual_sort') is True:
            return self._recognize_toc_payload({**data, **final_meta}, url)
        final_chapters = self._standardize_chapters(data['chapters'])
        
        # 返回合并后的结果
        return self._recognize_toc_payload({
            'title': data['title'], 
            'chapters': final_chapters,
            'cover': final_meta['cover'],
            'author': final_meta['author'],
            'desc': final_meta['desc'],
            'tags': final_meta['tags']
        }, url)

    def _general_toc_logic(self, toc_url):
        # Directory sites commonly split a long catalog into numbered pages.
        # Keep the traversal bounded and only enqueue explicit catalog pagination.
        pending_urls = [toc_url]
        visited_urls = set()
        raw_chapters = []
        title = ''
        meta = {'cover': '', 'author': '', 'desc': ''}

        while pending_urls and len(visited_urls) < 30:
            current_url = pending_urls.pop(0)
            if current_url in visited_urls:
                continue
            visited_urls.add(current_url)

            html = self._fetch_page_smart(current_url)
            if not html:
                continue
            soup = BeautifulSoup(html, 'html.parser')
            recognized = self.recognition_engine.analyze_html(html, current_url)
            if recognized.page_type is PageType.BLOCKED:
                if current_url == toc_url:
                    return None
                continue

            if not title:
                title = recognized.title or self._get_smart_title(soup)
                meta = self._get_book_meta(soup, current_url)
            page_chapters = recognized.chapters or self._parse_chapters_from_soup(soup, current_url)
            raw_chapters.extend(page_chapters)

            page_urls = []
            if recognized.next_page_url and (recognized.page_type is PageType.TOC or page_chapters):
                page_urls.append(recognized.next_page_url)
            for select in soup.find_all('select'):
                for option in select.find_all('option'):
                    value = (option.get('value') or '').strip()
                    # A numeric option commonly relies on site-specific
                    # JavaScript (for example value="2" -> p-2.html). It is
                    # not a URL and resolving it creates a false 404 request.
                    if value and not value.isdigit():
                        page_url = urljoin(current_url, value)
                        if page_url.rstrip('/') != current_url.rstrip('/'):
                            page_urls.append(page_url)
            for page_url in page_urls:
                if page_url not in visited_urls and page_url not in pending_urls:
                    pending_urls.append(page_url)

        normalized = self.recognition_engine.normalize_payload({'chapters': raw_chapters}, toc_url) or {}
        return {
            'title': title,
            'chapters': normalized.get('chapters', []),
            'cover': meta['cover'],
            'author': meta['author'],
            'desc': meta['desc']
        }

    def get_latest_chapter(self, toc_url, no_cache=False):
        """
        获取最新章节信息
        :param no_cache: 是否强制刷新
        """
        # [修复] 传递 no_cache 参数给 get_toc
        toc = self.get_toc(toc_url, fast_mode=True, no_cache=no_cache)
        
        if toc and toc.get('chapters'):
            return toc['chapters'][-1]
        return None

    def run(self, url, no_cache=False, username=None):
        """
        智能爬取：自动去重 + 结果共享
        如果同一 URL 正在被其他请求爬取，则等待结果而非重复爬取。
        username 显式传入时（导出/下载等无会话场景），全文缓存同样生效。
        """
        if not url:
            return None

        if username is None:
            try:
                from flask import session, has_request_context
                if has_request_context():
                    username = session.get('user', {}).get('username')
            except Exception:
                username = None

        # 0. 最优先：检查全文缓存（仅对明确的阅读者生效）。
        # managers 反向依赖 spider_core（导出/集群任务），因此保持惰性导入。
        if username and not no_cache and not url.startswith('epub:'):
            try:
                import managers
                book_key = managers.fulltext_cache_manager.find_book_for_url(url, username)
                if book_key:
                    cached_chapter = managers.fulltext_cache_manager.get_chapter_from_cache(
                        book_key, url, username=username)
                    if cached_chapter:
                        info("Crawler", f"🎯 命中全文缓存: {book_key}")
                        neighbors = managers.fulltext_cache_manager.get_neighbors(
                            book_key, url, username=username)
                        return self._recognize_payload({
                            'content': cached_chapter['content'].split('\n'),
                            'title': cached_chapter['title'],
                            'book_name': book_key,
                            'prev': neighbors.get('prev'),
                            'next': neighbors.get('next'),
                            'toc_url': neighbors.get('toc_url'),
                            'from_fulltext_cache': True
                        }, url, PageType.CHAPTER)
            except Exception as e:
                warn("Crawler", f"全文缓存查询失败: {e}")

        # 1. 其次：检查临时本地缓存
        if not no_cache and not url.startswith('epub:'):
            try:
                import managers
                cached_data = managers.cache.get(url)
                if cached_data:
                    info("Crawler", f"✅ 命中临时缓存: {url[:50]}")
                    return self._recognize_payload(cached_data, url)
            except Exception as e:
                warn("Crawler", f"临时缓存查询失败: {e}")
        
        # 1. [核心去重] 检查是否有正在进行的任务
        import threading
        with self._task_lock:
            # [关键修复] 如果是强制刷新（no_cache=True），且已有任务记录已经携带了结果（上一次爬取的残余），
            # 则不应进入“等待者”模式，而应该创建新任务重新爬取。
            if url in self._active_tasks and (not no_cache or self._active_tasks[url]['result'] is None):
                info("Crawler", f"🔄 检测到重复请求 {url[:80]}，等待已有任务完成...")
                task_info = self._active_tasks[url]
                is_waiter = True
            else:
                # 创建新任务记录（如果 no_cache=True 且已有结果，则会被这里覆盖）
                info("Crawler", f"🆕 创建新爬取任务: {url[:80]}")
                task_info = {
                    'event': threading.Event(),
                    'result': None,
                    'error': None
                }
                self._active_tasks[url] = task_info
                is_waiter = False
        
        # 2. 如果是等待者，阻塞等待结果
        if is_waiter:
            task_info['event'].wait(timeout=30)  # 最多等待 30 秒
            if task_info['result'] is not None:
                info("Crawler", f"✅ 获得共享结果: {url[:80]}")
                return task_info['result']
            elif task_info['error'] is not None:
                error("Crawler", f"❌ 主任务失败: {task_info['error']}")
                return None
            else:
                info("Crawler", f"⏰ 等待超时，尝试自己爬取")
                # 超时后尝试自己爬取（防止死锁）
        
        # 3. 我们是执行者，开始实际爬取
        try:
            result = self._do_actual_crawl(url, no_cache=no_cache)
            result = self._recognize_payload(result, url) if result else None
            
            # 保存结果并通知所有等待者
            with self._task_lock:
                if url in self._active_tasks:
                    self._active_tasks[url]['result'] = result
                    self._active_tasks[url]['event'].set()
                    info("Crawler", f"📢 爬取完成，通知等待者: {url[:80]}")
            
            return result
        
        except Exception as e:
            # 保存错误并通知等待者
            with self._task_lock:
                if url in self._active_tasks:
                    self._active_tasks[url]['error'] = str(e)
                    self._active_tasks[url]['event'].set()
            error("Crawler", f"❌ 爬取失败: {e}")
            return None
        
        finally:
            # 延迟清理任务记录（60秒后），避免内存泄漏
            threading.Timer(60, lambda: self._cleanup_task(url)).start()
    
    def _cleanup_task(self, url):
        """清理已完成的任务记录"""
        with self._task_lock:
            if url in self._active_tasks:
                del self._active_tasks[url]
                info("Crawler", f"🧹 清理任务记录: {url[:80]}")
    
    def _do_actual_crawl(self, url, no_cache=False):
        """
        实际执行爬取的逻辑（原 run 方法的核心部分）
        """
        import managers
        cache = managers.cache
        if self.source_health.cooldown_remaining(url) > 0:
            return self._source_cooldown_payload(url)

        # 1. 尝试远程集群爬取 (Pull/Push 模式通用)
        payload = {'url': url}
        if no_cache:
            payload['no_cache'] = True

        remote_data = _remote_request('run', payload)

        if remote_data:
            info("Crawler", f"📥 远程抓取成功，写入本地缓存")
            result = self._recognize_payload(remote_data, url)
            self._record_source_result(url, result)
            if self._cacheable_result(result):
                cache.set(url, result)
            return result

        # 2. 降级回本地爬取 (原有逻辑)
        info("Run", f"🐢 远程不可用或未配置，开始本地爬取: {url}")
        info("Spider", f"\n[Run] 🚀 开始处理 URL: {url}")

        # 3. 尝试匹配插件
        adapter = plugin_mgr.find_match(url)
        if adapter:
            info("Run", f"✨ 匹配到适配器: {adapter.__class__.__name__}")
            result = adapter.run(self, url)
            if not result:
                self._record_source_result(url, None)
                return None
            info("Run", f"📦 插件返回书名: {result.get('book_name', '未知')}")
            result = self._recognize_payload(result, url)
            self._record_source_result(url, result)
            if self._cacheable_result(result):
                cache.set(url, result)
            return result

        info("Run", f"🌐 未找到插件，使用通用逻辑...")
        # 3.5 站点规则层：已验收的声明式规则优先于通用启发式，失败即回退
        rule_payload = self._apply_site_rule(url)
        if rule_payload is not None:
            result = self._recognize_payload(rule_payload, url)
            self._record_source_result(url, result)
            if self._cacheable_result(result):
                cache.set(url, result)
            return result

        # 4. 如果没插件，执行通用逻辑
        result = self._general_run_logic(url)
        self._record_source_result(url, result)
        if self._cacheable_result(result):
            cache.set(url, result)
        return result

    def _apply_site_rule(self, url):
        """命中 active 站点规则时产出同构 payload，未命中/失败返回 None。"""
        try:
            from services.site_rules import apply_site_rule_for_url, fetch_rule_page
            payload, trace = apply_site_rule_for_url(url, lambda target: fetch_rule_page(target, self, url))
        except Exception as exc:
            debug("Run", f"站点规则层异常: {type(exc).__name__}: {exc}")
            return None
        if payload is not None:
            info("Run", f"📏 站点规则命中: {trace.get('domain')} "
                        f"(pages={trace.get('pages')}, paras={trace.get('paragraphs')})")
        else:
            debug("Run", f"站点规则未命中: {trace.get('reason') or trace.get('detail')}")
        return payload

    @staticmethod
    def _cacheable_result(result):
        """章节结果才写入临时缓存。

        目录误判页不缓存：否则章节 URL 会把 TOC payload 固化 30 天，
        之后每次访问都直接重定向，没有重试机会。
        """
        return bool(is_cacheable_payload(result)) and result.get('page_type') != 'toc'
    
    def _general_run_logic(self, url):
        try:
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
                recognized = self.recognition_engine.analyze_html(html, current_url)
                if page_count == 0 and recognized.page_type is PageType.TOC and recognized.confidence >= 0.6:
                    return recognized.to_payload()
                if page_count == 0 and recognized.page_type is PageType.BLOCKED:
                    return recognized.to_payload()

                current_title = recognized.title or self._get_smart_title(soup)
                if page_count == 0: original_title = current_title
                elif current_title != original_title and len(current_title) > 3: break
                content = recognized.content or self._extract_content_smart(soup)
                if content and original_title in content[0]: content = content[1:]
                combined_content.extend(content)
                next_page_url = recognized.next_page_url
                next_chapter_url = recognized.next_url
                prev_chapter_url = recognized.prev_url
                toc_url = recognized.toc_url
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
                return self._recognize_payload(first_page_meta, url, PageType.CHAPTER)
            return None
        except Exception as e:
            error("Run", f"General logic error: {e}")
            return None

    def get_first_chapter(self, toc_url):
        try:
            res = self.get_toc(toc_url)
            if res and res.get('chapters') and len(res['chapters']) > 0:
                return res['chapters'][0].get('url')
        except Exception as e:
            error("Crawler", f"get_first_chapter error: {e}")
        return None

# ... (EpubHandler 保持不变) ...
# spider_core.py

# === spider_core.py 中的 EpubHandler 类 ===

class EpubHandler:
    MAX_ARCHIVE_FILES = 10_000
    MAX_UNCOMPRESSED_SIZE = 150 * 1024 * 1024

    def __init__(self):
        self.lib_dir = LIB_DIR
        if not os.path.exists(self.lib_dir): os.makedirs(self.lib_dir)
        # 分页阈值：每页大约 3000 字 (过大容易卡顿，过小翻页太累)
        self.CHUNK_SIZE = 1500 

    def save_file(self, file_obj):
        """校验 EPUB 压缩包后，以不可预测的文件名保存。"""
        source_name = secure_filename(file_obj.filename or '')
        if not source_name.lower().endswith('.epub'):
            raise ValueError('仅支持 EPUB 文件')

        filename = f"{uuid.uuid4().hex}_{source_name}"
        filepath = os.path.join(self.lib_dir, filename)
        temporary_path = f"{filepath}.uploading"
        try:
            file_obj.save(temporary_path)
            self._validate_epub_archive(temporary_path)
            os.replace(temporary_path, filepath)
            return filename
        except Exception:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
            raise

    @classmethod
    def _validate_epub_archive(cls, filepath):
        if not zipfile.is_zipfile(filepath):
            raise ValueError('文件不是有效的 EPUB 压缩包')

        with zipfile.ZipFile(filepath) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > cls.MAX_ARCHIVE_FILES:
                raise ValueError('EPUB 文件条目数量异常')

            total_size = sum(entry.file_size for entry in entries)
            if total_size > cls.MAX_UNCOMPRESSED_SIZE:
                raise ValueError('EPUB 解压后的体积超过限制')

            for entry in entries:
                normalized_name = entry.filename.replace('\\', '/')
                if normalized_name.startswith('/') or '..' in normalized_name.split('/'):
                    raise ValueError('EPUB 包含非法文件路径')

            try:
                mimetype = archive.read('mimetype')
            except KeyError as exc:
                raise ValueError('EPUB 缺少 mimetype 文件') from exc
            if mimetype.strip() != b'application/epub+zip':
                raise ValueError('文件不是有效的 EPUB 格式')

    def _flatten_toc(self, toc, flat_list=None):
        """
        递归展平 TOC 结构，提取所有章节的 {title, href}
        """
        if flat_list is None: flat_list = []
        for item in toc:
            if isinstance(item, (list, tuple)):
                # 这是一个章节节点 (Section, [Children])
                section = item[0]
                children = item[1] if len(item) > 1 else []
                
                href = section.href if hasattr(section, 'href') else ''
                title = section.title if hasattr(section, 'title') else ''
                
                if href and title:
                    flat_list.append({'title': title, 'href': href})
                
                if children:
                    self._flatten_toc(children, flat_list)
            elif hasattr(item, 'href') and hasattr(item, 'title'):
                # 简单节点 (Link)
                flat_list.append({'title': item.title, 'href': item.href})
        return flat_list

    def get_toc(self, filename):
        filepath = os.path.join(self.lib_dir, filename)
        if not os.path.exists(filepath): return None
        try:
            book = epub.read_epub(filepath)
            title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else filename
            
            raw_toc = book.toc
            chapters = []
            
            if raw_toc:
                flat_toc = self._flatten_toc(raw_toc)
                # 映射到 URL 格式
                for item in flat_toc:
                    # 忽略 href 中的锚点部分 (chapter1.html#section1 -> chapter1.html)
                    clean_href = item['href'].split('#')[0]
                    chapters.append({
                        'title': item['title'], 
                        # 格式: epub:文件名:href:页码
                        'url': f"epub:{filename}:{clean_href}:0" 
                    })
            else:
                # 兜底：没有目录就用物理文件列表
                for i, item in enumerate(book.spine):
                    item_id = item[0]
                    chapters.append({
                        'title': f"第 {i+1} 节", 
                        'url': f"epub:{filename}:{item_id}:0" 
                    })

            return {'title': title, 'chapters': chapters}
        except Exception as e: 
            error("Spider", f"EPUB TOC Error: {e}")
            return None

    def get_chapter_content(self, filename, item_identifier, page_index=0):
        """
        item_identifier: 可能是 href (chapter1.html) 也可能是 item_id
        """
        filepath = os.path.join(self.lib_dir, filename)
        try:
            book = epub.read_epub(filepath)
            
            # --- 1. 优先从 TOC 元数据反查标题 (最准确) ---
            current_title = None
            if book.toc:
                flat_toc = self._flatten_toc(book.toc)
                for item in flat_toc:
                    # 对比 href (忽略锚点差异)
                    if item['href'].split('#')[0] == item_identifier.split('#')[0]:
                        current_title = item['title']
                        break
            
            # --- 2. 定位内容 Item ---
            target_item = None
            # 尝试按 href 找
            for item in book.get_items():
                if item.get_name() == item_identifier:
                    target_item = item
                    break
            # 尝试按 ID 找
            if not target_item:
                target_item = book.get_item_with_id(item_identifier)
            
            if not target_item:
                return {'title': '错误', 'content': ['章节文件丢失']}

            # --- 3. 解析 HTML 内容 ---
            soup = BeautifulSoup(target_item.get_content(), 'html.parser')
            
            # 如果 TOC 没查到标题，尝试从 HTML h1/h2 抓取兜底
            if not current_title:
                title_tag = soup.find(['h1', 'h2', 'title'])
                if title_tag:
                    current_title = title_tag.get_text(strip=True)
                else:
                    current_title = "未知章节"

            # 提取正文 (过滤掉空行)
            raw_lines = [p.get_text(strip=True) for p in soup.find_all(['p', 'div', 'span']) if p.get_text(strip=True)]
            
            # --- 4. 长章节分页算法 ---
            chunks = []
            if not raw_lines:
                chunks = [["(本章无文字内容)"]]
            else:
                current_chunk = []
                current_length = 0
                
                for line in raw_lines:
                    current_chunk.append(line)
                    current_length += len(line)
                    
                    # 当累积字数超过阈值，切一页
                    if current_length >= self.CHUNK_SIZE:
                        chunks.append(current_chunk)
                        current_chunk = []
                        current_length = 0
                
                if current_chunk:
                    chunks.append(current_chunk)

            # 校验页码范围
            if page_index >= len(chunks): page_index = len(chunks) - 1
            if page_index < 0: page_index = 0
            
            # --- 5. 构建翻页链接 (支持内部翻页 + 跨章翻页) ---
            prev_url = None
            next_url = None
            
            # A. 内部翻页 (页码加减)
            if page_index > 0:
                prev_url = f"epub:{filename}:{item_identifier}:{page_index-1}"
            if page_index < len(chunks) - 1:
                next_url = f"epub:{filename}:{item_identifier}:{page_index+1}"
            
            # B. 跨章翻页 (如果内部没页了)
            if (not prev_url or not next_url) and book.toc:
                # 为了跨章，我们需要知道当前 href 在 TOC 中的位置
                flat_toc = self._flatten_toc(book.toc)
                curr_idx = -1
                clean_id = item_identifier.split('#')[0]
                
                for i, item in enumerate(flat_toc):
                    if item['href'].split('#')[0] == clean_id:
                        curr_idx = i
                        break
                
                if curr_idx != -1:
                    # 上一章 (如果有)
                    if not prev_url and curr_idx > 0:
                        prev_href = flat_toc[curr_idx - 1]['href'].split('#')[0]
                        # 这是一个简化的假设：跳到上一章的第0页。
                        # (虽然体验上应该跳到上一章的"最后一页"，但那样需要预加载上一章计算页数，性能开销太大)
                        prev_url = f"epub:{filename}:{prev_href}:0"
                    
                    # 下一章 (如果有)
                    if not next_url and curr_idx < len(flat_toc) - 1:
                        next_href = flat_toc[curr_idx + 1]['href'].split('#')[0]
                        next_url = f"epub:{filename}:{next_href}:0"

            # 标题带上页码提示
            display_title = current_title
            if len(chunks) > 1:
                display_title = f"{current_title} ({page_index+1}/{len(chunks)})"

            return {
                'title': display_title,
                'content': chunks[page_index],
                'prev': prev_url,
                'next': next_url,
                'toc_url': f"epub:{filename}:toc"
            }

        except Exception as e: 
            return {'title': '解析错误', 'content': [f"错误详情: {str(e)}"]}
# 实例化对象
crawler_instance = NovelCrawler()
searcher = SearchHelper()
epub_handler = EpubHandler()
