import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

class SourceWorker:
    def __init__(self):
        self.source_name = "笔趣阁365 📚"
        self.base_url = "https://www.biquge365.net"
        self.search_url = f"{self.base_url}/s.php"

    def search(self, keyword):
        try:
            data = {'type': 'articlename', 's': keyword, 'submit': ''}
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Origin': self.base_url,
                'Referer': self.base_url
            }
            
            resp = requests.post(self.search_url, data=data, headers=headers, timeout=10, verify=False)
            resp.encoding = 'utf-8'
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            
            items = soup.select('ul.search li')
            
            for item in items:
                if "书名" in item.get_text(): continue
                
                title_tag = item.select_one('.name a')
                if not title_tag: continue
                
                title = title_tag.get_text(strip=True)
                href = title_tag.get('href')
                
                author_tag = item.select_one('.zuo')
                author = author_tag.get_text(strip=True) if author_tag else ""
                
                if href:
                    if not href.startswith('http'):
                        href = urljoin(self.base_url, href)
                    
                    # [核心修复] 强制修正目录 URL：/book/ -> /newbook/
                    # 这样爬虫就能直接拿到目录，而不是卡在详情页
                    if "/book/" in href:
                        href = href.replace("/book/", "/newbook/")
                    
                    results.append({
                        'title': title,
                        'url': href,
                        'source': self.source_name,
                        'description': f"作者: {author}"
                    })
                if len(results) >= 5: break
            
            return results
        except Exception as e:
            return []