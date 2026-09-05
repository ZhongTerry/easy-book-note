import os
from flask import session, jsonify, redirect, url_for, request, send_file
from functools import wraps
from urllib.parse import urlparse
import socket
from ipaddress import ip_address
from utils import debug, info, warn, error

# === 基础路径配置 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LIB_DIR = os.path.join(BASE_DIR, "library")
DL_DIR = os.path.join(BASE_DIR, "downloads")

# 自动创建目录
for d in [USER_DATA_DIR, CACHE_DIR, LIB_DIR, DL_DIR]:
    if not os.path.exists(d): os.makedirs(d)

# === 角色管理器占位符 (由 managers.py 注入) ===
role_manager_instance = None 

# === 登录装饰器 ===
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 检查用户是否在 Session 中
        if 'user' not in session:
            # === [核心修改] ===
            
            # 1. 如果是 API 请求，返回 JSON 错误
            # 这样前端 fetch 收到 401 可以静默处理，而不是收到一堆 HTML 报错
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({
                    "status": "error", 
                    "msg": "Unauthorized: Please login first", 
                    "code": 401
                }), 401
            
            # 2. 如果是页面请求，直接返回“未登录首页”
            # 注意：这里假设你的 index_guest.html 放在 templates 文件夹下
            try:
                # 假设 BASE_DIR 在 shared.py 同级或已导入
                # 如果 shared.py 里没有 BASE_DIR，请手动定义一下:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                guest_page_path = os.path.join(base_dir, 'templates', 'index_guest.html')
                return send_file(guest_page_path)
            except Exception as e:
                # 如果找不到文件，作为兜底才重定向
                print(f"[Auth] Guest page not found: {e}")
                return redirect(url_for('core.login'))
        
        return f(*args, **kwargs)
    return decorated_function

# === 权限装饰器 ===
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not role_manager_instance: return jsonify({"error": "System loading"}), 500
        user = session.get('user', {})
        if role_manager_instance.get_role(user.get('username')) != 'admin':
            return jsonify({"status": "error", "message": "Admin permission required"}), 403
        return f(*args, **kwargs)
    return decorated

# === shared.py ===

def pro_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not role_manager_instance: return jsonify({"error": "System loading"}), 500
        user = session.get('user', {})
        role = role_manager_instance.get_role(user.get('username'))
        
        # [核心修正]：只要是 admin 或者 pro，都允许通过
        if role not in ['admin', 'pro']:
            return jsonify({"status": "error", "message": "Pro membership required"}), 403
        return f(*args, **kwargs)
    return decorated

# === 安全工具 ===
def _ssrf_check_disabled():
    """只允许在明确标记的本地环境关闭 SSRF 检查。"""
    environment = os.getenv('APP_ENV', '').lower()
    return (
        os.getenv('DISABLE_SSRF_CHECK', '0') == '1'
        and environment in {'development', 'test', 'local'}
    )


def _resolve_public_addresses(hostname, port):
    """解析主机的全部地址；任一非公网地址都会使校验失败。"""
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    }
    if not addresses:
        return False
    return all(ip_address(address).is_global for address in addresses)


def verify_domain_online(domain):
    """兼容旧调用方：域名必须仅解析到公网地址。"""
    try:
        return _resolve_public_addresses(domain, 80)
    except (OSError, ValueError, UnicodeError):
        return False


def is_safe_url(url):
    """拒绝可能访问本机、内网或保留地址的外部 URL。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False

        hostname = parsed.hostname.rstrip('.').lower()
        if hostname == 'localhost' or hostname.endswith('.local'):
            return False

        # 解析 parsed.port 也会拒绝非法端口。
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if _ssrf_check_disabled():
            return True
        return _resolve_public_addresses(hostname, port)
    except (OSError, ValueError, UnicodeError):
        return False
