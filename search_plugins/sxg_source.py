import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

class SourceWorker:
    def __init__(self):
        self.source_name = "书香阁 📚"
        self.base_url = "https://www.sxgread.com"

    def search(self, keyword):
        try:
            # 书香阁特殊处理：GBK 编码参数
            encoded_kw = quote(keyword.encode('gb2312'))
            search_url = f"{self.base_url}/s/?sword={encoded_kw}"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': self.base_url
            }
            
            resp = requests.get(search_url, headers=headers, timeout=10, verify=False)
            resp.encoding = 'gb18030' # 解决乱码
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            
            # 解析逻辑
            items = soup.select('.slist ul li')
            for item in items:
                if "小说名称" in item.get_text(): continue # 跳过表头
                
                try:
                    link_tag = item.select_one('.sname a')
                    if not link_tag: continue
                    
                    title = link_tag.get_text(strip=True)
                    href = link_tag.get('href')
                    
                    if href and not href.startswith('http'):
                        href = urljoin(self.base_url, href)
                        
                    author_tag = item.select_one('.sauthor')
                    author = author_tag.get_text(strip=True) if author_tag else ""
                    
                    if href:
                        results.append({
                            'title': title,
                            'url': href,
                            'source': self.source_name,
                            'description': f"作者: {author}"
                        })
                except: continue
                if len(results) >= 5: break
            
            return results
        except Exception as e:
            print(f"[Plugin] {self.source_name} Error: {e}")
            return []