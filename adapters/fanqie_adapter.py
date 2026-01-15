import re
import json
from urllib.parse import urljoin

class FanqieAPIAdapter:
    """
    番茄小说 API 适配器
    基于第三方接口: https://qkfqapi.vv9v.cn
    """
    
    BASE_API = "https://qkfqapi.vv9v.cn/api"

    def can_handle(self, url):
        """
        匹配番茄小说的 URL
        常见格式: 
        - https://fanqienovel.com/page/123456 (书籍页)
        - https://fanqienovel.com/reader/123456 (阅读页)
        """
        return "fanqienovel.com" in url or "fqnovel.com" in url

    def _get_book_id(self, url):
        # 提取 book_id (page/后面的一串数字)
        match = re.search(r'page/(\d+)', url)
        if match: return match.group(1)
        # 有时候传入的是 reader 链接，尝试回溯或者需要额外处理，这里主要针对目录页
        return None

    def _get_item_id(self, url):
        # 提取 item_id (reader/后面的一串数字)
        match = re.search(r'reader/(\d+)', url)
        if match: return match.group(1)
        return None

    def get_toc(self, crawler, toc_url):
        """
        获取目录
        API: /api/directory?book_id=xxx
        """
        book_id = self._get_book_id(toc_url)
        if not book_id:
            print(f"[FanqieAPI] 无法从 URL 提取 Book ID: {toc_url}")
            return None

        api_url = f"{self.BASE_API}/directory?book_id={book_id}"
        
        try:
            # 复用 crawler 的请求方法 (自带重试和 headers)
            # 注意：crawler._fetch_page_smart 返回的是字符串
            json_str = crawler._fetch_page_smart(api_url)
            if not json_str: return None
            
            data = json.loads(json_str)
            
            # 校验 API 返回状态 (虽然这个第三方API似乎没标准code，但通常在data里)
            if 'data' not in data or 'lists' not in data['data']:
                print("[FanqieAPI] 目录解析失败: API 数据异常")
                return None

            # 1. 获取书名 (详情接口获取书名更准，但目录接口没有，我们尝试调用 detail)
            # 为了性能，这里先尝试从 crawler 传入的页面里解析，或者再调一次 detail API
            # 既然我们有 API，再调一次 detail 比较稳
            book_name = "番茄小说"
            detail_res = crawler._fetch_page_smart(f"{self.BASE_API}/detail?book_id={book_id}")
            if detail_res:
                detail_data = json.loads(detail_res)
                if detail_data.get('data'):
                    book_name = detail_data['data'].get('book_name', book_name)

            # 2. 构建章节列表
            raw_list = data['data']['lists']
            chapters = []
            
            for item in raw_list:
                # item 结构: {'item_id': '...', 'title': '...', ...}
                item_id = item['item_id']
                title = item['title']
                
                # 构造一个标准的番茄阅读链接，以便 run 方法能识别
                # 这样即使是第三方API，我们系统里存的也是官方链接，看起来更规范
                chapter_url = f"https://fanqienovel.com/reader/{item_id}"
                
                chapters.append({
                    'title': title,
                    'url': chapter_url
                })
                
            return {
                'title': book_name,
                'chapters': chapters
            }

        except Exception as e:
            print(f"[FanqieAPI] TOC Error: {e}")
            return None

    def run(self, crawler, url):
        """
        获取正文
        API: /api/content?tab=小说&item_id=xxx
        """
        item_id = self._get_item_id(url)
        if not item_id:
            return None
            
        # 构造 API 请求
        api_url = f"{self.BASE_API}/content?tab=小说&item_id={item_id}"
        
        try:
            json_str = crawler._fetch_page_smart(api_url)
            if not json_str: return None
            
            data = json.loads(json_str)
            
            if 'data' not in data or 'content' not in data['data']:
                print(f"[FanqieAPI] Content Error: 无法获取内容 - {url}")
                return None
                
            content_raw = data['data']['content'] # 这里是带 \n 的纯文本
            
            # 获取标题 (API data 里好像没直接给 title，需要从上层传或者自己提取)
            # 这里的 content 只是纯文本。为了标题，我们可能需要这里做个妥协：
            # 1. 返回 "未知章节" 让后端自己去匹配目录纠正
            # 2. 或者这个 API 其实有 title 字段？看你给的代码里 download_one_chapter 并没有返回 title
            # 既然这样，我们先返回 None 标题，依赖 smart_notedb 的目录反查机制
            
            # 清洗文本：按换行符分割
            lines = content_raw.split('\n')
            # 再次清洗空行
            clean_lines = [line.strip() for line in lines if line.strip()]
            
            return {
                'title': '', # 留空，依靠核心层的目录反查来自动填充标题
                'content': clean_lines,
                'next': None, # API 模式无法直接获知下一章 URL，依赖目录顺序
                'prev': None
            }

        except Exception as e:
            print(f"[FanqieAPI] Run Error: {e}")
            return None