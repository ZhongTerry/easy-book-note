import time
import re
from urllib.parse import urljoin
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from lxml import html as lxml_html

# === 配置 ===
# 这里填你那本只有200章的书的目录链接
TARGET_URL = "https://www.22biqu.com/biqu36065/" 

class DebugCrawler:
    def __init__(self):
        self.impersonate = "chrome110"
        self.timeout = 15

    def fetch(self, url):
        print(f"\n[1] 正在请求 URL: {url} ...")
        try:
            # 伪装成桌面版 Chrome
            response = cffi_requests.get(
                url, 
                impersonate=self.impersonate, 
                timeout=self.timeout,
                allow_redirects=True
            )
            content = response.content
            
            # 简单的编码检测
            charset = lxml_html.fromstring(content).xpath('//meta/@charset')
            encoding = charset[0] if charset else 'utf-8'
            try:
                html = content.decode(encoding, errors='replace')
            except:
                html = content.decode('utf-8', errors='replace')
            
            print(f"[2] 请求成功! 状态码: {response.status_code}, 长度: {len(html)}")
            return html
        except Exception as e:
            print(f"[ERROR] 请求失败: {e}")
            return None

    def analyze(self, url):
        html = self.fetch(url)
        if not html: return

        soup = BeautifulSoup(html, 'html.parser')

        # 1. 检查标题，确认是否被反爬（比如返回了验证码页面）
        title = soup.title.get_text(strip=True) if soup.title else "无标题"
        print(f"[3] 页面标题: {title}")

        # 2. 检查 Select 标签 (这是关键)
        print("-" * 30)
        print("[4] 开始寻找 <select> 分页...")
        selects = soup.find_all('select')
        
        if not selects:
            print("   >>> 警告: 未找到任何 <select> 标签！")
            print("   >>> 可能原因: 1. 网站识别出爬虫，返回了不同页面 2. 页面结构变了")
            # 尝试打印部分 HTML 看看
            print(f"   >>> 页面前 500 个字符: {html[:500]}")
        else:
            print(f"   >>> 找到了 {len(selects)} 个 <select> 标签")
            for i, select in enumerate(selects):
                print(f"   --- Select #{i+1} (ID: {select.get('id')}, Class: {select.get('class')}) ---")
                options = select.find_all('option')
                print(f"       包含 {len(options)} 个选项:")
                for opt in options:
                    txt = opt.get_text(strip=True)
                    val = opt.get('value')
                    print(f"       - 文本: '{txt}', Value: '{val}'")
                    
                    # 模拟 URL 拼接
                    if val:
                        full = urljoin(url, val)
                        print(f"         -> 拼接结果: {full}")

        # 3. 检查底部链接分页
        print("-" * 30)
        print("[5] 开始寻找底部链接分页 (class=pagination/page)...")
        paginations = soup.find_all(class_=re.compile(r'(pagination|page|index-container)'))
        if paginations:
            for p in paginations:
                print(f"   >>> 找到分页容器: {p.get('class')}")
                links = p.find_all('a')
                for a in links:
                    print(f"       - Link: {a.get_text(strip=True)} -> {a.get('href')}")
        else:
            print("   >>> 未找到明显的分页容器")

if __name__ == '__main__':
    debug = DebugCrawler()
    debug.analyze(TARGET_URL)