import os
import json
import time
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
# === 域名验证缓存管理器 ===
class DomainVerificationCache:
    """智能域名验证缓存（30天有效期）"""
    def __init__(self):
        self.cache_file = os.path.join(USER_DATA_DIR, 'domain_verification_cache.json')
        self.cache = self._load_cache()
        self.cache_ttl = 30 * 24 * 3600  # 30天（秒）
    
    def _load_cache(self):
        """加载缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except:
            pass
        return {}
    
    def _save_cache(self):
        """保存缓存"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[DomainCache] 保存失败: {e}")
    
    def get(self, domain):
        """获取缓存的验证结果"""
        if domain not in self.cache:
            return None
        
        record = self.cache[domain]
        # 检查是否过期
        if time.time() - record['timestamp'] > self.cache_ttl:
            return None
        
        return record['is_valid']
    
    def set(self, domain, is_valid):
        """设置验证结果"""
        self.cache[domain] = {
            'is_valid': is_valid,
            'timestamp': time.time()
        }
        self._save_cache()

# 全局缓存实例
_domain_cache = DomainVerificationCache()

def verify_domain_online(domain):
    """
    通过第三方方式验证域名是否合法
    1. 尝试 DNS 解析
    2. 尝试 HTTP HEAD 请求
    """
    try:
        # 方法1: DNS 解析测试
        socket.gethostbyname(domain)
        
        # 方法2: HTTP 连通性测试（HEAD 请求，不下载内容）
        import requests
        response = requests.head(f'http://{domain}', timeout=5, allow_redirects=True)
        
        # 如果返回 200-499 状态码，说明域名可访问（包括403、404等）
        # 5xx 表示服务器错误，也说明域名存在
        if 200 <= response.status_code < 600:
            return True
        
        return False
    except Exception as e:
        print(f"[DomainVerify] {domain} 验证失败: {e}")
        return False

def is_safe_url(url):
    """智能 SSRF 防护（带域名验证缓存）"""
    try:
        # [快速路径1] 环境变量控制：完全关闭 SSRF 检查
        if os.getenv('DISABLE_SSRF_CHECK', '0') == '1':
            parsed = urlparse(url)
            return parsed.scheme in ('http', 'https')
        
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # [快速路径2] 白名单：已知的小说网站域名，直接放行
        trusted_domains = [
            '22biqu.com', 'sxgread.com', 'fanqienovel.com',
            'xbqg77.com', 'qidian.com', 'zongheng.com', 'ciweimao.com',
        ]
        
        for trusted in trusted_domains:
            if hostname == trusted or hostname.endswith('.' + trusted):
                return True

        # [快速路径3] 检查缓存（30天内验证过的域名）
        cached_result = _domain_cache.get(hostname)
        if cached_result is not None:
            print(f"[SSRF] 🚀 使用缓存结果: {hostname} = {cached_result}")
            return cached_result

        # [智能验证] 在线验证域名合法性
        print(f"[SSRF] 🔍 首次验证域名: {hostname}")
        is_valid = verify_domain_online(hostname)
        
        # 缓存验证结果（无论成功或失败）
        _domain_cache.set(hostname, is_valid)
        
        if is_valid:
            print(f"[SSRF] ✅ 域名验证通过: {hostname}")
        else:
            print(f"[SSRF] ❌ 域名验证失败: {hostname}")
        
        return is_valid
        
    except Exception as e:
        print(f"[SSRF] 检查异常: {e}")
        return False