import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

class SourceWorker:
    def __init__(self):
        self.source_name = "笔尖中文 📚"
        self.base_url = "http://www.xbiquzw.net"
        self.search_url = f"{self.base_url}/modules/article/search.php"

    def search(self, keyword):
        try:
            # 笔尖中文通常接受 UTF-8
            params = {'searchkey': keyword}
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': self.base_url
            }
            
            resp = requests.get(self.search_url, params=params, headers=headers, timeout=10, verify=False)
            resp.encoding = 'utf-8'
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            
            # 解析表格: table.grid tr
            rows = soup.select('table.grid tr')
            
            for row in rows:
                # 跳过表头 (含有 th 的行)
                if row.find('th'): continue
                
                cols = row.find_all('td')
                if len(cols) < 3: continue
                
                # 第1列是书名
                title_tag = cols[0].find('a')
                if not title_tag: continue
                
                title = title_tag.get_text(strip=True)
                href = title_tag.get('href')
                
                # 第3列是作者
                author = cols[2].get_text(strip=True)
                
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
            # print(f"[Plugin] xbiquzw Error: {e}")
            return []