import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

class SourceWorker:
    def __init__(self):
        self.source_name = "必去小说 📚"
        self.base_url = "http://www.ibiquw.info"
        self.search_url = f"{self.base_url}/modules/article/search.php"

    def search(self, keyword):
        try:
            # 注意：参数里有个 action=login，虽然奇怪但加上保险
            params = {'searchkey': keyword, 'action': 'login'}
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            
            resp = requests.get(self.search_url, params=params, headers=headers, timeout=10, verify=False)
            resp.encoding = 'utf-8' # 根据meta标签推断
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            
            # 解析列表: .toplist ul li
            items = soup.select('.toplist ul li')
            
            for item in items:
                # 结构: p.s1 a (书名), p.s3 (作者)
                title_tag = item.select_one('.s1 a')
                if not title_tag: continue
                
                title = title_tag.get_text(strip=True)
                href = title_tag.get('href')
                
                author_tag = item.select_one('.s3')
                author = author_tag.get_text(strip=True) if author_tag else ""
                
                if href:
                    if not href.startswith('http'):
                        href = urljoin(self.base_url, href)
                    
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