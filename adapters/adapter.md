# 🛠️ Smart NoteDB 适配器开发指南

本文档旨在指导开发者为 **Smart NoteDB** 编写特定站点的爬虫适配器 (Adapter)。

## 1. 适配器机制简介

Smart NoteDB 采用 **“通用 + 插件”** 的混合爬虫模式：
1.  **通用逻辑**：`spider_core.py` 中的 `NovelCrawler` 处理绝大多数标准结构的网站。
2.  **适配器插件**：位于 `adapters/` 目录下。针对反爬严重、结构特殊或分页逻辑复杂的网站，系统会优先匹配适配器。

**加载机制**：系统启动时，`AdapterManager` 会自动扫描 `adapters/` 目录下的所有 `.py` 文件，加载其中类名包含 `Adapter` 的类。

---

## 2. 快速开始

在 `adapters/` 目录下新建一个 Python 文件，例如 `xxsite_adapter.py`。

### 标准模板

```python
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

class XxSiteAdapter:
    """
    Xx小说网适配器
    类名必须包含 'Adapter' (区分大小写)
    """

    def can_handle(self, url):
        """
        [必需] 判断当前 URL 是否由本适配器处理
        """
        return "xxsite.com" in url

    def get_toc(self, crawler, toc_url):
        """
        [必需] 解析目录页
        :param crawler: 传入的主爬虫实例 (用于发送请求)
        :param toc_url: 目录页 URL
        :return: 字典 {'title': 书名, 'chapters': [{'name': 章节名, 'url': 链接}, ...]}
        """
        html = crawler._fetch_page_smart(toc_url)
        if not html: return None
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 获取书名
        book_title = soup.select_one('h1').get_text(strip=True)
        
        # 2. 获取章节列表
        chapters = []
        for link in soup.select('.chapter-list a'):
            chapters.append({
                'name': link.get_text(strip=True),
                'url': urljoin(toc_url, link['href'])
            })
            
        return {
            'title': book_title,
            'chapters': chapters
        }

    def run(self, crawler, url):
        """
        [必需] 解析正文页 (包含自动翻页/缝合逻辑)
        :param crawler: 传入的主爬虫实例
        :param url: 起始章节 URL
        :return: 字典 (见下文详细结构)
        """
        # 使用 crawler 发送请求，自动处理 headers 和代理
        html = crawler._fetch_page_smart(url)
        if not html: return None
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # 提取标题
        title = soup.select_one('h1').get_text(strip=True)
        
        # 提取正文 (清洗并转为列表)
        content_div = soup.select_one('#content')
        # 利用 crawler 的内置工具清洗垃圾文本
        content_lines = crawler._clean_text_lines(content_div.get_text('\n'))
        
        # 获取上一章/下一章/目录链接
        prev_url = soup.find('a', text='上一章')['href']
        next_url = soup.find('a', text='下一章')['href']
        toc_url = soup.find('a', text='目录')['href']

        return {
            'title': title,
            'content': content_lines, # 必须是字符串列表 List[str]
            'book_name': '未知书名',   # 可选，如果有能提取更好
            'prev': urljoin(url, prev_url),
            'next': urljoin(url, next_url),
            'toc_url': urljoin(url, toc_url)
        }
```

---

## 3. 核心 API 详解

编写适配器时，**不要**自己使用 `requests` 库，请务必调用传入的 `crawler` 实例的方法，以确保指纹伪装（curl_cffi）和代理设置生效。

### 3.1 `crawler._fetch_page_smart(url)`
*   **功能**：智能发送 GET 请求。
*   **特性**：自动处理重试、超时、以及常见中文编码（GBK/UTF-8）的自动识别。
*   **返回**：HTML 字符串（解码后）或 `None`。

### 3.2 `crawler._clean_text_lines(text)`
*   **功能**：清洗正文文本。
*   **特性**：自动去除广告词（如“一秒记住”、“加入书签”）、多余空行。
*   **输入**：包含换行符的长字符串。
*   **返回**：干净的字符串列表 `List[str]`。

### 3.3 `crawler._get_smart_title(soup)`
*   **功能**：尝试从 BeautifulSoup 对象中智能提取章节标题。

---

## 4. 高级技巧：处理章节内分页

很多网站为了骗点击，将一章拆分为 `1.html`, `1_2.html`。适配器需要负责将它们“缝合”起来。

**推荐的 `run` 方法逻辑：**

```python
    def run(self, crawler, url):
        combined_content = []
        current_url = url
        first_title = ""
        meta_info = {} # 存 next, prev 等
        
        page_count = 0
        while page_count < 10: # 防止死循环，最多拼10页
            html = crawler._fetch_page_smart(current_url)
            if not html: break
            soup = BeautifulSoup(html, 'html.parser')
            
            # 1. 记录第一页的元数据
            if page_count == 0:
                first_title = soup.select_one('h1').get_text(strip=True)
                # 提取 prev, toc ...
            
            # 2. 提取正文并追加
            lines = crawler._clean_text_lines(soup.select_one('#content').get_text('\n'))
            combined_content.extend(lines)
            
            # 3. 寻找“下一页”链接
            # 注意：需区分“下一页”和“下一章”
            next_btn = soup.find('a', string=re.compile('下一页'))
            if next_btn and '下一章' not in next_btn.get_text():
                current_url = urljoin(current_url, next_btn['href'])
                page_count += 1
            else:
                # 是下一章了，记录链接并跳出
                if next_btn:
                    meta_info['next'] = urljoin(current_url, next_btn['href'])
                break
        
        return {
            'title': first_title,
            'content': combined_content,
            'next': meta_info.get('next'),
            # ... 其他字段
        }
```

## 5. 调试建议

在开发过程中，可以在代码中插入 `print` 语句。运行后端服务时，控制台会输出这些日志。

```python
print(f"[MyAdapter] 正在解析: {url}")
```

如果遇到 `403 Forbidden` 或 Cloudflare 拦截，请检查是否在 `crawler._fetch_page_smart` 调用前需要设置特定的 Headers，或者该站点是否必须使用 Selenium（目前架构主要支持 curl_cffi）。