"""番茄 App 接口访问层（已实测通过）。

对外方法与旧微服务（127.0.0.1:9000）的返回结构保持一致，便于最小改动接入 noteDB 适配器：
    get_catalog(book_id) -> {"code":0, "data":{"item_data_list":[{"item_id","title"}], "all_item_ids":[...]}}
    get_detail(book_id)  -> {"code":0, "data":{book_name, author, abstract, thumb_url, tags, ...}}
    get_content(item_id) -> {"code":0, "data":{"content":"正文纯文本（\\n 分行）"}}

依赖 fq_sign_client.SignService（签名服务）与 fq_crypto（加解密）。
"""
import json
import _thread
import threading
import time

import requests

from fq_crypto import (clean_chapter_text, decrypt_content, make_registerkey_body,
                       parse_registerkey_key, to_lines)
from fq_sign_client import CircuitGuard, SignService, SignServiceError

API_HOST = "https://api5-normal-sinfonlineb.fqnovel.com"
DIRECTORY_HOST = "https://fanqienovel.com/api/reader/directory/detail"
REVERSE_HOST = "https://novel.snssdk.com/api/novel/book/directory/detail/v/"
SEARCH_HOST = "https://novel.snssdk.com/api/novel/channel/homepage/search/search/v1/"
WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 正文单批上限：实测 >30 章时超出部分不返回正文
BATCH_LIMIT = 30
KEY_TTL_SECONDS = 600     # 解密密钥缓存时间；失败/110 时自动刷新
CATALOG_TTL_SECONDS = 300


def _base_params():
    """App 公共参数（与签名服务配套的海外版参数集；iid/device_id/cdid 会被 /sign 改写）。"""
    return [
        ("iid", "1"), ("device_id", "2"), ("ac", "wifi"), ("channel", "googleplay"),
        ("aid", "1967"), ("app_name", "novelapp"), ("version_code", "68132"),
        ("version_name", "6.8.1.32"), ("device_platform", "android"), ("os", "android"),
        ("ssmix", "a"), ("device_type", "OnePlus11"), ("device_brand", "OnePlus"),
        ("language", "zh"), ("os_api", "32"), ("os_version", "12"),
        ("manifest_version_code", "68132"), ("resolution", "3200*1440"), ("dpi", "640"),
        ("update_version_code", "68132"), ("_rticket", str(int(time.time() * 1000))),
        ("host_abi", "arm64-v8a"), ("dragon_device_type", "phone"), ("pv_player", "68132"),
        ("compliance_status", "0"), ("need_personal_recommend", "1"), ("player_so_load", "1"),
        ("is_android_pad_screen", "0"), ("rom_version", "V291IR+release-keys"), ("cdid", "3"),
    ]


def _qs(pairs):
    return "?" + "&".join(f"{k}={v}" for k, v in pairs)


class FanqieBackend:
    """番茄 App 数据访问层。

    线程安全：签名调用由 SignService 串行化；本类只做缓存与重试编排（进程内锁）。
    """

    def __init__(self, sign_url="http://127.0.0.1:8090", sign_token="",
                 timeout=30, logger=None):
        self.sign = SignService(sign_url, timeout=timeout, token=sign_token)
        self.timeout = timeout
        self.guard = CircuitGuard()
        self._log = logger or (lambda level, msg: print(f"[{level}] [FanqieBackend] {msg}"))
        self._lock = _thread.allocate_lock()
        self._device_lock = threading.RLock()
        self._key_cache = {}      # device -> (key_hex, ts)
        self._catalog_cache = {}  # book_id -> (data, ts)

    # ---------- 内部工具 ----------
    def _info(self, msg): self._log("INFO", msg)
    def _warn(self, msg): self._log("WARN", msg)
    def _error(self, msg): self._log("ERROR", msg)

    def _signed_request(self, url, method="GET", body=None):
        """签名 + 发请求。返回 (status, text, env)。"""
        env = self.sign.sign(url)
        headers = {}
        headers.update(env.get("req_headers") or {})
        headers.update(env.get("headers") or {})
        headers["User-Agent"] = env.get("ua") or WEB_UA
        headers["Cookie"] = env.get("cookie") or ""
        headers["Accept"] = "application/json; charset=utf-8"
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = requests.request(method, env["url"], headers=headers, data=body,
                                timeout=self.timeout)
        return resp.status_code, resp.text, env

    def _fresh_key(self):
        """对当前设备现取 registerkey 密钥并缓存。返回 key_hex，失败返回空串。"""
        url = API_HOST + "/reading/crypt/registerkey" + _qs(_base_params())
        env = self.sign.sign(url)  # 一次签名即拿到设备与签名头
        device_id = (env.get("device_info") or {}).get("device_id")
        if not device_id:
            return ""
        headers = {}
        headers.update(env.get("req_headers") or {})
        headers.update(env.get("headers") or {})
        headers["User-Agent"] = env.get("ua") or WEB_UA
        headers["Cookie"] = env.get("cookie") or ""
        headers["Accept"] = "application/json; charset=utf-8"
        headers["Content-Type"] = "application/json"
        resp = requests.post(env["url"], headers=headers,
                             data=make_registerkey_body(device_id), timeout=self.timeout)
        if resp.status_code != 200:
            self._warn(f"registerkey HTTP {resp.status_code}")
            return ""
        try:
            payload = json.loads(resp.text)
        except ValueError:
            return ""
        if payload.get("code") != 0:
            self._warn(f"registerkey code={payload.get('code')} msg={payload.get('message')}")
            return ""
        key = parse_registerkey_key((payload.get("data") or {}).get("key"))
        if key:
            with self._lock:
                self._key_cache[env["device"]] = (key, time.time())
        return key

    def _get_key(self, force=False):
        device = self.sign.device or "?"
        with self._lock:
            cached = self._key_cache.get(device)
        if not force and cached and time.time() - cached[1] < KEY_TTL_SECONDS:
            return cached[0]
        return self._fresh_key()

    def _recover_device(self, why):
        """110/100/空响应 → 上报坏设备，换一台后重试一次。"""
        self._warn(f"触发换设备: {why}")
        self.sign.report_device()
        self.sign.reset_device()
        with self._lock:
            self._key_cache.clear()

    # ---------- 免签名接口 ----------
    def search(self, keyword, limit=10):
        """关键词搜索（免签名，返回 App 搜索结果列表）。"""
        url = f"{SEARCH_HOST}?q={requests.utils.quote(keyword)}&aid=1967"
        resp = requests.get(url, headers={"User-Agent": WEB_UA,
                                          "Accept": "application/json, text/plain, */*"},
                            timeout=self.timeout)
        data = resp.json()
        items = ((data.get("data") or {}).get("ret_data")) or []
        return items[:limit]

    def resolve_book_id_by_item(self, item_id):
        """章节 item_id → book_id + 书籍元数据（免签名）。返回 dict 或 None。"""
        url = f"{REVERSE_HOST}?item_ids={item_id}&aid=1967"
        resp = requests.get(url, headers={"User-Agent": WEB_UA}, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError:
            return None
        if data.get("code") != 0:
            return None
        rows = data.get("data") or []
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def get_catalog(self, book_id):
        """目录（免签名接口）。返回旧微服务同构结构。"""
        book_id = str(book_id)
        with self._lock:
            cached = self._catalog_cache.get(book_id)
            if cached and time.time() - cached[1] < CATALOG_TTL_SECONDS:
                return cached[0]
        try:
            resp = requests.get(DIRECTORY_HOST, params={"bookId": book_id},
                                headers={"User-Agent": WEB_UA,
                                         "Accept": "application/json, text/plain, */*"},
                                timeout=self.timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            return {"code": -1, "message": f"目录请求失败: {exc}"}
        if data.get("code") != 0:
            return {"code": data.get("code", -1), "message": data.get("message", "FAIL")}
        d = data.get("data") or {}
        items = []
        for volume in d.get("chapterListWithVolume") or []:
            for ch in volume or []:
                if ch.get("itemId"):
                    items.append({"item_id": str(ch["itemId"]),
                                  "title": ch.get("title", "")})
        if not items:
            # 目录接口偶尔给空（书籍下架/登录态需求），回退到 allItemIds
            items = [{"item_id": str(i), "title": ""} for i in d.get("allItemIds") or []]
        result = {"code": 0, "data": {"item_data_list": items,
                                      "all_item_ids": [i["item_id"] for i in items]}}
        if items:
            with self._lock:
                self._catalog_cache[book_id] = (result, time.time())
        return result

    # ---------- 签名接口 ----------
    def get_detail(self, book_id):
        """书籍元数据（签名接口）。book_name 以该接口为准（搜索索引可能还是旧书名）。"""
        url = API_HOST + "/reading/bookapi/detail/v1" + _qs(_base_params() + [("book_id", str(book_id))])
        try:
            status, text, _env = self._signed_request(url)
        except (SignServiceError, requests.RequestException) as exc:
            return {"code": -1, "message": str(exc)}
        if status != 200:
            return {"code": -1, "message": f"HTTP {status}"}
        try:
            data = json.loads(text)
        except ValueError:
            return {"code": -1, "message": "详情响应非 JSON"}
        if data.get("code") == 0 and not (data.get("data") or {}).get("book_name"):
            # 极少数情况详情缺书名：用章节反查补一次（免签名）
            pass
        return data

    def get_contents(self, item_ids, book_id="0"):
        """批量取正文（≤30 章/次）。返回 {item_id: {"content": 纯文本, "title": ...}}。"""
        ids = [str(x) for x in item_ids][:BATCH_LIMIT]
        if not ids:
            return {}
        batch_url = API_HOST + "/reading/reader/batch_full/v1" + _qs(
            _base_params() + [("item_ids", ",".join(ids)), ("key_register_ts", "0"),
                              ("book_id", str(book_id or "0")), ("req_type", "1")])

        with self._device_lock:
            return self._get_contents_locked(ids, batch_url)

    def _get_contents_locked(self, ids, batch_url):
        key = self._get_key()
        result = {}
        for attempt in (1, 2):
            try:
                status, text, env = self._signed_request(batch_url)
            except SignServiceError as exc:
                self.guard.record(False)
                return {"__error__": str(exc)}
            if status != 200 or not text:
                if attempt == 1:
                    self._recover_device(f"正文 HTTP {status} 空响应")
                    key = self._get_key(force=True)
                    continue
                return {"__error__": f"正文 HTTP {status}"}

            try:
                payload = json.loads(text)
            except ValueError:
                return {"__error__": f"正文响应非 JSON: {text[:150]}"}
            code = payload.get("code")
            if code in (110, 100):
                if attempt == 1:
                    self._recover_device(f"正文 code={code} {payload.get('message')}")
                    key = self._get_key(force=True)
                    continue
                return {"__error__": f"code={code} {payload.get('message')}"}
            if code != 0:
                return {"__error__": f"code={code} {payload.get('message')}"}

            data = payload.get("data") or {}
            ok_count = 0
            for iid in ids:
                item = data.get(iid)
                if not item:
                    continue
                crypt_status = int(item.get("crypt_status") or 0)
                if crypt_status in (1, 2):
                    body = clean_chapter_text(item.get("content") or "")
                else:
                    body = clean_chapter_text(decrypt_content(item.get("content") or "", key))
                if not body and attempt == 1:
                    # 可能是密钥过期：刷新密钥后整批重试
                    key = self._get_key(force=True)
                    break
                result[iid] = {"content": body,
                               "title": item.get("title", ""),
                               "lines": to_lines(body)}
                ok_count += 1
            else:
                self.guard.record(ok_count > 0)
                return result
        # 第二次仍失败
        return result or {"__error__": "正文获取失败（密钥或设备异常）"}

    def get_content(self, item_id):
        """单章正文，返回旧微服务同构结构。"""
        book_id = "0"
        # 反查 book_id 可以让服务端做更准确的校验（实测 book_id=0 也返回正文，这里仅尽力而为）
        info = self.resolve_book_id_by_item(item_id)
        if info and info.get("book_id"):
            book_id = str(info["book_id"])
        contents = self.get_contents([item_id], book_id=book_id)
        if "__error__" in contents:
            return {"code": -1, "message": contents["__error__"]}
        item = contents.get(str(item_id))
        if not item or not item.get("content"):
            return {"code": -1, "message": "正文为空（该章可能无法通过此接口获取）"}
        return {"code": 0, "data": {"content": item["content"], "title": item.get("title", "")}}
