import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

class SxgreadAdapter:
    """
    书香阁 (sxgread.com) 适配器
    核心难点：导航链接隐藏在 JS 变量中
    """
    
    def can_handle(self, url):
        return "sxgread.com" in url

    def get_toc(self, crawler, toc_url):
        # 目录页通常是标准的，复用通用逻辑即可
        html = crawler._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        
        # 尝试提取书名
        title = "未知书籍"
        # 书香阁目录页标题通常在 .Info h1 或 .book-info h1
        h1 = soup.find('h1')
        if h1: title = h1.get_text(strip=True)
        
        # 利用爬虫自带的通用目录解析器
        chapters = crawler._parse_chapters_from_soup(soup, toc_url)
        
        return {'title': title, 'chapters': chapters}

    def run(self, crawler, url):
        html = crawler._fetch_page_smart(url)
        if not html: return None
        
        soup = BeautifulSoup(html, 'html.parser')
        meta = {}

        # 1. 标题提取
        # 源码：<div class="Noveltitle"><h1>第1728章 ...</h1></div>
        h1 = soup.find('div', class_='Noveltitle')
        if h1:
            meta['title'] = h1.get_text(strip=True)
        else:
            meta['title'] = crawler._get_smart_title(soup)

        # 2. 正文提取
        # 源码：<div class="NovelTxt" pageid="...">...</div>
        content_div = soup.find('div', class_='NovelTxt')
        if content_div:
            # 移除广告脚本
            for script in content_div.find_all('script'): script.decompose()
            text = content_div.get_text('\n')
            meta['content'] = crawler._clean_text_lines(text)
        else:
            meta['content'] = ["正文提取失败"]

        # 3. [核心] 导航提取 (Regex 解析 JS)
        # 寻找 var prevpage="..." 这种模式
        
        # 提取上一页
        prev_match = re.search(r'var\s+prevpage\s*=\s*["\'](.*?)["\'];', html)
        if prev_match:
            link = prev_match.group(1)
            # 如果链接包含 index.html，说明是第一章，没有上一章了
            if "index.html" not in link:
                meta['prev'] = urljoin(url, link)

        # 提取下一页
        next_match = re.search(r'var\s+nextpage\s*=\s*["\'](.*?)["\'];', html)
        if next_match:
            link = next_match.group(1)
            # 如果链接包含 index.html，说明是最后一章
            if "index.html" not in link:
                meta['next'] = urljoin(url, link)

        # 提取目录
        toc_match = re.search(r'var\s+bookpage\s*=\s*["\'](.*?)["\'];', html)
        if toc_match:
            meta['toc_url'] = urljoin(url, toc_match.group(1))

        return meta