import requests
import json
import os
import dotenv

# --- 加载配置 ---
def _load_config():
    possible_paths = [
        os.path.join(os.getcwd(), 'config.env'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.env'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.env')
    ]
    for path in possible_paths:
        if os.path.exists(path):
            dotenv.load_dotenv(path)
            return True
    return False

_load_config()

class SourceWorker:
    def __init__(self):
        self.source_name = "番茄小说 (本地) 🍅"
        # 从环境变量读取配置
        self.api_host = os.environ.get("FANQIE_API_HOST", "http://127.0.0.1:9000").rstrip('/')
        self.api_token = os.environ.get("FANQIE_API_TOKEN", "").strip().strip('"').strip("'")
        self.api_url = f"{self.api_host}/search"

    def _get_headers(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def search(self, keyword):
        print(f"[FanqieLocal] 正在搜索: {keyword}")
        try:
            # 1. 发起请求 (添加 headers)
            resp = requests.get(
                self.api_url, 
                params={"key": keyword, "offset": 0}, 
                headers=self._get_headers(),
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
            # with open('debug.json', 'w', encoding='utf-8') as f:
            #     f.write(json.dumps(data))
            # 5. 提取数据
            # data = json.dumps(data)
            results = []
            # 兼容两种返回结构：data['book_data'] 或 data['data']['book_data']
            raw_data = data.get('search_tabs', {})
            book_list = []
            # raw_data = data.get("search_tabs", )
            # with open('debug.json', 'w', encoding='utf-8') as f:
                # f.write(json.dumps(raw_data)) 
            # raw_data = raw_data.get("data", [])
            if isinstance(raw_data, list):
                with open('debug.json', 'w', encoding='utf-8') as f:
                    for i, item in enumerate(raw_data):
                        print(f"第 {i} 个元素的类型: {type(item)}")
                        
                        # 获取 data 字段
                        if isinstance(item, dict):
                            allbooks = item.get("data", {})
                            
                            # 如果 allbooks 是字符串，尝试解析
                            if isinstance(allbooks, str):
                                try:
                                    allbooks = json.loads(allbooks)
                                except:
                                    print(f"第 {i} 个元素的 data 不是有效的 JSON")
                                    continue
                            
                            # 写入文件
                            if isinstance(allbooks, list):
                                for _b in allbooks:
                                    # print("")
                                    book_list.append(json.dumps(_b, ensure_ascii=False, indent=2))
                                    # f.write(json.dumps(_b, ensure_ascii=False, indent=2) + ',\n')
                            else:
                                print("")
                                # f.write(json.dumps(allbooks, ensure_ascii=False, indent=2) + ',\n')
                        else:
                            print(f"第 {i} 个元素不是字典: {type(item)}")
            # if isinstance(raw_data, list):
            #     book_list = raw_data
            # elif 'book_data' in raw_data:
            #     book_list = raw_data['book_data']
            
            for book in book_list:
                # book = json.loads(json.dumps(book["book_data"]))
                # print("book", type())
                book = json.loads(book).get("book_data")[0]
                # print(book)
                book_id = book.get('book_id')
                title = book.get('book_name')
                author = book.get('author')
                desc = book.get('abstract', '')
                print(title)
                if book_id and title:
                    results.append({
                        'title': title,
                        'url': f"https://fanqienovel.com/page/{book_id}",
                        'source': "番茄小说",
                        'description': f"作者: {author}"
                    })
                    print("appened")
            # print(results[:3])
            return results[:5]

        except Exception as e:
            with open('debug.json', 'w', encoding='utf-8') as f:
                f.write(f"[FanqieLocal] 插件运行出错: {e}")
            print(f"[FanqieLocal] 插件运行出错: {e}")
            return []