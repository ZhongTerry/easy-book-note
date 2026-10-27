"""fq-sign-api 签名服务客户端（已实测通过）。

签名服务是独立进程（Java + Unidbg，见 说明-02），本模块只负责 HTTP 调用：
    POST {FQ_SIGN_URL}/sign   body: {"url": "...", "device": "0001"（可选）}
    响应: {"ok":true, "headers":{8个签名头}, "device":"0001", "url":"改写后的URL",
           "cookie":"...", "ua":"...", "req_headers":{...}, "device_info":{...}, "key":"..."}

硬性规则（违反会拿到 code=110 ILLEGAL_ACCESS 或空响应）：
1. 真实请求必须用响应里的 `url`（iid/device_id/cdid 已被改写成该设备的值）
2. 请求头必须用响应里的 `req_headers` + `headers` + `cookie` + `ua` 原样发出；
   自己重算 x-ss-req-ticket / x-reading-request 会导致签名不一致
3. 同一请求的签名、Cookie、UA、解密密钥必须属于同一台设备
4. 服务单 IP 限速 1 次 / 5 秒（FQ_PER_IP_MS），这里做了进程内节流
"""
import json
import _thread
import threading
import time

import requests


class SignServiceError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class SignService:
    """签名服务客户端。线程安全；sign() 内部串行并保持最小间隔。"""

    def __init__(self, base_url="http://127.0.0.1:8090", timeout=60,
                 min_interval=5.2, token=""):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval  # 服务端默认 5000ms/IP，留 200ms 余量
        self.token = (token or "").strip()
        self._lock = _thread.allocate_lock()
        self._sign_lock = threading.RLock()
        self._last_call = 0.0
        self._device = None  # 固定设备（4 位内部 id），保证 签名↔密钥 一致

    # ---- 底层 ----
    def _throttle(self):
        with self._lock:
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _post(self, url, payload):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SignServiceError(f"签名服务不可达: {exc}") from exc
        try:
            data = resp.json()
        except ValueError:
            raise SignServiceError(f"签名服务返回非 JSON (HTTP {resp.status_code})",
                                   status=resp.status_code, payload=resp.text[:300])
        return resp.status_code, data

    # ---- 对外 ----
    def sign(self, target_url, device=None):
        """为 target_url 取签名。device 为空时使用已固定设备，首次调用后自动固定。

        返回完整 env（调用方必须使用 env["url"] 与 env 中的头/设备信息）。
        """
        with self._sign_lock:
            return self._sign_locked(target_url, device)

    def _sign_locked(self, target_url, device=None):
        if not self.base_url.endswith("/sign"):
            endpoint = self.base_url + "/sign"
        else:
            endpoint = self.base_url
        use_device = device or self._device
        payload = {"url": target_url}
        if use_device:
            payload["device"] = use_device
        self._throttle()
        status, data = self._post(endpoint, payload)
        if status == 429:
            # 服务端限流：等待 Retry-After 后重试一次
            retry_after = 6
            try:
                retry_after = int(data.get("retry_after") or retry_after)
            except (TypeError, ValueError):
                pass
            time.sleep(max(1, retry_after))
            self._throttle()
            status, data = self._post(endpoint, payload)
        if status != 200 or not data.get("ok"):
            raise SignServiceError(
                f"签名失败 HTTP {status}: {json.dumps(data, ensure_ascii=False)[:300]}",
                status=status, payload=data)
        returned_device = data.get("device")
        if not returned_device or (use_device and returned_device != use_device):
            raise SignServiceError("签名服务返回设备不一致或缺失")
        if not self._device:
            self._device = returned_device
        return data

    @property
    def device(self):
        return self._device

    def reset_device(self):
        with self._sign_lock:
            self._device = None

    def report_device(self, device_id=None):
        """上报设备异常，让服务替换一台并补足池子（全局 10 秒一次）。"""
        dev = device_id or self._device
        if not dev:
            return None
        endpoint = self.base_url
        if endpoint.endswith("/sign"):
            endpoint = endpoint[: -len("/sign")]
        try:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            resp = requests.post(endpoint + "/device/report",
                                 json={"id": dev}, headers=headers, timeout=self.timeout)
            return resp.json() if resp.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None

    def health(self):
        endpoint = self.base_url
        if endpoint.endswith("/sign"):
            endpoint = endpoint[: -len("/sign")]
        try:
            return requests.get(endpoint + "/health", timeout=10).json()
        except (requests.RequestException, ValueError):
            return None


class CircuitGuard:
    """简单熔断器：连续失败 N 次后冷却，避免服务离线时每章都等超时。"""

    def __init__(self, threshold=3, cooldown=300):
        self.threshold = threshold
        self.cooldown = cooldown
        self._consecutive = 0
        self._until = 0.0
        self._lock = _thread.allocate_lock()

    def is_open(self):
        with self._lock:
            return time.time() < self._until

    def record(self, ok):
        with self._lock:
            if ok:
                self._consecutive = 0
                return
            self._consecutive += 1
            if self._consecutive >= self.threshold:
                self._until = time.time() + self.cooldown
                self._consecutive = 0
