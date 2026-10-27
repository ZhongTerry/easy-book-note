"""番茄 App API 加解密工具（已实测通过）。

依赖：pycryptodome（环境已装 3.23.0；如需固定版本请加入 requirements.txt）

协议要点（2026-10 实测，番茄 App 7.4.1.32 / 海外签名服务 6.8.1.32）：
- registerkey 请求体：AES-128-CBC(REG_KEY, LE64(device_id) || 8 字节 0)，IV 全 0，PKCS7，
  再整体 base64(iv || ciphertext) 后放入 {"content": ..., "keyver": 1}
- registerkey 响应 data.key：同样 base64(iv || ct)，用 REG_KEY 解开，hex 前 32 个字符即正文密钥
- 正文：base64(iv || ct) → AES-128-CBC(key=正文密钥16字节, iv=前16字节) → 可能再套 gzip → utf8
"""
import base64
import gzip
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# registerkey 的固定密钥（来自番茄 App 客户端逻辑，社区公开实现一致）
REG_KEY = bytes.fromhex("ac25c67ddd8f38c1b37a2348828e222e")


def _le64(value: int) -> bytes:
    """小端 8 字节无符号整数。"""
    return int(value).to_bytes(8, "little", signed=False)


def make_registerkey_body(device_id) -> str:
    """构造 registerkey 的 POST body（JSON 字符串）。device_id 必须是 /sign 返回的同一设备。"""
    plain = _le64(int(device_id)) + b"\x00" * 8
    iv = b"\x00" * 16
    cipher = AES.new(REG_KEY, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plain, 16))
    return json.dumps({
        "content": base64.b64encode(iv + ct).decode("ascii"),
        "keyver": 1,
    }, separators=(",", ":"))


def parse_registerkey_key(key_b64: str) -> str:
    """把 registerkey 响应里的 data.key 解成正文密钥（32 位 hex 字符串）。

    返回空串表示格式异常（调用方应视为失败）。
    """
    if not key_b64:
        return ""
    try:
        raw = base64.b64decode(key_b64)
    except Exception:
        return ""
    if len(raw) < 32 or (len(raw) - 16) % 16 != 0:
        return ""
    iv, ct = raw[:16], raw[16:]
    try:
        plain = unpad(AES.new(REG_KEY, AES.MODE_CBC, iv).decrypt(ct), 16)
    except Exception:
        return ""
    return plain.hex()[:32]


def decrypt_content(content_b64: str, key_hex: str) -> str:
    """解密正文。key_hex 是 parse_registerkey_key 的结果（32 位 hex）。

    返回空串表示解密失败（调用方应标记该章失败，不要静默写入垃圾内容）。
    """
    if not content_b64 or not key_hex:
        return ""
    try:
        raw = base64.b64decode(content_b64)
    except Exception:
        return ""
    if len(raw) < 32 or (len(raw) - 16) % 16 != 0:
        return ""
    iv, ct = raw[:16], raw[16:]
    try:
        plain = unpad(AES.new(bytes.fromhex(key_hex), AES.MODE_CBC, iv).decrypt(ct), 16)
    except Exception:
        return ""
    if len(plain) >= 2 and plain[0] == 0x1F and plain[1] == 0x8B:
        try:
            plain = gzip.decompress(plain)
        except Exception:
            return ""
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain.decode("utf-8", errors="replace")


def clean_chapter_text(raw: str) -> str:
    """去掉 <blk> 标题块与 HTML 标签，收敛空白。"""
    from bs4 import BeautifulSoup
    if not raw:
        return ""
    soup = BeautifulSoup(raw, 'html.parser')
    for tag in soup.find_all(['script', 'style']):
        tag.decompose()
    text = soup.get_text('\n').replace('\u3000', ' ')
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def to_lines(text: str):
    """转为 noteDB 适配器需要的按行列表（去空行）。"""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]
