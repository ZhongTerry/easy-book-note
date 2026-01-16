import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

class SxgreadAdapter:
    """
    书香阁 (sxgread.com) 适配器
    特点：
    1. 导航链接隐藏在 JS 变量中
    2. 目录页源码是乱序的，必须根据 data-id 属性进行排序
    """
    def can_handle(self, url):
        return "sxgread.com" in url

    def get_book_name(self, soup):
        # 优先从 meta property="og:novel:book_name" 获取，这是最准的
        meta_name = soup.find('meta', property='og:novel:book_name')
        if meta_name:
            return meta_name.get('content', '').strip()
            
        # 其次尝试面包屑
        path = soup.find('div', class_='pagepath')
        if path:
            links = path.find_all('a')
            if len(links) >= 3:
                return links[2].get_text(strip=True).replace('最新章节列表', '')
                
        return None

    def get_toc(self, crawler, toc_url):
        html = crawler._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 获取书名
        title = self.get_book_name(soup)
        if not title:
            h1 = soup.find('h1')
            title = h1.get_text(strip=True) if h1 else "未知书籍"

        # 2. [核心修复] 解析乱序目录
        # 网站源码中章节是乱的，依靠 <li data-id="xxx"> 中的 data-id 排序
        chapter_list = []
        
        # 找到 id="newlist" 的 ul
        ul = soup.find('ul', id='newlist')
        if ul:
            lis = ul.find_all('li')
            for li in lis:
                a = li.find('a')
                if not a: continue
                
                # 提取链接
                href = a.get('href')
                if not href: continue
                full_url = urljoin(toc_url, href)
                
                # 提取标题
                raw_title = a.get_text(strip=True)
                
                # [关键] 提取 data-id
                data_id_str = li.get('data-id')
                
                # 过滤掉无效数据 (如 data-id="999999" 的隐藏项)
                if not data_id_str or not data_id_str.isdigit():
                    continue
                
                chap_id = int(data_id_str)
                if chap_id >= 999999: continue # 排除那个隐藏的空li
                
                # 清洗标题，去除 "第xxx章" 前缀，只保留名字（可选）
                # 这里为了稳妥，保留原标题，name 字段做简单清洗
                pure_name = re.sub(r'^(?:第)?\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回]', '', raw_title).strip()

                chapter_list.append({
                    'id': chap_id,        # 既然网站给了明确ID，直接用，不要自己解析了
                    'title': raw_title,   # 完整标题：第1章 xxxx
                    'name': pure_name,    # 纯净标题：xxxx
                    'url': full_url,
                    'raw_title': raw_title
                })

            # 3. 按照 data-id 进行升序排列
            chapter_list.sort(key=lambda x: x['id'])
            
            print(f"[SxgreadAdapter] 成功通过 data-id 重排 {len(chapter_list)} 个章节")
            return {'title': title, 'chapters': chapter_list}
            
        else:
            print("[SxgreadAdapter] 未找到 #newlist，尝试通用解析")
            # 如果改版了找不到 newlist，回退到通用逻辑
            return crawler._general_toc_logic(toc_url)
    # === [新增] 获取元数据函数 ===
    def get_meta(self, crawler, url):
        """
        从书香阁目录页提取封面、作者、简介和标签
        """
        # 1. 请求页面
        html = crawler._fetch_page_smart(url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        print(url)
        # 2. 提取数据
        meta = {}

        # --- A. 封面 (Cover) ---
        # 优先从 meta og:image 获取
        og_img = soup.find('meta', property='og:image')
        if og_img:
            meta['cover'] = urljoin(url, og_img.get('content', ''))
        else:
            # 兜底：从 .bookimg img 获取
            img_div = soup.find('div', class_='bookimg')
            if img_div and img_div.find('img'):
                meta['cover'] = urljoin(url, img_div.find('img').get('src', ''))

        # --- B. 作者 (Author) ---
        # 优先从 meta og:novel:author 获取
        og_author = soup.find('meta', property='og:novel:author')
        if og_author:
            meta['author'] = og_author.get('content', '')
        else:
            # 兜底：从 .author 获取 (格式：作者：十月廿二)
            author_div = soup.find('div', class_='author')
            if author_div:
                # 提取第一个 <p>
                p_tag = author_div.find('p')
                if p_tag:
                    meta['author'] = p_tag.get_text(strip=True).replace('作者：', '').replace('作者:', '')

        # --- C. 简介 (Description) ---
        # 注意：og:description 往往是被截断的，我们优先用页面里的 .intro div
        intro_div = soup.find('div', class_='intro')
        if intro_div:
            # 书香阁简介里有很多 <br>，get_text 会把它们连在一起，最好先换成换行符
            # 但简单起见，get_text('\n') 通常足够
            # 另外要清理掉里面的 FONT 标签干扰
            meta['desc'] = intro_div.get_text('\n', strip=True)
        else:
            og_desc = soup.find('meta', property='og:description')
            if og_desc:
                meta['desc'] = og_desc.get('content', '')

        # --- D. 书名 (Book Name) - 可选，用于校验 ---
        og_name = soup.find('meta', property='og:novel:book_name')
        if og_name:
            meta['book_name'] = og_name.get('content', '')

        # --- E. 标签 (Tags) ---
        # 书香阁的分类在 og:novel:category
        meta['tags'] = []
        og_cat = soup.find('meta', property='og:novel:category')
        if og_cat:
            meta['tags'].append(og_cat.get('content', ''))
        
        # 完结状态
        status_div = soup.find('div', class_='author')
        if status_div and "已完结" in status_div.get_text():
            meta['tags'].append("完结")
        elif status_div and "连载" in status_div.get_text():
            meta['tags'].append("连载")

        return meta
    def run(self, crawler, url):
        html = crawler._fetch_page_smart(url)
        if not html: return None
        
        soup = BeautifulSoup(html, 'html.parser')
        meta = {}

        # 1. 标题与书名
        h1 = soup.find('div', class_='Noveltitle')
        meta['title'] = h1.get_text(strip=True) if h1 else crawler._get_smart_title(soup)
        meta['book_name'] = self.get_book_name(soup)

        # 2. 正文提取
        content_div = soup.find('div', class_='NovelTxt')
        if content_div:
            for junk in content_div.find_all(['script', 'style', 'div']): 
                junk.decompose()
            text = content_div.get_text('\n')
            meta['content'] = crawler._clean_text_lines(text)
        else:
            meta['content'] = ["正文提取失败"]

        # 3. JS 导航提取
        # var prevpage="/book/1/738/4083161.html";
        prev_match = re.search(r'var\s+prevpage\s*=\s*["\']([^"\']+)["\']', html)
        if prev_match and "index.html" not in prev_match.group(1):
            meta['prev'] = urljoin(url, prev_match.group(1))

        next_match = re.search(r'var\s+nextpage\s*=\s*["\']([^"\']+)["\']', html)
        if next_match:
            link = next_match.group(1)
            # 只有当下一页不是 index.html 时才算有下一章
            if link and "index.html" not in link:
                meta['next'] = urljoin(url, link)

        toc_match = re.search(r'var\s+bookpage\s*=\s*["\']([^"\']+)["\']', html)
        if toc_match:
            meta['toc_url'] = urljoin(url, toc_match.group(1))

        return meta