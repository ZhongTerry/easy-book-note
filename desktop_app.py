import os
import sys
import time
import threading
import json
import requests
import webview
import keyboard  # 需要 pip install keyboard
from flask import Flask, render_template, request, session, redirect, jsonify, make_response

# ==========================================
# 1. 配置区域 (Configuration)
# ==========================================

# 云端服务器地址 (你的生产环境域名)
REMOTE_SERVER = "https://book.ztrztr.top" 
# 本地运行端口
LOCAL_PORT = 54321
LOCAL_HOST = "127.0.0.1"
LOCAL_BASE = f"http://{LOCAL_HOST}:{LOCAL_PORT}"

# 窗口标题
WINDOW_TITLE = "Smart NoteDB - 沉浸阅读器"

# ==========================================
# 2. Flask 应用初始化 (支持 PyInstaller 打包)
# ==========================================

def get_resource_path():
    """获取资源绝对路径 (适配 PyInstaller 打包后的临时目录)"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

base_dir = get_resource_path()
template_dir = os.path.join(base_dir, 'templates')
static_dir = os.path.join(base_dir, 'static')

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
app.secret_key = os.urandom(24)  # 本地 Session 加密密钥

# ==========================================
# 3. 辅助函数
# ==========================================

def get_auth_headers():
    """获取带 Token 的请求头"""
    token = session.get('access_token')
    headers = {
        'User-Agent': 'SmartNoteDB-Desktop-Client/1.0',
        'Content-Type': 'application/json'
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers

# ==========================================
# 4. 路由定义 (Routes)
# ==========================================

@app.route('/')
def index():
    """首页：如果没登录，跳去登录；否则渲染本地 index.html"""
    if 'user_info' not in session:
        return redirect('/login')
    
    # 注入版本号 (模拟 context_processor)
    # 这里你需要确保 index.html 里的 {{ app_version }} 能被渲染
    # 为了简单，我们手动传参，或者你可以把 get_latest_version 逻辑搬过来
    return render_template('index.html', app_version="v1.1.2")

@app.route('/login')
def login():
    """
    发起登录：跳转到云端 SSO
    注意：你需要去云端配置回调白名单包含 http://127.0.0.1:54321/callback
    """
    # 构造云端登录链接
    # 假设云端有一个 /sso/desktop_login 接口专门处理桌面端跳转
    # 或者直接跳到 OAuth 授权页，并指定 callback 为本地
    
    # 简单模式：让用户去云端登录，云端登录成功后带着 token 跳回本地
    redirect_url = f"{REMOTE_SERVER}/login?next={LOCAL_BASE}/callback_receive"
    return redirect(redirect_url)

@app.route('/callback_receive')
def callback_receive():
    """
    接收云端传回的 Token
    假设云端重定向回：http://127.0.0.1:54321/callback_receive?token=xxxx&username=xxxx
    """
    token = request.args.get('token')
    username = request.args.get('username')
    avatar = request.args.get('avatar', '')
    
    if token:
        session['access_token'] = token
        session['user_info'] = {'username': username, 'avatar': avatar}
        return redirect('/')
    else:
        return "登录失败：未接收到 Token"

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# --- 核心页面渲染 (Read / TOC) ---

@app.route('/read')
def read_mode():
    if 'user_info' not in session:
        return redirect('/login')

    url = request.args.get('url')
    key = request.args.get('key')
    
    try:
        # 向云端请求数据 (不再本地爬虫)
        # 假设云端已经按照我们之前的讨论，建立了 /api/v2/read 纯数据接口
        # 如果云端还没改，这里需要请求云端的 HTML 并提取数据 (比较麻烦)
        # 我们假设云端已经支持 JSON 返回
        api_url = f"{REMOTE_SERVER}/api/v2/read"
        
        resp = requests.get(api_url, params={'url': url, 'key': key}, headers=get_auth_headers(), timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            # 在本地渲染 reader_pc.html
            return render_template(
                'reader_pc.html',
                article=data['data'],
                current_url=data['current_url'],
                db_key=data['db_key'],
                chapter_id=data.get('chapter_id', -1),
                app_version="v1.1.2"
            )
        elif resp.status_code == 401:
            return redirect('/login')
        else:
            return f"Remote Error: {resp.text}", 500
            
    except Exception as e:
        return f"Network Error: {e}", 500

@app.route('/toc')
def toc_page():
    # 这里的逻辑主要是处理侧边栏目录加载
    # 如果 reader_pc.html 里是 fetch('/toc?api=true')，会走到下面的 API 代理
    # 如果是直接访问页面，走这里
    url = request.args.get('url')
    key = request.args.get('key')
    return render_template('toc.html', toc_url=url, db_key=key) # 简单渲染，数据靠 JS 拉取

# --- 万能 API 代理 (The Proxy) ---

@app.route('/api/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def api_proxy(subpath):
    """
    将前端所有 /api/xxx 请求转发到云端
    """
    remote_url = f"{REMOTE_SERVER}/api/{subpath}"
    
    # 构造请求参数
    kwargs = {
        'headers': get_auth_headers(),
        'params': request.args,
        'timeout': 30
    }
    
    if request.method in ['POST', 'PUT']:
        kwargs['json'] = request.json
        
    try:
        # 发起转发
        resp = requests.request(request.method, remote_url, **kwargs)
        
        # 透传响应
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.headers.items() 
                   if name.lower() not in excluded_headers]
        
        return (resp.content, resp.status_code, headers)
        
    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "msg": f"Proxy Error: {str(e)}"}), 502

# ==========================================
# 5. 桌面窗口管理 (PyWebview)
# ==========================================

class WindowApi:
    """JS 交互接口"""
    def minimize(self):
        window.minimize()
    
    def close(self):
        force_quit()

def start_flask():
    app.run(host=LOCAL_HOST, port=LOCAL_PORT, debug=False, use_reloader=False)

def force_quit():
    print("正在退出程序...")
    try:
        window.destroy()
    except: pass
    os._exit(0)

def toggle_visibility():
    if window.hidden:
        window.show()
        window.restore()
        window.hidden = False
    else:
        window.hide()
        window.hidden = True

def on_loaded():
    print(f"✅ {WINDOW_TITLE} 已启动")
    print(f"👉 本地服务: {LOCAL_BASE}")
# ==========================================
# [新增] 全局行为补丁 (解决 target="_blank" 跳出问题)
# ==========================================
def inject_global_patch():
    """
    每次页面加载时注入 JS，强制拦截所有 target="_blank" 的点击，
    将其改为在当前窗口打开 (window.location.href)。
    """
    js_code = """
    // 监听全局点击事件 (捕获阶段)
    document.addEventListener('click', function(e) {
        // 寻找被点击元素最近的 <a> 标签
        var target = e.target.closest('a');
        
        // 如果找到了 <a> 标签
        if (target) {
            // 检查是否带有 target="_blank" 或者 target="_new"
            if (target.getAttribute('target') === '_blank' || target.getAttribute('target') === '_new') {
                // 1. 阻止浏览器默认的新窗口行为
                e.preventDefault();
                e.stopPropagation();
                
                // 2. 强制在当前窗口加载链接
                window.location.href = target.href;
                
                console.log("[PyWebview] Intercepted external link:", target.href);
            }
        }
    }, true); // useCapture = true 确保我们在事件冒泡前捕获它
    """
    # 在当前页面执行这段 JS
    window.evaluate_js(js_code)
if __name__ == '__main__':
    # 1. 启动 Flask
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()
    
    # 2. 注册热键
    try:
        keyboard.add_hotkey('alt+z', toggle_visibility)
        keyboard.add_hotkey('ctrl+c', force_quit)
    except:
        print("全局热键注册失败，请检查权限")

    # 3. 创建窗口
    # 注意：前端静态资源引用 (src="/static/...") 会自动指向 Flask
    window = webview.create_window(
        WINDOW_TITLE, 
        LOCAL_BASE,
        width=1100,
        height=800,
        min_size=(400, 300),
        frameless=False, # 建议先开启边框调试，稳定后再无边框
        js_api=WindowApi()
    )
    window.events.loaded += inject_global_patch
    window.events.closed += force_quit
    
    # 4. 启动 Loop
    webview.start(on_loaded)