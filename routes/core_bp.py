from flask import Blueprint, request, jsonify, send_file, render_template, redirect, url_for, send_from_directory, session
import requests
import os
from shared import login_required, is_safe_url, BASE_DIR, DL_DIR
import managers
from spider_core import crawler_instance as crawler, searcher, epub_handler

core_bp = Blueprint('core', __name__)
DEFAULT_SERVER = 'https://auth.ztrztr.top'
DEFAULT_CALLBACK = 'https://book.ztrztr.top/callback'
# 注意：CLIENT_ID 和 SECRET 通常不建议硬编码默认值，
# 但为了配合你的逻辑，如果 .env 没填，这里可以留空或者写死你的备用 Key
DEFAULT_CLIENT_ID = None 
DEFAULT_CLIENT_SECRET = None
CLIENT_ID = os.environ.get('CLIENT_ID') or DEFAULT_CLIENT_ID
CLIENT_SECRET = os.environ.get('CLIENT_SECRET') or DEFAULT_CLIENT_SECRET
AUTH_SERVER = os.environ.get('SERVER', 'https://auth.ztrztr.top')
REDIRECT_URI = os.environ.get('CALLBACK', 'https://book.ztrztr.top/callback')

@core_bp.route('/login')
def login(): return redirect(f"{AUTH_SERVER}/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}")

@core_bp.route('/callback')
def callback():
    code = request.args.get('code')
    try:
        resp = requests.post(f"{AUTH_SERVER}/oauth/token", json={'grant_type': 'authorization_code', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'code': code}).json()
        if 'access_token' in resp:
            u = requests.get(f"{AUTH_SERVER}/api/user", headers={'Authorization': f"Bearer {resp['access_token']}"}).json()
            session.permanent = True
            session['user'] = u
            return redirect(url_for('core.index'))
    except: pass
    return "Login Failed", 400

@core_bp.route('/logout')
def logout(): session.clear(); return redirect('/')

# routes/core_bp.py

from flask import make_response # 记得引入这个

@core_bp.route('/api/me')
def api_me():
    # 1. 获取 Session 中的基础信息
    user = session.get('user', {"username": None})
    
    # 2. 【核心】实时查询并注入角色权限
    # 即使 Session 里没存 role，这里也要查出来塞进去
    if user.get('username'):
        # 这里的 managers.role_manager 需要确保已导入
        user['role'] = managers.role_manager.get_role(user['username'])
    
    # 3. 【核心】构建响应并禁止缓存
    response = make_response(jsonify(user))
    # 告诉浏览器和 CDN：不要缓存这个请求！
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@core_bp.route('/')
@login_required
def index(): return send_file(os.path.join(BASE_DIR, 'index.html'))

@core_bp.route('/read')
@login_required
def read_mode():
    u, k = request.args.get('url'), request.args.get('key', '')
    force = request.args.get('force')
    if not u.startswith('epub:') and not is_safe_url(u): return "Illegal URL", 403
    
    if u.startswith('epub:'):
        p = u.split(':')
        if p[2] == 'toc': return redirect(url_for('core.toc_page', url=u, key=k))
        data = epub_handler.get_chapter_content(p[1], int(p[2]))
    else:
        # 优先读离线包
        data = managers.offline_manager.get_chapter(k, u) if k and not force else None
        # 其次读缓存
        if not data and not force: data = managers.cache.get(u)
        # 最后抓取
        if not data:
            data = crawler.run(u)
            if data: managers.cache.set(u, data)
    
    return render_template('reader.html', article=data, current_url=u, db_key=k)

@core_bp.route('/toc')
@login_required
def toc_page():
    u, k = request.args.get('url'), request.args.get('key', '')
    force = request.args.get('force')
    data = None if force else managers.cache.get(u)
    if not data:
        data = crawler.get_toc(u)
        if data: managers.cache.set(u, data)
    return render_template('toc.html', toc=data, toc_url=u, db_key=k)

@core_bp.route('/list', methods=['POST'])
@login_required
def list_all(): return jsonify(managers.db.list_all())

@core_bp.route('/find', methods=['POST'])
@login_required
def find(): return jsonify(managers.db.find(request.json.get('key', '')))

@core_bp.route('/insert', methods=['POST'])
@login_required
def insert(): return jsonify(managers.db.insert(request.json.get('key'), request.json.get('value')))

@core_bp.route('/update', methods=['POST'])
@login_required
def update(): return jsonify(managers.db.update(request.json.get('key'), request.json.get('value')))

@core_bp.route('/remove', methods=['POST'])
@login_required
def remove(): return jsonify(managers.db.remove(request.json.get('key')))

@core_bp.route('/rollback', methods=['POST'])
@login_required
def rollback(): return jsonify(managers.db.rollback())

@core_bp.route('/api/get_value', methods=['POST'])
@login_required
def get_val():
    v = managers.db.get_val(request.json.get('key'))
    return jsonify({"status": "success", "value": v}) if v else jsonify({"status": "error"})

@core_bp.route('/api/last_read', methods=['GET', 'POST'])
@login_required
def handle_last_read():
    if request.method == 'GET': return jsonify({"status": "success", "key": managers.db.get_val('@last_read')})
    return jsonify(managers.db.insert('@last_read', request.json.get('key')))

@core_bp.route('/api/tags/list')
@login_required
def api_tags_list(): return jsonify({"status": "success", "data": managers.tag_manager.get_all()})

@core_bp.route('/api/tags/update', methods=['POST'])
@login_required
def api_tags_update(): return jsonify({"status": "success", "tags": managers.tag_manager.update_tags(request.json.get('key'), request.json.get('tags', []))})

@core_bp.route('/api/analyze_stats')
@login_required
def api_analyze_stats(): return jsonify({"status": "success", "summary": managers.stats_manager.get_summary(), "keywords": []})

@core_bp.route('/api/stats/heartbeat', methods=['POST'])
@login_required
def api_heartbeat():
    d = request.json
    managers.stats_manager.update(60 if d.get('is_heartbeat') else 0, d.get('words', 0), 1 if d.get('words', 0)>0 else 0, d.get('book_key'))
    return jsonify({"status": "success"})

@core_bp.route('/api/booklists/all')
@login_required
def api_booklists_all(): return jsonify({"status": "success", "data": managers.booklist_manager.load()})

@core_bp.route('/api/booklists/create', methods=['POST'])
@login_required
def api_booklists_create(): return jsonify({"status": "success", "id": managers.booklist_manager.add_list(request.json.get('name'))})

@core_bp.route('/api/booklists/add_book', methods=['POST'])
@login_required
def api_booklists_add(): 
    managers.booklist_manager.add_to_list(request.json['list_id'], request.json['book_data'])
    return jsonify({"status": "success"})

@core_bp.route('/api/prefetch', methods=['POST'])
@login_required
def api_prefetch():
    u = request.json.get('url')
    if managers.cache.get(u): return jsonify({"status": "skipped"})
    d = crawler.run(u)
    if d:
        managers.cache.set(u, d)
        return jsonify({"status": "success"})
    return jsonify({"status": "failed"})

@core_bp.route('/api/resolve_head', methods=['POST'])
@login_required
def api_resolve_head():
    try: return jsonify({"status": "success", "url": crawler.get_first_chapter(request.json.get('url'))})
    except: return jsonify({"status": "error"})

@core_bp.route('/api/search_novel', methods=['POST'])
@login_required
def api_search(): return jsonify({"status": "success", "data": searcher.search_bing(request.json.get('keyword'))})

@core_bp.route('/api/upload_epub', methods=['POST'])
@login_required
def api_upload_epub():
    if 'file' not in request.files: return jsonify({"status": "error"})
    f = request.files['file']
    fn = epub_handler.save_file(f)
    k = searcher.get_pinyin_key(os.path.splitext(fn)[0])
    v = f"epub:{fn}:toc"
    managers.db.insert(k, v)
    return jsonify({"status": "success", "key": k, "value": v})

@core_bp.route('/api/download', methods=['POST'])
@login_required
def start_dl():
    d = request.json
    toc = managers.cache.get(d['toc_url']) or crawler.get_toc(d['toc_url'])
    if not toc: return jsonify({"status": "error"})
    return jsonify({"status": "success", "task_id": managers.downloader.start_download(d['book_name'], toc['chapters'], crawler)})

@core_bp.route('/api/download/status')
@login_required
def dl_status(): return jsonify(managers.downloader.get_status(request.args.get('task_id')))

@core_bp.route('/api/download/file')
@login_required
def dl_file():
    t = managers.downloader.get_status(request.args.get('task_id'))
    return send_from_directory(DL_DIR, t['filename'], as_attachment=True) if t else ("Not Found", 404)

@core_bp.route('/manifest.json')
def serve_manifest(): return send_file('manifest.json')
@core_bp.route('/sw.js')
def serve_sw(): return send_file('sw.js')
@core_bp.route('/static/<path:filename>')
def serve_static(filename): return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)
@core_bp.route('/purecss/<path:path>')
def send_pure(path): return send_from_directory(os.path.join(BASE_DIR, 'purecss'), path)