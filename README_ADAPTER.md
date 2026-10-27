
# 🛠️ Smart NoteDB 适配器插件开发手册

## 番茄通道配置

在 `config.env` 或进程环境中设置 `FANQIE_ADAPTER=legacy`（默认，旧本地服务）或 `FANQIE_ADAPTER=app`（新 App 通道）。切换后重启 noteDB 和爬虫进程；集群各节点需一致配置。App 通道使用 `FQ_SIGN_URL`（默认 `http://127.0.0.1:8090`）和可选的 `FQ_SIGN_TOKEN`。仅使用可信的本机或安全内网服务；跨不可信网络须使用 HTTPS 和鉴权，令牌不要提交到仓库。旧通道仍使用 `FANQIE_API_HOST`、`FANQIE_API_TOKEN`。新通道需要安装 requirements.txt 中的 pycryptodome。

## 1. 概述
Smart NoteDB 采用 **Adapter Pattern (适配器模式)** 处理不同小说网站的差异。
*   **通用爬虫逻辑**：负责处理 90% 的标准笔趣阁模板站点。
*   **适配器插件**：专门处理结构奇葩、有特殊干扰符（如 `.la`、`www.xxx.com` 广告词）、或者翻页逻辑诡异的“硬骨头”网站。

## 2. 插件机制
主程序会自动扫描 `/adapters` 目录下的所有 `.py` 文件。
*   **识别条件**：类名中包含 `Adapter` 关键字。
*   **加载方式**：动态加载，修改插件代码后重启服务器即可生效，**无需改动主程序逻辑**。

## 3. 插件标准模板
在 `/adapters` 目录下新建 `xxx_adapter.py`，复制以下结构：

```python
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

class XxxSiteAdapter:
    """
    适配器类：请确保类名包含 'Adapter' 关键字
    """

    def can_handle(self, url):
        """
        [必须实现] 决定哪些 URL 由该插件接管
        """
        return "xxxsite.com" in url

    def get_toc(self, crawler, toc_url):
        """
        [必须实现] 专用的目录解析逻辑
        :param crawler: 传入的 NovelCrawler 实例，用于复用请求方法
        :param toc_url: 目录页地址
        """
        html = crawler._fetch_page_smart(toc_url)
        if not html: return None
        soup = BeautifulSoup(html, 'html.parser')
        
        title = "解析到的书名"
        chapters = []
        # 手写解析逻辑：例如 soup.select('.list a')
        # 每项格式：{'title': '章节名', 'url': '完整链接'}
        return {'title': title, 'chapters': chapters}

    def run(self, crawler, url):
        """
        [必须实现] 专用的正文解析与分页缝合逻辑
        :param crawler: 传入的 NovelCrawler 实例
        :param url: 章节起始地址
        """
        combined_content = []
        meta = {} # 存储 title, next, prev, toc_url
        
        # 推荐逻辑：
        # 1. 执行 while 循环或 for 循环递归抓取分页 (Page 1, 2, 3...)
        # 2. 调用 crawler._extract_content_smart(soup) 获取正文
        # 3. 在插件内进行针对性的垃圾字符清理 (如 re.sub)
        # 4. 识别真正的‘下一章’链接并返回
        
        return {
            'title': meta.get('title'),
            'content': combined_content, # 缝合后的 List
            'prev': meta.get('prev'),
            'next': meta.get('next'),
            'toc_url': meta.get('toc_url')
        }
```

## 4. 开发核心技巧

### 4.1 复用主程序能力
编写插件时，请务必利用 `crawler` 提供的内置工具，以保证功能一致性：
*   `crawler._fetch_page_smart(url)`：**必须使用**。它内置了你配置的**系统代理**、**浏览器指纹伪装**和**智能编码识别**。
*   `crawler._clean_text_lines(list)`：通用清洗。可以过滤掉“上一章、下一章”等基础噪音。
*   `crawler._get_smart_title(soup)`：通用标题识别。如果该站标题不特殊，可以直接调用。

### 4.2 处理分页缝合的“套路”
针对一章分多页的站点，在 `run` 方法中使用以下逻辑：
1.  **归一化**：如果传入的 URL 是 `_2.html`，先正则替换成 `.html` (第一页)。
2.  **获取本章 ID**：从 URL 提取数字，判断下一页按钮的链接是否还包含这个数字。如果包含，说明是分页；如果不包含，说明是真正的下一章。
3.  **标题锁定**：记录第一页的标题。如果后续页面的标题变了，立即停止缝合。

### 4.3 强力清洗“脏数据”
对于站点特有的干扰（如 `xbqg77` 的 `.la`）：
```python
lines = crawler._clean_text_lines(raw_text)
# 针对性处理
clean_lines = [line.replace('.la', '').strip() for line in lines]
```

## 5. 调试建议
1.  **控制台日志**：在插件的关键位置写 `print(f"[Adapter] xxx")`，启动 Flask 后可以在终端直接查看到插件的运行状态。
2.  **独立测试**：可以先写一个简单的测试脚本，手动实例化你的 Adapter 并传入一个模拟的 crawler 对象进行解析测试。
