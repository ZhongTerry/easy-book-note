import requests
import json

class SourceWorker:
    def __init__(self):
        self.source_name = "番茄小说 (本地) 🍅"
        # 请确保端口与你启动 main.py 的端口一致 (9000 或 9001)
        self.api_url = "http://127.0.0.1:9000/search"

    def search(self, keyword):
        print(f"[FanqieLocal] 正在搜索: {keyword}")
        try:
            # 调用本地微服务的搜索接口
            resp = requests.get(
                self.api_url, 
                params={"key": keyword, "offset": 0}, 
                timeout=5
            )
            
            # 处理可能的双重序列化问题
            data = resp.json()
            if isinstance(data, str):
                data = json.loads(data)
                
            if data.get('code') != 0:
                return []

            results = []
            # 番茄搜索返回的数据结构通常在 data['book_data'] 里
            book_list = data.get('data', {}).get('book_data', [])
            
            for book in book_list:
                # 提取关键信息
                book_id = book.get('book_id')
                title = book.get('book_name')
                author = book.get('author')
                desc = book.get('abstract', '')
                
                if book_id and title:
                    results.append({
                        'title': title,
                        # 构造标准的目录页 URL，这样 FanqieLocalAdapter 就能识别并接管
                        'url': f"https://fanqienovel.com/page/{book_id}",
                        'source': self.source_name,
                        'description': f"作者: {author} | {desc[:20]}..."
                    })
            
            # 只取前 3 条，保证质量且不霸屏
            return results[:3]

        except Exception as e:
            print(f"[FanqieLocal] 搜索出错: {e}")
            return []