import os
from flask import session, jsonify, redirect, url_for, request, send_file
from functools import wraps
from urllib.parse import urlparse
import socket
from ipaddress import ip_address, ip_network

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
def is_safe_url(url):
    """防止 SSRF 攻击"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # 共享地址空间需明确拦截
        shared_net = ip_network('100.64.0.0/10')

        def _is_private_ip(ip_str: str) -> bool:
            try:
                ip_obj = ip_address(ip_str)
                if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast or ip_obj.is_unspecified:
                    return True
                if ip_obj in shared_net:
                    return True
                return False
            except Exception:
                return True

        # 1) 如果传入的是 IP，直接检查
        try:
            if _is_private_ip(hostname):
                return False
            return True
        except Exception:
            pass

        # 2) 解析域名到所有地址，任一私网即拒绝
        try:
            infos = socket.getaddrinfo(hostname, None)
        except Exception:
            # DNS 解析失败，默认拒绝
            return False

        for info in infos:
            ip_str = info[4][0]
            if _is_private_ip(ip_str):
                return False

        return True
    except Exception:
        return False