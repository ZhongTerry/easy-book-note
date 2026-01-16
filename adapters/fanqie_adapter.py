import re
import requests
import json
from urllib.parse import urljoin

class FanqieLocalAdapter:
    """
    番茄小说本地微服务适配器 (增强版)
    功能：
    1. 连接本地 FastAPI 微服务 (默认 127.0.0.1:9001)
    2. 支持自动反查 BookID
    3. 支持基于目录上下文计算 Prev/Next 链接
    """
    
    # 你的微服务地址 (请确保和 main.py 的端口一致)
    API_HOST = "http://127.0.0.1:9000"

    def can_handle(self, url):
        return "fanqienovel.com" in url or "fqnovel.com" in url

    def _get_item_id(self, url):
        # 匹配 reader/123456
        match = re.search(r'reader/(\d+)', url)
        if match: return match.group(1)
        return None

    def _get_book_id(self, url):
        # 匹配 page/123456
        match = re.search(r'page/(\d+)', url)
        if match: return match.group(1)
        return None
    # adapters/fanqie_local_adapter.py -> FanqieLocalAdapter -> get_meta

    def get_meta(self, crawler, url):
        """
        利用微服务 /get_detail 接口获取超详细元数据 (含标签)
        """
        book_id = self._get_book_id(url)
        if not book_id:
            item_id = self._get_item_id(url)
            if item_id:
                book_id = self._resolve_book_id_by_item(crawler, item_id)
        
        if not book_id: return None

        try:
            resp = requests.get(f"{self.API_HOST}/get_detail", params={"book_id": book_id}, timeout=10)
            json_data = resp.json()
            if isinstance(json_data, str): 
                try: json_data = json.loads(json_data)
                except: pass

            if json_data.get('code') == 0:
                data = json_data.get('data', {})
                
                # 1. 封面
                cover = data.get('thumb_url') or data.get('thumb_uri')
                if cover and not cover.startswith('http'):
                    cover = f"https://p3-novel.byteimg.com/origin/{cover}"

                # 2. 作者
                author = data.get('author', '未知作者')
                
                # 3. 简介
                abstract = data.get('abstract', '暂无简介').replace('\n', '<br>')
                
                # === [核心新增] 标签提取挑战 ===
                tags_list = []
                
                # A. 评分 (如果有且不为0)
                score = data.get('score')
                if score and str(score) != '0.0':
                    tags_list.append(f"{score}分")
                
                # B. 连载状态 (creation_status: 1=连载, ?=完结)
                # 番茄API中 creation_status=1 通常是连载，status=1 也是
                # 我们这里简单判定：如果 score 是完结，或者 word_number 很大，可以推测
                # 暂时先不猜状态，直接拿 category
                
                # C. 分类与标签
                category = data.get('category') # "都市高武"
                if category: tags_list.append(category)
                
                raw_tags = data.get('tags', '') # "都市高武,都市,穿越"
                if raw_tags: tags_list.extend(raw_tags.split(','))
                
                # D. 高质量标签 (如"编辑推荐")
                hq_tags = data.get('high_quality_tags', '')
                if hq_tags: tags_list.extend(hq_tags.split(','))

                # E. 去重 & 清洗 & 截断
                # 保持顺序去重
                seen = set()
                clean_tags = []
                for t in tags_list:
                    t = t.strip()
                    if t and t not in seen:
                        seen.add(t)
                        clean_tags.append(t)
                
                # 只取前 4 个标签，防止太长
                final_tags = clean_tags[:4]

                return {
                    "cover": cover,
                    "author": author,
                    "desc": abstract,
                    "book_name": data.get('book_name'),
                    "tags": final_tags # 返回列表
                }
                
        except Exception as e:
            print(f"[FanqieLocal] Detail 获取失败: {e}")
            return None
        
        return None
    def _resolve_book_id_by_item(self, crawler, item_id):
        """
        [增强版] 通过章节 ID 反查书籍 ID
        策略 A: 调用公共 API (directory/detail)
        策略 B: 爬取阅读页 HTML (正则提取)
        """
        # --- 策略 A: 公共 API (最快) ---
        # aid=1967 是番茄 App 的标识，通常这个接口无需签名即可访问
        api_url = f"https://novel.snssdk.com/api/novel/book/directory/detail/v/?item_ids={item_id}&aid=1967"
        try:
            print(f"[FanqieLocal] 正在通过 API 反查 BookID: {item_id}")
            # 使用 crawler 发送请求以利用其 header/proxy 配置
            json_str = crawler._fetch_page_smart(api_url)
            
            if json_str:
                data = json.loads(json_str)
                if data.get('code') == 0 and data.get('data'):
                    # 结构: data -> [ { "book_id": "...", ... } ]
                    info_list = data['data']
                    if isinstance(info_list, list) and len(info_list) > 0:
                        bid = info_list[0].get('book_id')
                        print(f"[FanqieLocal] API 反查成功: {bid}")
                        return bid
        except Exception as e:
            print(f"[FanqieLocal] API 反查失败: {e}")

        # --- 策略 B: 网页源码提取 (兜底) ---
        # 如果 API 挂了，我们直接请求 PC 版阅读页，HTML 里一定藏着 book_id
        page_url = f"https://fanqienovel.com/reader/{item_id}"
        try:
            print(f"[FanqieLocal] API 失败，尝试解析网页源码: {page_url}")
            html = crawler._fetch_page_smart(page_url)
            if html:
                # 1. 尝试匹配 window.__INITIAL_STATE__ 里的 bookId
                # 格式通常是: "bookId":"123456"
                match = re.search(r'"bookId":"(\d+)"', html)
                if match:
                    bid = match.group(1)
                    print(f"[FanqieLocal] 网页源码正则匹配成功: {bid}")
                    return bid
                
                # 2. 尝试匹配面包屑链接
                # <a href="/page/123456">书名</a>
                match_link = re.search(r'href="/page/(\d+)"', html)
                if match_link:
                    bid = match_link.group(1)
                    print(f"[FanqieLocal] 网页链接匹配成功: {bid}")
                    return bid

        except Exception as e:
            print(f"[FanqieLocal] 网页解析失败: {e}")

        return None

    def _fetch_toc_list(self, book_id):
        """
        从微服务获取标准化的目录列表
        返回: [{'item_id': '...', 'title': '...', 'url': '...'}, ...]
        """
        try:
            resp = requests.get(f"{self.API_HOST}/get_catalog", params={"book_id": book_id}, timeout=10)
            data = resp.json()
            if isinstance(data, str): data = json.loads(data) # 防双重序列化

            if data.get('code') != 0: return []

            raw_data = data.get('data', {})
            # 兼容微服务不同的返回结构
            raw_list = raw_data.get('item_data_list') or raw_data.get('item_list') or []
            
            chapter_list = []
            for item in raw_list:
                if isinstance(item, str): continue # 过滤纯ID
                cid = str(item.get('item_id'))
                chapter_list.append({
                    'item_id': cid,
                    'title': item.get('title', '无标题'),
                    'url': f"https://fanqienovel.com/reader/{cid}"
                })
            return chapter_list
        except Exception as e:
            print(f"[FanqieLocal] 获取目录失败: {e}")
            return []

    def get_toc(self, crawler, toc_url):
        """
        获取目录页数据
        """
        # 1. 尝试直接获取 BookID
        book_id = self._get_book_id(toc_url)
        
        # 2. 如果是 reader 链接，尝试反查
        if not book_id:
            item_id = self._get_item_id(toc_url)
            if item_id:
                book_id = self._resolve_book_id_by_item(crawler, item_id)
        
        if not book_id: return None

        # 3. 获取列表
        chapters = self._fetch_toc_list(book_id)
        
        # 4. 获取书名 (尝试从微服务详情接口拿，或者默认)
        book_title = "番茄小说"
        # 这里的请求为了速度可以省略，或者单独加一个 get_detail 调用
        
        return {
            'title': book_title,
            'chapters': chapters
        }

    def run(self, crawler, url):
        """
        获取正文 (包含自动上下文分析)
        """
        # 1. 获取当前 ItemID
        current_item_id = self._get_item_id(url)
        if not current_item_id: return None
        # 2. 并行获取内容 (Microservice)
        content_data = None
        try:
            resp = requests.get(f"{self.API_HOST}/get_content", params={
                "item_id": current_item_id,
                "text_mode": 1,
                "image_mode": 0
            }, timeout=15)
            
            data = resp.json()
            if isinstance(data, str): data = json.loads(data)
            # print(data)
            if data.get('code') == 0:
                raw_text = data.get('data', {}).get('content', '')
                if raw_text:
                    content_data = [line.strip() for line in raw_text.split('\n') if line.strip()]
        except Exception as e:
            print(f"[FanqieLocal] 正文请求错误: {e}")

        if not content_data: return None

        # 3. [核心功能] 计算上下文 (Prev/Next/Toc)
        # 我们需要 BookID 才能获取目录。
        # 策略：先尝试从 URL 找，找不到就反查。
        # 注意：这一步可能会增加请求耗时，但为了用户体验是值得的。
        
        book_id = self._get_book_id(url) # URL里通常没有
        print("ttttttt", book_id)
        if not book_id:
            book_id = self._resolve_book_id_by_item(crawler, current_item_id)
            
        prev_url = None
        next_url = None
        toc_url = None
        chapter_title = ""
        
        if book_id:
            toc_url = f"https://fanqienovel.com/page/{book_id}"
            
            # 获取全书目录列表
            toc_list = self._fetch_toc_list(book_id)
            
            # 在列表中定位当前章节
            for i, chapter in enumerate(toc_list):
                if str(chapter['item_id']) == str(current_item_id):
                    # 找到了当前章节
                    chapter_title = chapter['title']
                    
                    # 找上一章
                    if i > 0:
                        prev_url = toc_list[i-1]['url']
                    
                    # 找下一章
                    if i < len(toc_list) - 1:
                        next_url = toc_list[i+1]['url']
                    
                    break
        
        # 4. 返回完整结构
        return {
            'title': chapter_title,  # 现在我们有真标题了！
            'content': content_data,
            'book_name': '番茄小说', # 如果需要准确书名，需调 detail 接口，暂且写死
            'prev': prev_url,
            'next': next_url,
            'toc_url': toc_url
        }