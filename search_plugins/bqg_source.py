import requests
from urllib.parse import urljoin

class SourceWorker:
    def __init__(self):
        self.source_name = "笔趣阁 📚"
        self.base_url = "https://www.bqg128.cc"
        self.search_api = f"{self.base_url}/user/search.html"

    def search(self, keyword):
        try:
            params = {'q': keyword}
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': self.base_url
            }
            
            # 笔趣阁直接返回 JSON，速度极快
            resp = requests.get(self.search_api, params=params, headers=headers, timeout=8, verify=False)
            data = resp.json()
            
            results = []
            if isinstance(data, list):
                for item in data:
                    title = item.get('articlename')
                    href = item.get('url_list')
                    author = item.get('author')
                    
                    if title and href:
                        # 移动端链接转 PC 端
                        href = href.replace('https://m.', 'https://www.')
                        results.append({
                            'title': title.strip(),
                            'url': href,
                            'source': self.source_name,
                            'description': f"作者: {author}"
                        })
                    if len(results) >= 5: break
            return results
        except Exception as e:
            print(f"[Plugin] {self.source_name} Error: {e}")
            return []