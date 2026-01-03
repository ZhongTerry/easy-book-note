import os
from flask import session, jsonify, redirect, url_for, request
from functools import wraps
from urllib.parse import urlparse
import socket

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
        if 'user' not in session:
            # API 请求返回 401，页面请求跳转登录
            if request.path.startswith('/api/') or request.path in ['/insert', '/update', '/remove', '/list', '/find', '/rollback']:
                return jsonify({"status": "error", "message": "Unauthorized"}), 401
            # 注意：这里的 endpoint 改为了 core.login (适配蓝图)
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
        if parsed.scheme not in ('http', 'https'): return False
        hostname = parsed.hostname
        try:
            ip = socket.gethostbyname(hostname)
        except:
            return True 
        if ip.startswith(('127.', '192.168.', '10.', '172.16.', '0.')): return False
        return True
    except:
        return False