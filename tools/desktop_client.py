import os
import sys
import time
import json
import requests
from flask import Flask, render_template, request, session, redirect, jsonify, make_response, send_from_directory, render_template

# ==========================================
# 1. 基础配置
# ==========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REMOTE_SERVER = "https://book.ztrztr.top" 
LOCAL_PORT = 54321
LOCAL_HOST = "127.0.0.1"

# 初始化一个纯净的 Flask，不要导入 dbserver
app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, 'templates'), 
            static_folder=os.path.join(BASE_DIR, 'static'))

app.secret_key = 'smart-notedb-desktop-client-secret-key-fixed'

TOKEN_FILE = os.path.join(BASE_DIR, 'user_token.json')
LOCAL_COOKIES = {}

# ==========================================
# 2. 辅助函数
# ==========================================
def save_local_token(cookie_dict):
    try:
        data = {'user_info': {'cookie': cookie_dict}, 'updated_at': time.time()}
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e: print(f"Save Token Err: {e}")

def load_cookies_from_file():
    global LOCAL_COOKIES
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                LOCAL_COOKIES = data['user_info']['cookie']
                return True
        except: pass
    return False

# ==========================================
# 3. 核心路由 (仅保留渲染和代理)
# ==========================================
purecss_path = os.path.join(BASE_DIR, 'purecss')
static_path = os.path.join(BASE_DIR, 'static')
# desktop_client.py

# ... (前面的导入和配置保持不变) ...

@app.before_request
def ensure_session_is_valid():
    """
    全局前置检查：如果访问的是需要权限的页面，且 Session 丢失，
    立即尝试从本地 user_token.json 恢复。
    """
    path = request.path
    
    # 静态资源、登录接口、同步接口、回调接口不拦截
    if path.startswith('/static') or path.startswith('/purecss') or \
       path in ['/login', '/api/local/sync_cookies', '/sw.js', '/manifest.json']:
        return None

    # 如果 Session 里没有标记，尝试“回血”
    if 'user_logged' not in session:
        print(f"[Auth] 路由 {path} 发现 Session 丢失，尝试从硬盘恢复...")
        if load_cookies_from_file():
            session['user_logged'] = True
            session.permanent = True
            print("[Auth] ✅ 自动恢复登录成功")
        else:
            # 彻底没登录过，才拦截去登录
            print("[Auth] ❌ 硬盘无凭证，引导去线上登录")
            # 如果是 API 请求返回 401，如果是页面请求返回重定向
            if path.startswith('/api/'):
                return jsonify({"status": "error", "code": 401}), 401
            return redirect('/login')
            
    return None
@app.route('/purecss/<path:path>')
def send_pure(path):
    return send_from_directory(purecss_path, path)

@app.route('/sw.js')
def send_sw():
    return send_from_directory(BASE_DIR, 'sw.js')

@app.route('/manifest.json')
def send_manifest():
    return send_from_directory(BASE_DIR, 'manifest.json')

@app.route('/static/<path:path>')
def send_static_fallback(path):
    return send_from_directory(static_path, path)
# @app.route('/')
# def index():
#     if 'user_logged' not in session:
#         if load_cookies_from_file():
#             session['user_logged'] = True
#         else:
#             return redirect('/login')
#     # 渲染本地模板
#     return render_template('index.html', app_version="v1.1.2")
@app.route('/')
def index():
    # 此时 before_request 已经帮你处理好了登录
    return render_template('index.html', app_version="v1.1.2")

@app.route('/login')
def login():
    # 让用户去云端登录
    return redirect(f"{REMOTE_SERVER}/")

@app.route('/api/local/sync_cookies', methods=['POST'])
def sync_cookies():
    # 接收 Electron 抓到的凭证
    cookie_list = request.json.get('cookies', [])
    cookie_dict = {c['name']: c['value'] for c in cookie_list}
    global LOCAL_COOKIES
    LOCAL_COOKIES = cookie_dict
    save_local_token(cookie_dict)
    session['user_logged'] = True
    return jsonify({"status": "success"})

@app.route('/read')
def read_mode():
    # 此时 before_request 已经帮你处理好了登录
    url, key = request.args.get('url'), request.args.get('key')
    api_url = f"{REMOTE_SERVER}/api/v2/read"
    
    try:
        # LOCAL_COOKIES 已经在 before_request 里的 load_cookies_from_file 加载好了
        resp = requests.get(api_url, 
                            params={'url': url, 'key': key}, 
                            cookies=LOCAL_COOKIES, 
                            timeout=15, verify=False)
        
        if resp.status_code == 200:
            d = resp.json()
            return render_template('reader_pc.html', 
                                 article=d['data'], 
                                 current_url=d['current_url'], 
                                 db_key=d['db_key'], 
                                 chapter_id=d.get('chapter_id', -1))
        
        # 如果云端返回 401，说明硬盘里的 Cookie 真的过期了
        if resp.status_code == 401:
            session.clear()
            if os.path.exists(TOKEN_FILE): os.remove(TOKEN_FILE)
            return redirect('/login')
            
        return f"云端异常: {resp.status_code}", resp.status_code
    except Exception as e:
        return f"连接云端失败: {e}", 500

# ==========================================
# 4. 万能代理转发 (最关键的部分)
# ==========================================
@app.before_request
def proxy_all_data_requests():
    path = request.path
    # 1. 排除本地定义的路由，这些不转发
    if path in ['/', '/login', '/read', '/api/local/sync_cookies'] or \
       path.startswith('/static') or path.startswith('/purecss') or \
       path in ['/sw.js', '/manifest.json']:
        return None

    # 2. 检查 Cookie 是否存在
    global LOCAL_COOKIES
    if not LOCAL_COOKIES: load_cookies_from_file()

    # 如果完全没有 Cookie 记录，直接在本地拦截并返回 401，引导登录
    if not LOCAL_COOKIES:
        return jsonify({"status": "error", "msg": "Client not logged in", "code": 401}), 401

    remote_url = f"{REMOTE_SERVER}{path}"
    
    try:
        # === [核心修复：Header 清洗] ===
        # 必须从转发的 Headers 中剔除 'Cookie'、'Host' 和 'Content-Length'
        # 如果不剔除 'Cookie'，本地的 session 标识会干扰云端的 session 标识
        headers = {
            k: v for k, v in request.headers 
            if k.lower() not in ['host', 'content-length', 'cookie', 'connection']
        }
        
        # 伪造 Referer 和 Origin，让云端认为请求来自它自己
        headers['Referer'] = REMOTE_SERVER
        headers['Origin'] = REMOTE_SERVER

        kwargs = {
            'method': request.method,
            'url': remote_url,
            'headers': headers,
            'params': request.args,
            'cookies': LOCAL_COOKIES, # 这里才是真正的生产环境凭证
            'timeout': 20,
            'verify': False,
            'allow_redirects': False # 严禁自动跳转，防止逻辑混乱
        }

        if request.method in ['POST', 'PUT'] and request.is_json:
            kwargs['json'] = request.json

        # 发起请求
        resp = requests.request(**kwargs)
        
        # 如果转发过程中云端返回 401，说明本地存的 Cookie 过期了
        if resp.status_code == 401:
            print(f"⚠️ [Proxy] 生产环境 Cookie 已过期，清除本地凭证。")
            if os.path.exists(TOKEN_FILE): os.remove(TOKEN_FILE)
            LOCAL_COOKIES = {}
            # 这里可以返回一个特殊的 JSON，让前端知晓需要重新登录
            return jsonify({"status": "error", "msg": "Login expired", "code": 401}), 401

        # 构造响应给本地前端
        response = make_response(resp.content, resp.status_code)
        
        # 仅透传必要的 Content-Type
        if 'Content-Type' in resp.headers:
            response.headers['Content-Type'] = resp.headers['Content-Type']
            
        return response

    except Exception as e:
        print(f"❌ [Proxy] 转发异常: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 502

# ==========================================
# 5. 静态资源转发 (保持不变)
# ==========================================
# @app.route('/purecss/<path:path>')
# def send_pure(path): return send_from_directory(os.path.join(app.static_folder, 'purecss'), path)
# @app.route('/sw.js')
# def send_sw(): return send_from_directory(app.static_folder, 'sw.js')
# @app.route('/manifest.json')
# def send_manifest(): return send_from_directory(app.static_folder, 'manifest.json')

if __name__ == '__main__':
    app.run(host=LOCAL_HOST, port=LOCAL_PORT, debug=False, use_reloader=False)