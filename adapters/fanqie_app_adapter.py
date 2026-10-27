"""番茄小说 App 数据源；设置 FANQIE_ADAPTER=app 启用。"""
import os
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.env'))

from shared import debug, info, warn, error  # noqa: E402  noteDB 统一日志

from fq_api import FanqieBackend  # noqa: E402
from fq_sign_client import CircuitGuard, SignServiceError  # noqa: E402


class FanqieAppAdapter:
    """番茄小说（App 接口通道）适配器。

    与旧 FanqieLocalAdapter 的区别：不再依赖本地 9000 端口的 FastAPI 微服务，
    直接调用独立签名服务（8090）+ 官方 API，并在进程内完成正文解密。
    """

    def __init__(self):
        self.sign_url = os.environ.get("FQ_SIGN_URL", "http://127.0.0.1:8090").strip()
        self.sign_token = os.environ.get("FQ_SIGN_TOKEN", "").strip()
        self.backend = FanqieBackend(sign_url=self.sign_url, sign_token=self.sign_token)
        self.guard = CircuitGuard(threshold=3, cooldown=300)
        self._toc_cache = {}   # book_id -> (chapters, ts)

    # ---------- 基础识别 ----------
    def can_handle(self, url):
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower()
            return (os.environ.get('FANQIE_ADAPTER', 'legacy').strip().lower() == 'app'
                    and parsed.scheme in ('http', 'https')
                    and not parsed.username and not parsed.password
                    and any(host == domain or host.endswith('.' + domain)
                            for domain in ('fanqienovel.com', 'fqnovel.com')))
        except (ValueError, TypeError):
            return False

    def detect_url_type(self, url):
        if "/page/" in url:
            return "toc"
        if "/reader/" in url:
            return "chapter"
        return "unknown"

    @staticmethod
    def _get_item_id(url):
        m = re.search(r"reader/(\d+)", url)
        return m.group(1) if m else None

    @staticmethod
    def _get_book_id(url):
        m = re.search(r"page/(\d+)", url)
        return m.group(1) if m else None

    def _resolve_book_id_by_item(self, item_id):
        info_row = self.backend.resolve_book_id_by_item(item_id)
        if info_row and info_row.get("book_id"):
            return str(info_row["book_id"])
        return None

    # ---------- 元数据 ----------
    def get_meta(self, crawler, url):
        book_id = self._get_book_id(url)
        if not book_id:
            item_id = self._get_item_id(url)
            if item_id:
                book_id = self._resolve_book_id_by_item(item_id)
        if not book_id:
            return None
        if self.guard.is_open():
            warn("FanqieApp", "签名服务熔断中，跳过元数据请求")
            return None
        detail = self.backend.get_detail(book_id)
        self.guard.record(detail.get("code") == 0)
        if detail.get("code") != 0:
            warn("FanqieApp", f"详情失败: {detail.get('message')}")
            return None
        d = detail.get("data") or {}
        cover = d.get("thumb_url") or d.get("thumb_uri") or ""
        if cover and not cover.startswith("http"):
            cover = f"https://p3-novel.byteimg.com/origin/{cover}"

        tags = []
        score = d.get("score")
        if score and str(score) != "0.0":
            tags.append(f"{score}分")
        if d.get("category"):
            tags.append(str(d["category"]))
        for key in ("tags", "high_quality_tags"):
            raw = d.get(key) or ""
            if raw:
                tags.extend([t for t in str(raw).split(",") if t])
        dedup = list(dict.fromkeys(tags))

        return {
            "title": d.get("book_name") or "番茄小说",
            "author": d.get("author") or "未知作者",
            "cover": cover,
            "abstract": (d.get("abstract") or "暂无简介").replace("\n", "<br>"),
            "tags": ",".join(dedup),
            "word_number": d.get("word_number"),
            "status": d.get("creation_status"),
            "book_id": book_id,
        }

    # ---------- 目录 ----------
    def _fetch_toc_list(self, book_id):
        cached = self._toc_cache.get(str(book_id))
        if cached and time.time() - cached[1] < 300:
            return cached[0]
        cat = self.backend.get_catalog(book_id)  # 免签名接口，不走熔断
        if cat.get("code") != 0:
            error("FanqieApp", f"目录失败: {cat.get('message')}")
            return []
        chapters = []
        for idx, item in enumerate((cat.get("data") or {}).get("item_data_list") or [], 1):
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            title = item.get("title") or "无标题"
            num = idx
            m = re.search(r"第(\d+)章", title)
            if m:
                num = int(m.group(1))
            chapters.append({
                "id": num,
                "item_id": item_id,
                "title": title,
                "name": title,
                "url": f"https://fanqienovel.com/reader/{item_id}",
            })
        if chapters:
            self._toc_cache[str(book_id)] = (chapters, time.time())
        return chapters

    def get_toc(self, crawler, toc_url):
        book_id = self._get_book_id(toc_url)
        if not book_id:
            item_id = self._get_item_id(toc_url)
            if item_id:
                book_id = self._resolve_book_id_by_item(item_id)
        if not book_id:
            return None
        chapters = self._fetch_toc_list(book_id)
        if not chapters:
            return None
        info("FanqieApp", f"目录获取成功: book={book_id} 共 {len(chapters)} 章")

        book_title = "番茄小说"
        if not self.guard.is_open():
            detail = self.backend.get_detail(book_id)
            self.guard.record(detail.get("code") == 0)
            if detail.get("code") == 0:
                book_title = (detail.get("data") or {}).get("book_name") or book_title

        return {
            "title": book_title,
            "manual_sort": True,   # 禁止 spider_core 重排章节 id
            "chapters": chapters,
            "page_type": "toc",
        }

    # ---------- 正文 ----------
    def run(self, crawler, url):
        item_id = self._get_item_id(url)
        if not item_id:
            return None

        content_lines = None
        if not self.guard.is_open():
            try:
                result = self.backend.get_content(item_id)
            except Exception:
                self.guard.record(False)
                error("FanqieApp", "正文请求异常")
                return {"title": "", "content": [], "page_type": "chapter"}
            ok = result.get("code") == 0
            self.guard.record(ok)
            if ok:
                content_lines = [
                    line.strip() for line in result["data"]["content"].split("\n") if line.strip()
                ]
            else:
                error("FanqieApp", f"正文失败: {result.get('message')}")
        else:
            warn("FanqieApp", "签名服务熔断中，正文请求跳过")

        if not content_lines:
            return {"title": "", "content": [], "page_type": "chapter"}

        # 上下文（上一章/下一章/目录）——目录有 5 分钟缓存，基本无额外请求
        try:
            book_id = self._resolve_book_id_by_item(item_id)
        except Exception:
            book_id = None
        prev_url = next_url = toc_url = None
        chapter_title = ""
        book_name = "番茄小说"
        if book_id:
            toc_url = f"https://fanqienovel.com/page/{book_id}"
            chapters = self._fetch_toc_list(book_id)
            for i, ch in enumerate(chapters):
                if ch["item_id"] == item_id:
                    chapter_title = ch["title"]
                    if i > 0:
                        prev_url = chapters[i - 1]["url"]
                    if i < len(chapters) - 1:
                        next_url = chapters[i + 1]["url"]
                    break
            if not self.guard.is_open():
                detail = self.backend.get_detail(book_id)
                self.guard.record(detail.get("code") == 0)
                if detail.get("code") == 0:
                    book_name = (detail.get("data") or {}).get("book_name") or book_name

        return {
            "title": chapter_title,
            "content": content_lines,
            "book_name": book_name,
            "prev": prev_url,
            "next": next_url,
            "toc_url": toc_url,
            "page_type": "chapter",
        }
