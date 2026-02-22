import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup


class LuoxiawuAdapter:
    """
    落霞读书 (luoxiawu.com) 适配器
    关键点：目录是分页的，必须合并多页章节才能支持“全书爬取”。
    """

    def can_handle(self, url):
        return "luoxiawu.com" in url

    def _extract_book_id(self, url):
        patterns = [
            r'/book/(\d+)\.html$',
            r'/book/(\d+)/\d+\.html$',
            r'/book_(\d+)/\d+(?:_\d+)?\.html$'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _build_toc_url(self, url):
        book_id = self._extract_book_id(url)
        if not book_id:
            return None
        return f"https://www.luoxiawu.com/book/{book_id}.html"

    def _parse_toc_page(self, toc_page_url):
        """
        解析单个目录页，返回章节列表（保持原顺序）
        """
        html = self._crawler._fetch_page_smart(toc_page_url)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        chapters = []

        # 章节链接形态：https://www.luoxiawu.com/book_135650/1530093.html
        for a in soup.find_all('a'):
            href = (a.get('href') or '').strip()
            if not href:
                continue

            full_url = urljoin(toc_page_url, href)
            if not re.search(r'/book_\d+/\d+\.html$', full_url):
                continue

            title = a.get_text(strip=True)
            if not title:
                continue

            chapters.append({
                'title': title,
                'name': title,
                'raw_title': title,
                'url': full_url
            })

        return chapters

    def _discover_toc_pages(self, first_toc_url):
        """
        发现目录分页：
        - 首页: /book/135650.html
        - 分页: /book/135650/2.html
        """
        page_urls = []
        visited = set()
        book_id = self._extract_book_id(first_toc_url)

        current_url = first_toc_url
        max_pages = 60

        while current_url and current_url not in visited and len(page_urls) < max_pages:
            visited.add(current_url)
            page_urls.append(current_url)

            html = self._crawler._fetch_page_smart(current_url)
            if not html:
                break

            soup = BeautifulSoup(html, 'html.parser')
            next_url = None

            for a in soup.find_all('a'):
                text = a.get_text(strip=True)
                href = (a.get('href') or '').strip()
                if not href or href.startswith('javascript'):
                    continue

                candidate = urljoin(current_url, href)
                if not book_id:
                    continue

                if not re.search(rf'/book/{book_id}/\d+\.html$', candidate):
                    continue

                if '下一页' in text:
                    next_url = candidate
                    break

            current_url = next_url

        return page_urls

    def get_toc(self, crawler, toc_url):
        self._crawler = crawler

        normalized_toc_url = self._build_toc_url(toc_url) or toc_url
        html = crawler._fetch_page_smart(normalized_toc_url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')

        title = crawler._get_smart_title(soup) or "未知书籍"

        page_urls = self._discover_toc_pages(normalized_toc_url)

        chapters = []
        seen = set()
        for page_url in page_urls:
            sub_chapters = self._parse_toc_page(page_url)
            for chapter in sub_chapters:
                chapter_url = chapter['url']
                if chapter_url in seen:
                    continue
                seen.add(chapter_url)
                chapters.append(chapter)

        return {
            'title': title,
            'chapters': chapters,
            'page_type': 'toc'
        }

    def run(self, crawler, url):
        # 正文解析复用通用逻辑（已支持章节分页缝合）
        data = crawler._general_run_logic(url)
        if not data:
            return None

        # 对该站补一个稳定目录链接，供“从章节启动全书下载”时定位目录
        if not data.get('toc_url'):
            toc_url = self._build_toc_url(url)
            if toc_url:
                data['toc_url'] = toc_url

        data['page_type'] = 'chapter'
        return data
