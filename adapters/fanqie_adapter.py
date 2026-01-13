# adapters/fanqie_adapter.py
import re
import json
import time
from bs4 import BeautifulSoup

class FanqieAdapter:
    """
    番茄小说适配器 (API 模式)
    """

    def can_handle(self, url):
        return "fanqienovel.com" in url or "fqnovel.com" in url

    def _get_book_id(self, url):
        # 从 URL 提取 Book ID
        # 例如: https://fanqienovel.com/page/123456789
        match = re.search(r'page/(\d+)', url)
        if match: return match.group(1)
        return None

    def _get_item_id(self, url):
        # 从 URL 提取 Item ID (章节ID)
        # 例如: https://fanqienovel.com/reader/123456...
        match = re.search(r'reader/(\d+)', url)
        if match: return match.group(1)
        return None

    def get_toc(self, crawler, toc_url):
        book_id = self._get_book_id(toc_url)
        if not book_id:
            return None

        # 构造 API URL
        api_url = f"https://fanqienovel.com/api/reader/directory/detail?bookId={book_id}"
        
        # 必须模拟真实浏览器头
        # 这里的 User-Agent 最好用手机端的，或者保持你 curl_cffi 的默认配置
        try:
            # 使用 crawler 的智能 fetch，它自带 curl_cffi
            # 注意：这里我们期望返回 JSON，但 crawler._fetch_page_smart 返回的是 text
            # 我们手动解析 text 为 json
            response_text = crawler._fetch_page_smart(api_url)
            if not response_text: return None

            data = json.loads(response_text)
            
            # 解析 JSON 结构
            # 结构通常是: data -> allItemIds (或者 chapterList)
            # 番茄接口经常变，这里假设是标准结构，如果失败需要打印 data 调试
            if data.get('code') != 0:
                print(f"[Fanqie] API Error: {data.get('message')}")
                return None

            chapter_list = data['data']['chapterList']
            chapters = []
            
            for item in chapter_list:
                chapters.append({
                    'title': item['title'],
                    # 构造这一章的阅读链接，方便下面 run 方法识别
                    'url': f"https://fanqienovel.com/reader/{item['itemId']}" 
                })

            # 书名通常在 data['data']['bookInfo']['originalName']
            book_name = data.get('data', {}).get('bookInfo', {}).get('originalName', '番茄小说')

            return {'title': book_name, 'chapters': chapters}

        except Exception as e:
            print(f"[Fanqie] TOC Error: {e}")
            return None

    def run(self, crawler, url):
        item_id = self._get_item_id(url)
        if not item_id: return None

        # 构造正文 API
        api_url = f"https://fanqienovel.com/api/reader/full?itemId={item_id}"

        try:
            response_text = crawler._fetch_page_smart(api_url)
            if not response_text: return None
            
            data = json.loads(response_text)
            print("data", response_text)
            if data.get('code') != 0:
                return None
            
            curr_data = data['data']['chapterData']
            title = curr_data['title']
            content_html = curr_data['content'] # 这里是 HTML 格式的内容
            
            # 清洗内容：番茄返回的是 <p>...</p> 的字符串，需要转为纯文本 list
            soup = BeautifulSoup(content_html, 'html.parser')
            lines = [p.get_text(strip=True) for p in soup.find_all('p') if p.get_text(strip=True)]
            
            # 获取上一章/下一章 ID 用于链式抓取
            # 番茄 API 通常在 preChapterId 和 nextChapterId 字段
            # 如果没有直接返回 URL，我们需要自己拼接
            # 注意：这里简化处理，如果没有 nextChapterId 可能需要去目录查
            # 但通常 API 爬取单章就够了，翻页逻辑交给前端或 crawler 的通用逻辑
            print("lines", lines)
            return {
                'title': title,
                'content': lines,
                'book_name': "番茄小说", # 暂时没法从单章接口拿书名，除非再调一次目录接口
                'next': None, # 既然是 API 模式，通常不依赖爬虫的自动翻页，或者需要解析 json 里的 nextId
                'prev': None
            }

        except Exception as e:
            print(f"[Fanqie] Content Error: {e}")
            return None