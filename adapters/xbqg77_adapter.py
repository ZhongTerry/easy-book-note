import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

class Xbqg77Adapter:
    """
    新笔趣阁 (xbqg77.com) 专属适配器
    """
    def can_handle(self, url):
        # 只要 URL 包含 xbqg77.com，就由该插件接管
        return "xbqg77.com" in url

    def get_toc(self, crawler, toc_url):
        """解析目录逻辑"""
        html = crawler._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        
        # 提取书名
        title = "未知书籍"
        h1 = soup.find('h1')
        if h1: title = h1.get_text(strip=True)

        # 提取章节：该站通常在 .dir a 
        chapters = []
        # 尝试寻找目录容器
        dir_div = soup.find('div', class_='dir') or soup.find('div', id='list')
        if dir_div:
            for a in dir_div.find_all('a'):
                t = a.get_text(strip=True)
                href = a.get('href')
                if href and t:
                    chapters.append({'title': t, 'url': urljoin(toc_url, href)})
        
        return {'title': title, 'chapters': chapters}

    def run(self, crawler, url):
        """解析正文逻辑（含分页缝合）"""
        combined_content = []
        current_url = url
        visited = {url}
        meta = {}
        
        # 识别书ID和章ID，用于判断分页
        # URL 格式: https://www.xbqg77.com/52449/1
        path_parts = url.rstrip('/').split('/')
        book_id = path_parts[-2] if len(path_parts) >= 2 else ""

        for i in range(5): # 最多缝合5页
            html = crawler._fetch_page_smart(current_url)
            if not html: break
            soup = BeautifulSoup(html, 'html.parser')

            # 1. 标题识别：该站 h2 是最纯净的
            page_title = ""
            h2 = soup.find('h2')
            if h2: page_title = h2.get_text(strip=True)
            
            if i == 0:
                meta['title'] = page_title or "加载失败"
            
            # 2. 正文提取：该站使用 article 标签
            article = soup.find('article', id='article') or soup.find('article')
            if article:
                # 移除 article 内部的广告和干扰
                for junk in article.select('script, .ad, .desc'): junk.decompose()
                
                # 提取行并清洗
                raw_text = article.get_text('\n')
                lines = crawler._clean_text_lines(raw_text)
                
                # [专项清洗] 移除该站特有的 .la 干扰符
                clean_lines = [re.sub(r'\.la', '', line).strip() for line in lines if line.strip()]
                combined_content.extend(clean_lines)

            # 3. 寻找导航
            next_page = None
            nav_links = soup.select('.dir a') # 该站翻页在 .dir 容器里
            for a in nav_links:
                txt = a.get_text(strip=True)
                href = a.get('href')
                full_url = urljoin(current_url, href)
                
                if "下一页" in txt or "下一章" in txt:
                    # 如果链接里包含 book_id 且 URL 变长了（如 /1_2），视为分页
                    if book_id in href and ("_" in href or "page" in href):
                        next_page = full_url
                    else:
                        meta['next'] = full_url
                elif "上一章" in txt and i == 0:
                    meta['prev'] = full_url
                elif "目录" in txt and i == 0:
                    meta['toc_url'] = full_url

            if next_page and next_page not in visited:
                current_url = next_page
                visited.add(next_page)
            else:
                break

        meta['content'] = combined_content
        return meta