import re
import json
import os
import time
import logging
import traceback
from urllib.parse import urljoin
from shared import USER_DATA_DIR, BASE_DIR

# ================= 配置日志 =================
# 自动创建 debug 文件夹
DEBUG_DIR = os.path.join(BASE_DIR, "debug")
if not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

LOG_FILE = os.path.join(DEBUG_DIR, "fanqie.log")

# 配置专属 logger
logger = logging.getLogger("FanqieDebug")
logger.setLevel(logging.DEBUG)
# 避免重复添加 handler
if not logger.handlers:
    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
# ===========================================

class FanqieAdapter:
    """
    番茄小说 API 适配器 (带深度调试日志版)
    """

    def can_handle(self, url):
        return "fanqienovel.com" in url

    def _log(self, msg, level="info"):
        """写日志辅助函数"""
        if level == "info": logger.info(msg)
        elif level == "error": logger.error(msg)
        elif level == "debug": logger.debug(msg)
        # 同时打印到控制台方便查看
        print(f"[FanqieDebug] {msg}")

    def _load_cookies(self):
        cookie_path = os.path.join(USER_DATA_DIR, "fanqie_cookie.txt")
        if os.path.exists(cookie_path):
            try:
                with open(cookie_path, 'r', encoding='utf-8') as f:
                    c = f.read().strip()
                    if c:
                        self._log(f"成功加载 Cookie (长度: {len(c)})")
                        return c
                    else:
                        self._log("Cookie 文件存在但为空", "error")
            except Exception as e:
                self._log(f"读取 Cookie 文件出错: {e}", "error")
        else:
            self._log("未找到 Cookie 文件 (user_data/fanqie_cookie.txt)", "error")
        return None

    def _get_api_headers(self):
        headers = {
            "Referer": "https://fanqienovel.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://fanqienovel.com",
            # 这些头模拟真实浏览器行为，防止被轻易识别
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        cookie = self._load_cookies()
        if cookie:
            headers["Cookie"] = cookie
        else:
            self._log("警告: 请求将不带 Cookie 发送，极大概率失败", "error")
        return headers

    def get_toc(self, crawler, toc_url):
        self._log(f"=== 开始解析目录: {toc_url} ===")
        # ... (目录解析逻辑相对简单，为了节省篇幅，这里略过，重点在 run 方法) ...
        # 如果你也需要调试目录，请仿照 run 方法的写法加上日志
        # 这里暂时返回 None 让通用爬虫接管，或者你可以保留之前的目录解析代码
        self._log("目录解析暂未启用详细调试，建议直接测试章节阅读")
        return None

    def run(self, crawler, url):
        self._log(f"\n{'='*20} 新的抓取任务 {'='*20}")
        self._log(f"目标 URL: {url}")

        # 1. 提取 Item ID
        item_id = ""
        match = re.search(r'reader/(\d+)', url)
        if match:
            item_id = match.group(1)
        
        if not item_id:
            self._log("❌ URL 解析失败，无法提取 Item ID", "error")
            return None

        # 2. 构造 API
        api_url = f"https://fanqienovel.com/api/reader/full?itemId={item_id}"
        self._log(f"构造 API: {api_url}")

        meta = {
            'title': "",
            'content': [],
            'next': None,
            'prev': None,
            'toc_url': None
        }

        try:
            from curl_cffi import requests as cffi_requests
            
            headers = self._get_api_headers()
            self._log("正在发送请求...")
            
            # 发送请求
            resp = cffi_requests.get(
                api_url, 
                impersonate=crawler.impersonate, 
                timeout=crawler.timeout, 
                headers=headers,
                proxies=crawler.proxies
            )

            self._log(f"请求完成. HTTP状态码: {resp.status_code}")
            
            # === [关键调试信息] 保存响应内容 ===
            # 将原始响应写入日志，这是分析问题的金钥匙
            log_content = resp.text
            if len(log_content) > 2000:
                log_content = log_content[:2000] + "\n...[内容过长截断]..."
            self._log(f"响应内容预览:\n{log_content}")
            # ================================

            if resp.status_code != 200:
                self._log(f"❌ API 请求失败，非 200 状态码", "error")
                return self._return_error_msg("网络请求被拒绝 (HTTP != 200)")

            try:
                data = resp.json()
            except:
                self._log("❌ 响应不是合法的 JSON，可能是 HTML 报错页面或滑块", "error")
                return self._return_error_msg("响应格式错误 (非 JSON)")

            # 检查业务状态码
            # 番茄通常 code=0 表示成功
            code = data.get('code')
            if code != 0:
                msg = data.get('message') or data.get('msg') or "未知错误"
                self._log(f"❌ API 业务报错: code={code}, msg={msg}", "error")
                return self._return_error_msg(f"番茄拒绝访问: {msg} (Code: {code})")

            # 3. 解析数据
            chapter_data = data.get('data', {}).get('chapterData', {})
            meta['title'] = chapter_data.get('title', '未知章节')
            self._log(f"获取到标题: {meta['title']}")

            content_html = chapter_data.get('content', '')
            if content_html:
                self._log(f"获取到正文 HTML (长度: {len(content_html)})")
                # 清洗 HTML
                content_text = re.sub(r'<br\s*/?>', '\n', content_html)
                content_text = re.sub(r'<p>', '\n', content_text)
                content_text = re.sub(r'<.*?>', '', content_text)
                import html as html_parser
                content_text = html_parser.unescape(content_text)
                meta['content'] = crawler._clean_text_lines(content_text)
                self._log(f"正文清洗完成，共 {len(meta['content'])} 行")
            else:
                self._log("⚠️ 警告: chapterData.content 为空!", "error")

            # 导航
            next_id = chapter_data.get('nextItemId')
            if next_id and str(next_id) != "0":
                meta['next'] = f"https://fanqienovel.com/reader/{next_id}"

        except Exception as e:
            err_track = traceback.format_exc()
            self._log(f"❌ 发生严重异常:\n{err_track}", "error")
            return self._return_error_msg(f"插件内部错误: {str(e)}")

        # 最终检查
        if not meta['content']:
             return self._return_error_msg("内容解析为空 (可能是 VIP 限制)")

        return meta

    def _return_error_msg(self, reason):
        return {
            'title': "错误报告",
            'content': [
                "【番茄插件调试报告】",
                f"错误原因: {reason}",
                "----------------",
                "请查看服务器 debug/fanqie.log 获取详细 JSON 响应。",
                "如果是 'Auth Failed' 或 'Need Login'，请更新 Cookie。",
                "如果是 'Risk Control'，说明 IP 被风控。"
            ],
            'next': None, 'prev': None, 'toc_url': None
        }