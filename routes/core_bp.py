from flask import Blueprint, request, jsonify, send_file, render_template, redirect, url_for, send_from_directory, session
import requests
import os
from shared import login_required, is_safe_url, BASE_DIR, DL_DIR
import managers
from spider_core import crawler_instance as crawler, searcher, epub_handler
import re
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
    if data and k and data.get('title'):
        # 只有当获取内容成功且有 Key 时才记录
        # title 可能是 "第xxx章 标题"，我们最好也存一下书名(如果有的话)，
        # 但这里只有章节标题。为了简单，我们暂时存章节标题，
        # 或者前端展示时用 Key (书名拼音) + 章节标题。
        managers.history_manager.add_record(k, data['title'], u)
    return render_template('reader.html', article=data, current_url=u, db_key=k)
@core_bp.route('/api/history/list')
@login_required
def api_history_list():
    return jsonify({"status": "success", "data": managers.history_manager.get_history()})

@core_bp.route('/api/history/clear', methods=['POST'])
@login_required
def api_history_clear():
    managers.history_manager.clear()
    return jsonify({"status": "success"})
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
def insert():
    key = request.json.get('key')
    value = request.json.get('value')
    is_manual = request.json.get('manual', False) # 获取前端传来的标记
    
    # 1. 保存当前值
    res = managers.db.insert(key, value)
    
    # 2. 如果是手动操作，记录历史版本
    if is_manual and res.get('status') == 'success':
        managers.db.add_version(key, value)
        
    return jsonify(res)


@core_bp.route('/update', methods=['POST'])
@login_required
def update():
    key = request.json.get('key')
    value = request.json.get('value')
    is_manual = request.json.get('manual', False) # 获取前端传来的标记

    # 1. 保存当前值
    result = managers.db.update(key, value)
    
    # 2. 如果是手动操作，记录历史版本
    if is_manual and result.get('status') == 'success':
        managers.db.add_version(key, value)
    # 2. [新增] 顺手更新“追更状态”
    try:
        # 获取该书当前的更新信息
        info = managers.update_manager.get_update(key)
        
        # 只有当这本书在追更列表里 (即有 info 信息) 且有数字 ID 时才计算
        if info and info.get('latest_url'):
            # 如果当前链接就是最新链接，直接清零
            if value == info['latest_url']:
                 managers.update_manager.update_progress(key, 0, "已追平")
            
            # 否则尝试通过 ID 计算
            else:
                # 尝试从 value (当前URL) 中提取数字 ID
                # 匹配 /123.html 或 /123_2.html 中的 123
                match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', value.split('?')[0])
                if match:
                    current_id = int(match.group(1))
                    
                    # 我们暂时没法从 info 里直接拿 latest_id (因为之前存的是 title/url/total)
                    # 但是我们可以尝试再次解析 latest_url 的 ID
                    latest_url = info['latest_url']
                    l_match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', latest_url.split('?')[0])
                    
                    if l_match:
                        latest_id = int(l_match.group(1))
                        
                        # 计算差距
                        diff = latest_id - current_id
                        if diff > 0:
                            managers.update_manager.update_progress(key, diff, f"落后 {diff} 章")
                        elif diff <= 0:
                            managers.update_manager.update_progress(key, 0, "已追平")
    except Exception as e:
        print(f"[AutoUpdate] Failed to recalc progress: {e}")

    return jsonify(result)

@core_bp.route('/api/history/versions', methods=['POST'])
@login_required
def api_history_versions():
    key = request.json.get('key')
    if not key: return jsonify({"status": "error"})
    
    versions = managers.db.get_versions(key)
    return jsonify({"status": "success", "data": versions})
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
# ... 引入 update_manager ...
from managers import db, update_manager, booklist_manager

# 1. 手动检查单本更新
@core_bp.route('/api/check_update', methods=['POST'])
@login_required
def api_check_update():
    # 前端传来的当前阅读 URL
    current_url = request.json.get('url') 
    book_key = request.json.get('key')
    
    if not current_url: return jsonify({"status": "error", "msg": "No URL"})

    try:
        # 1. 智能找目录 (优先缓存)
        toc_url = None
        cached_page = managers.cache.get(current_url)
        if cached_page and cached_page.get('toc_url'): 
            toc_url = cached_page['toc_url']
        else:
            page_data = crawler.run(current_url)
            if page_data: 
                toc_url = page_data.get('toc_url')
                managers.cache.set(current_url, page_data)

        if not toc_url: 
            # 兜底猜测
            toc_url = current_url.rsplit('/', 1)[0] + '/'

        # 2. 爬取最新章节 (这是手动检查，必须实时爬)
        latest_chap = crawler.get_latest_chapter(toc_url)
        
        if latest_chap:
            # 3. 保存最新信息到硬盘 (给自动轮询用)
            save_data = {
                "latest_title": latest_chap['title'],
                "latest_url": latest_chap['url'],
                "latest_id": latest_chap['id'],
                "toc_url": toc_url
            }
            managers.update_manager.set_update(book_key, save_data)
            
            # ===============================================
            # 4. [核心修复] 立即计算差值返回给前端
            # ===============================================
            
            # A. 获取当前阅读进度的 ID
            current_id = parse_chapter_id(current_url)
            # 如果 URL 里没 ID 或者是 _2.html，尝试正则提取
            if current_id <= 0:
                match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
                if match: current_id = int(match.group(1))

            latest_id = latest_chap['id']
            
            # 构造返回给前端的详细数据
            response_data = {
                "latest_title": latest_chap['title'],
                "latest_url": latest_chap['url'],
                "unread_count": 0,
                "status_text": "已最新"
            }

            # B. 执行减法
            if latest_id > 0 and current_id > 0:
                diff = latest_id - current_id
                if diff > 0:
                    response_data["unread_count"] = diff
                    response_data["status_text"] = f"落后 {diff} 章"
                else:
                    response_data["status_text"] = "已追平"
            elif current_url != latest_chap['url']:
                # ID 解析失败，回退到 URL 对比
                response_data["unread_count"] = 1
                response_data["status_text"] = "有新章节"

            return jsonify({
                "status": "success", 
                "msg": "刷新成功", 
                "data": response_data  # 把算好的数据传回去
            })
        else:
            return jsonify({"status": "failed", "msg": "目录解析失败"})

    except Exception as e:
        print(f"Check Update Error: {e}")
        return jsonify({"status": "error", "msg": str(e)})
# 2. 获取所有更新状态 (用于前端渲染小红点)
from spider_core import searcher, epub_handler, parse_chapter_id 

# =========================================================
# 核心接口 1：获取所有书的实时状态 (前端刷新/轮询调用)
# =========================================================
# routes/core_bp.py

# routes/core_bp.py

@core_bp.route('/api/updates/status')
@login_required
def api_get_updates_status():
    # 1. 寻找 target_books (只检查 to_read 书单里的书)
    all_lists = managers.booklist_manager.load()
    target_books = []
    
    # 关键字匹配书单名
    watch_keywords = ['to_read', '必读', '追更', 'reading', '在读']
    
    for list_data in all_lists.values():
        list_name = list_data.get('name', '').lower()
        if any(k in list_name for k in watch_keywords):
            target_books.extend(list_data.get('books', []))
            
    # 如果没有匹配的书单，为了兼容性，可以检查一下 status='want' 的书
    if not target_books:
        for list_data in all_lists.values():
            for book in list_data.get('books', []):
                if book.get('status') == 'want':
                    target_books.append(book)

    # 去重
    target_keys = list(set([b['key'] for b in target_books]))
    
    # 获取更新记录 (来自后台爬虫)
    updates_record = managers.update_manager.load()
    
    response_data = {}
    
    # 获取用户当前的阅读进度 (KV Store)
    user_progress = managers.db.list_all().get('data', {})

    print(f"\n[StatusCheck] 正在为 {len(target_keys)} 本追更书籍计算进度...")

    for key in target_keys:
        current_url = user_progress.get(key)
        if not current_url: continue

        # --- A. 获取当前阅读章节的 ID (最精准的方法：爬当前页) ---
        current_id = -1
        
        # 1. 先尝试从缓存读页面信息 (避免频繁爬取)
        cached_page = managers.cache.get(current_url)
        if cached_page and cached_page.get('title'):
            # 使用统一的 parse_chapter_id 函数
            current_id = parse_chapter_id(cached_page['title'])
        
        # 2. 如果缓存没有或没解析出ID，且不是频繁请求，才尝试爬取
        if current_id <= 0:
            # 这里为了性能，如果用户短时间内频繁刷新，我们不应该每次都去爬
            # 但为了准确性，我们假设用户已经阅读了新章节
            try:
                page_data = crawler.run(current_url)
                if page_data and page_data.get('title'):
                    managers.cache.set(current_url, page_data) # 顺手存缓存
                    current_id = parse_chapter_id(page_data['title'])
            except: pass
        
        # 3. 实在不行，回退到 URL 正则提取
        if current_id <= 0:
            match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
            if match: current_id = int(match.group(1))

        # --- B. 获取最新章节 ID (来自 update_manager) ---
        latest_info = updates_record.get(key)
        if not latest_info: continue

        latest_id = int(latest_info.get('latest_id', -1))
        # 如果 json 里存的 latest_id 无效，尝试从 latest_title 现算
        if latest_id <= 0 and latest_info.get('latest_title'):
             latest_id = parse_chapter_id(latest_info['latest_title'])

        # --- C. 计算差值 ---
        status_payload = {
            "unread_count": 0,
            "status_text": "已最新",
            "latest_title": latest_info.get('latest_title', '')
        }

        if latest_id > 0 and current_id > 0:
            diff = latest_id - current_id
            # 只有正向差距才算更新 (防止看番外导致 ID 变小)
            if diff > 0:
                status_payload['unread_count'] = diff
                status_payload['status_text'] = f"落后 {diff} 章"
            else:
                status_payload['status_text'] = "已追平"
        
        # 存入结果
        response_data[key] = status_payload

    return jsonify(response_data)
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