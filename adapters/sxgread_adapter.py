import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

class SxgreadAdapter:
    """
    书香阁 (sxgread.com) 适配器
    特点：导航链接隐藏在 JS 变量中 (prevpage, nextpage)
    """
    def get_book_name(self, soup):
        print("[SxgreadAdapter] 🔎 尝试从面包屑提取书名...")
        path = soup.find('div', class_='pagepath')
        if path:
            links = path.find_all('a')
            if len(links) >= 3:
                name = links[2].get_text(strip=True)
                print(f"[SxgreadAdapter] ✅ 提取成功: {name}")
                return name
        print("[SxgreadAdapter] ❌ 提取失败")
        return None
    def can_handle(self, url):
        return "sxgread.com" in url

    def get_toc(self, crawler, toc_url):
        # 目录页解析复用通用逻辑，因为书香阁目录页是标准的 HTML
        html = crawler._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        
        # 尝试提取书名
        title = "未知书籍"
        h1 = soup.find('h1')
        if h1: title = h1.get_text(strip=True)
        
        # 利用爬虫自带的通用目录解析器 (它会自动处理排序和去重)
        chapters = crawler._parse_chapters_from_soup(soup, toc_url)
        
        return {'title': title, 'chapters': chapters}

    def run(self, crawler, url):
        html = crawler._fetch_page_smart(url)
        if not html: return None
        
        soup = BeautifulSoup(html, 'html.parser')
        meta = {}

        # 1. 标题提取
        # 优先找 h1，书香阁正文标题在 .Noveltitle h1
        h1 = soup.find('div', class_='Noveltitle')
        if h1:
            meta['title'] = h1.get_text(strip=True)
        else:
            meta['title'] = crawler._get_smart_title(soup)
        book_name = self.get_book_name(soup) or crawler._get_smart_title(soup)
        # 2. 正文提取
        # 书香阁正文在 .NovelTxt
        content_div = soup.find('div', class_='NovelTxt')
        if content_div:
            # 移除广告脚本和无用标签
            for junk in content_div.find_all(['script', 'style', 'div']): 
                junk.decompose()
            
            # 移除 <br> 标签 (get_text 会自动处理换行，但为了保险)
            text = content_div.get_text('\n')
            meta['content'] = crawler._clean_text_lines(text)
        else:
            meta['content'] = ["正文提取失败，请尝试刷新或更换源。"]
        meta["book_name"] = book_name
        # 3. [核心] 导航提取 (Regex 解析 JS)
        # 源码示例: var prevpage="/book/1/738/4083161.html";
        
        # 提取上一页
        prev_match = re.search(r'var\s+prevpage\s*=\s*["\']([^"\']+)["\']', html)
        if prev_match:
            link = prev_match.group(1)
            # 这里的 index.html 通常指目录，如果上一页是目录，说明这是第一章
            if "index.html" not in link:
                meta['prev'] = urljoin(url, link)

        # 提取下一页
        next_match = re.search(r'var\s+nextpage\s*=\s*["\']([^"\']+)["\']', html)
        if next_match:
            link = next_match.group(1)
            # 如果下一页是 index.html，说明是最后一章，或者是没有下一章了
            # 注意：书香阁有时候最后一章的 nextpage 是空的 ""
            if link and "index.html" not in link:
                meta['next'] = urljoin(url, link)

        # 提取目录
        toc_match = re.search(r'var\s+bookpage\s*=\s*["\']([^"\']+)["\']', html)
        if toc_match:
            meta['toc_url'] = urljoin(url, toc_match.group(1))

        return meta