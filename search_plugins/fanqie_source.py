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
            # 1. 发起请求
            resp = requests.get(
                self.api_url, 
                params={"key": keyword, "offset": 0}, 
                timeout=8
            )
            
            # 2. [调试] 打印状态码和前100个字符，看看返回了啥
            # print(f"[Debug] 状态码: {resp.status_code}")
            # print(f"[Debug] 返回内容: {resp.text[:200]}")

            if resp.status_code != 200:
                print(f"[FanqieLocal] 接口请求失败: {resp.status_code}")
                return []

            # 3. 尝试解析 JSON
            try:
                data = resp.json()
            except Exception as e:
                print(f"[FanqieLocal] JSON解析崩溃! 返回的可能不是JSON。内容预览: {resp.text[:50]}")
                return []
            
            # 4. 处理双重序列化 (String -> JSON)
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except:
                    pass
                
            if data.get('code') != 0:
                print(f"[FanqieLocal] API返回错误: {data.get('msg')}")
                return []

            # 5. 提取数据
            results = []
            # 兼容两种返回结构：data['book_data'] 或 data['data']['book_data']
            raw_data = data.get('data', {})
            book_list = []
            
            if isinstance(raw_data, list):
                book_list = raw_data
            elif 'book_data' in raw_data:
                book_list = raw_data['book_data']
            
            for book in book_list:
                book_id = book.get('book_id')
                title = book.get('book_name')
                author = book.get('author')
                desc = book.get('abstract', '')
                
                if book_id and title:
                    results.append({
                        'title': title,
                        'url': f"https://fanqienovel.com/page/{book_id}",
                        'source': self.source_name,
                        'description': f"作者: {author}"
                    })
            
            return results[:3]

        except Exception as e:
            print(f"[FanqieLocal] 插件运行出错: {e}")
            return []