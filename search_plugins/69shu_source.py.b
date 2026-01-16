from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

class SourceWorker:
    def __init__(self):
        self.source_name = "69书吧 🟢"
        self.base_url = "https://www.69shu.com"
        self.search_url = "https://www.69shu.com/modules/article/search.php"

    def search(self, keyword):
        try:
            # 69书吧必须用 POST，且需要 GBK 编码
            data = {
                'searchkey': keyword.encode('gbk'),
                'searchtype': 'articlename'
            }
            
            # 使用 curl_cffi 模拟浏览器指纹
            resp = cffi_requests.post(
                self.search_url, 
                data=data,
                impersonate="chrome110",
                timeout=10
            )
            
            # 手动解码 GBK
            content = resp.content.decode('gbk', errors='ignore')
            soup = BeautifulSoup(content, 'html.parser')
            
            results = []
            # 69书吧如果是唯一结果，会直接302跳转到目录页
            # 如果是列表页，结构通常是表格
            
            # 检查是否直接跳转到了目录页 (包含 "章节列表" 字样)
            if "章节列表" in soup.title.string:
                # 当前页面就是结果
                canonical = soup.find('link', {'rel': 'canonical'})
                if canonical:
                    results.append({
                        'title': keyword, # 简单处理
                        'url': canonical['href'],
                        'source': self.source_name,
                        'description': "直达目录"
                    })
                return results

            # 解析列表
            # 69书吧列表通常在 tr 中
            # 这里简单处理，如果没匹配到直接返回空
            
            return results
        except Exception as e:
            # print(f"[Plugin] 69Shu Error: {e}")
            return []