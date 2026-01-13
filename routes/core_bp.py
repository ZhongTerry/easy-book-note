from flask import Blueprint, render_template_string, request, jsonify, send_file, render_template, redirect, url_for, send_from_directory, session
import requests
import os
from shared import login_required, is_safe_url, BASE_DIR, DL_DIR
import managers
from spider_core import crawler_instance as crawler, searcher, epub_handler, parse_chapter_id
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
def calculate_real_chapter_id(book_key, chapter_url, chapter_title):
    """
    只通过标题识别真实序号。
    如果识别不到，返回 -1，不再尝试从 URL 瞎猜。
    """
    if chapter_url.startswith('epub:'):
        return -1
    # 策略 A: 标题解析 (使用我们刚刚修好的增强版函数)
    title_id = parse_chapter_id(chapter_title)
    if title_id > 0:
        return title_id
    
    # 策略 B: 严格模式下，我们不再从 URL 正则提取 ID，
    # 因为 URL ID 往往是网站数据库的 ID (如 5882.html)，而不是第几章。
    # 如果你确定某些网站 URL 就是章节号，可以保留，但目前为了防误报，建议关闭。
    
    return -1
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
from spider_core import parse_chapter_id
# [新增] 解析页码的辅助函数
def get_page_index(url):
    """从 URL 解析页码 (例如 123_2.html -> 2, 123.html -> 1)"""
    try:
        # 匹配 _2.html 这种格式
        match = re.search(r'_(\d+)\.', url)
        if match:
            return int(match.group(1))
    except: pass
    return 1 # 默认是第 1 页
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
def index():
    # 旧代码: return send_file(os.path.join(BASE_DIR, 'index.html'))
    
    # === [新代码] 读取文件并当做模板渲染 ===
    try:
        # index_path = os.path.join(BASE_DIR, 'index.html')
        # with open(index_path, 'r', encoding='utf-8') as f:
        #     html_content = f.read()
        
        # 这里的 render_template_string 会自动接收 context_processor 注入的 app_version
        return render_template("index.html", 
        api_url="")
    except Exception as e:
        return f"Error loading index: {str(e)}", 500

@core_bp.route('/read')
@login_required
def read_mode():
    u, k = request.args.get('url'), request.args.get('key', '')
    force = request.args.get('force')
    
    # 1. 安全检查 (放行 epub 协议)
    if not u.startswith('epub:') and not is_safe_url(u): 
        return "Illegal URL", 403
    
    data = None
    
    # 2. 获取数据 (EPUB 或 网页)
    try:
        if u.startswith('epub:'):
            # EPUB 处理逻辑
            parts = u.split(':')
            filename = parts[1]
            
            # 如果是目录请求，跳转到 TOC
            if len(parts) >= 3 and parts[2] == 'toc':
                return redirect(url_for('core.toc_page', url=u, key=k))
            
            # 解析标识符和页码
            if len(parts) >= 4:
                identifier = parts[2]
                page_index = int(parts[3])
            else:
                identifier = parts[2]
                page_index = 0
            
            data = epub_handler.get_chapter_content(filename, identifier, page_index)
        else:
            # 网页爬虫逻辑
            data = managers.offline_manager.get_chapter(k, u) if k and not force else None
            if not data and not force: data = managers.cache.get(u)
            if not data:
                data = crawler.run(u)
                if data: managers.cache.set(u, data)
    except Exception as e:
        return f"解析错误: {e}", 500

    if not data:
        return "无法获取内容，请检查链接或稍后重试", 404

    # 3. 记录历史 & 计算章节 ID
    if k and data.get('title'):
        managers.history_manager.add_record(k, data['title'], u, data.get('book_name'))

    current_chapter_id = -1
    if data.get('title'):
        current_chapter_id = parse_chapter_id(data['title'])
    
    # 如果标题解析失败，尝试从 URL 提取 (仅针对网页)
    if current_chapter_id <= 0 and not u.startswith('epub:'):
        match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', u.split('?')[0])
        if match: current_chapter_id = int(match.group(1))

    # 4. === [核心修改] 设备分流逻辑 ===
    ua = request.headers.get('User-Agent', '').lower()
    is_mobile = any(x in ua for x in ['iphone', 'android', 'phone', 'mobile'])
    
    # 统一的模板上下文变量
    context = {
        'article': data,
        'current_url': u,
        'db_key': k,
        'chapter_id': current_chapter_id
    }

    if is_mobile:
        # 手机端 -> 渲染 reader_m.html (一定要确保这个文件在 templates 里)
        return render_template('reader_m.html', **context)
    else:
        # 电脑端 -> 渲染 reader_pc.html
        return render_template('reader_pc.html', **context)
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
    # 接收 force 参数，如果是 'true' 则跳过缓存
    force = request.args.get('force') == 'true'
    is_api = request.args.get('api')
    if u.startswith('epub:'):
        # 协议格式：epub:文件名:索引 (例如 epub:test.epub:toc)
        parts = u.split(':')
        filename = parts[1]
        data = epub_handler.get_toc(filename)
        
        if not data:
            return "EPUB 目录解析失败", 404
            
        if is_api:
            return jsonify(data)
        return render_template('toc.html', toc=data, toc_url=u, db_key=k)
    data = None if force else managers.cache.get(u)
    
    if not data:
        data = crawler.get_toc(u)
        if data:
            managers.cache.set(u, data)
    
    if is_api:
        return jsonify(data if data else {"status": "error", "message": "无法获取目录"})

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
    raw_value = request.json.get('value') # 原始输入
    is_manual = request.json.get('manual', False)
    
    # [核心修改] 智能纠错
    # 只有在手动输入时才尝试纠错，自动同步时不纠错(节省性能)
    final_value = raw_value
    if is_manual:
        # 调用爬虫的智能解析
        final_value = crawler.resolve_start_url(raw_value)
    
    # 保存纠错后的值
    res = managers.db.insert(key, final_value)
    
    if is_manual and res.get('status') == 'success':
        managers.db.add_version(key, final_value)
        
    return jsonify(res)

import time
@core_bp.route('/update', methods=['POST'])
@login_required
def update():
    key = request.json.get('key')
    value = request.json.get('value')
    title = request.json.get('title', '') 
    is_manual = request.json.get('manual', False)

    # 1. 保存 URL (这是基础 KV 记录)
    final_value = value
    if is_manual and hasattr(crawler, 'resolve_start_url'):
        final_value = crawler.resolve_start_url(value)
    
    res = managers.db.update(key, final_value)

    # 2. 【核心修改点】计算并保存序号
    real_id = calculate_real_chapter_id(key, final_value, title)
    
    # 只有当 real_id 是有效正整数时才更新 meta
    # 如果返回 -1 (未识别)，这里直接跳过，数据库里旧的 meta 会保留
    if real_id > 0:
        try:
            import json
            # 获取旧 meta
            old_meta_str = managers.db.get_val(f"{key}:meta")
            meta = json.loads(old_meta_str) if old_meta_str else {}
            
            # 更新序号和时间戳
            meta['chapter_id'] = real_id
            meta['updated_at'] = int(time.time())
            
            managers.db.update(f"{key}:meta", json.dumps(meta))
            # print(f"[Sync] 识别成功：{title} -> ID {real_id}")
        except Exception as e:
            print(f"[Sync] Meta save error: {e}")
    else:
        # 如果没识别到，打印一个日志方便调试，但不写库
        print(f"[Sync] ⚠️ 章节识别失败，跳过 Meta 记录: {title}")

    # 3. 历史版本 (仅手动)
    if is_manual and res.get('status') == 'success':
        managers.db.add_version(key, final_value)
    
    return jsonify(res)
# routes/core_bp.py

@core_bp.route('/api/rename_key', methods=['POST'])
@login_required
def api_rename_key():
    old_key = request.json.get('old_key')
    new_key = request.json.get('new_key')
    
    if not old_key or not new_key:
        return jsonify({"status": "error", "message": "参数不足"})
    
    # 调用刚才在 managers 里写的逻辑
    res = managers.db.rename_key(old_key, new_key)
    return jsonify(res)
@core_bp.route('/api/switch_source', methods=['POST'])
@login_required
def api_switch_source():
    current_url = request.json.get('url')
    book_key = request.json.get('key')
    
    if not current_url or not book_key:
        return jsonify({"status": "error", "msg": "Missing params"})

    try:
        # 1. 获取当前书名 (从书单或缓存拿，或者重新爬当前页)
        # 为了准确，我们先尝试从缓存拿当前页信息
        book_name = ""
        current_id = -1
        
        cached_page = managers.cache.get(current_url)
        if cached_page:
            # 尝试从页面标题提取书名 (通常格式: 第xx章 标题 - 书名 - 网站名)
            # 这步比较难，如果缓存里没存书名，我们只能用 SearchHelper 的 key 反推或者让前端传
            # 简单起见，我们用 key (拼音) 去书单里反查书名，或者让用户前端传书名
            pass
            
        # 更好的方案：前端传 book_title 过来。
        # 如果前端没传，我们去书单管理器里查这个 key 对应的书名
        book_name = request.json.get('title')
        if not book_name:
             # 尝试从书单反查
             all_lists = managers.booklist_manager.load()
             for lid, ldata in all_lists.items():
                 for book in ldata.get('books', []):
                     if book['key'] == book_key:
                         book_name = book['title']
                         break
                 if book_name: break
        
        if not book_name:
            return jsonify({"status": "error", "msg": "无法获取书名，请先将书加入书单"})

        # 2. 获取当前章节 ID
        if cached_page and cached_page.get('title'):
             current_id = parse_chapter_id(cached_page['title'])
        
        if current_id <= 0:
             # 尝试正则
             import re
             match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
             if match: current_id = int(match.group(1))

        if current_id <= 0:
            return jsonify({"status": "error", "msg": "无法识别当前章节ID"})

        # 3. 执行换源
        result = crawler.search_and_switch_source(book_name, current_id)
        
        if result:
            # 找到新源了！
            new_url = result['new_url']
            
            # 4. 更新数据库 (无缝衔接)
            managers.db.update(book_key, new_url)
            
            # 5. 顺便更新下缓存 (预热)
            # threading.Thread(target=crawler.run, args=(new_url,)).start()
            
            return jsonify({
                "status": "success", 
                "new_url": new_url,
                "msg": f"已切换至: {result['source_name']}"
            })
        else:
            return jsonify({"status": "failed", "msg": "全网未找到该章节的其他源"})

    except Exception as e:
        print(f"Switch Error: {e}")
        return jsonify({"status": "error", "msg": str(e)})
    
# @core_bp.route('/api/source/list', methods=['POST'])
# @login_required
@core_bp.route('/api/source/list', methods=['POST'])
@login_required
def api_source_list():
    current_url = request.json.get('url')
    book_key = request.json.get('key')
    frontend_title = request.json.get('title', '') # 章节标题
    
    if not current_url: return jsonify({"status": "error", "msg": "参数错误"})

    # === 核心逻辑：多级探测真实书名 ===
    book_name = None
    
    # 1. 尝试从书单反查 (用户定义的标题最优先)
    all_lists = managers.booklist_manager.load()
    for list_data in all_lists.values():
        for b in list_data.get('books', []):
            if b['key'] == book_key:
                book_name = b['title']
                break
        if book_name: break

    # 2. 【关键补丁】如果书单没找到，直接“现场爬取”当前阅读页提取书名
    if not book_name or re.match(r'^[a-zA-Z0-9_]+$', book_name):
        print(f"[Switch] 无法从本地获取书名，正在现场爬取源站: {current_url}")
        try:
            # 现场爬取当前页面内容
            # 注意：这里 run 会自动识别是走插件还是走通用逻辑
            temp_data = crawler.run(current_url)
            if temp_data and temp_data.get('book_name'):
                book_name = temp_data['book_name']
                print(f"[Switch] 🎯 现场抓取书名成功: {book_name}")
        except Exception as e:
            print(f"[Switch] 现场抓取书名失败: {e}")

    # 3. 最终校验
    # 如果还是拿不到中文（全是字母数字），说明真的没法搜
    if not book_name or re.match(r'^[a-zA-Z0-9_]+$', str(book_name)):
        return jsonify({
            "status": "error", 
            "msg": f"无法识别书名(当前:{book_name})。建议手动将本书加入书单并填写中文书名。"
        })

    # === 获取当前章节 ID ===
    current_id = parse_chapter_id(frontend_title)
    if current_id <= 0:
         match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
         if match: current_id = int(match.group(1))
    
    if current_id <= 0:
        return jsonify({"status": "error", "msg": "无法识别当前章节ID"})

    # === 搜索并比对 ===
    print(f"[Switch] 准备搜索新源，关键词: {book_name}, 目标章节: {current_id}")
    sources = crawler.search_alternative_sources(book_name, current_id)
    
    if not sources:
        return jsonify({"status": "failed", "msg": "全网未找到包含该章节的其他源"})
        
    return jsonify({"status": "success", "data": sources})
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
    key = request.json.get('key')
    v = managers.db.get_val(key)
    
    if v:
        # 直接读取 meta，不再进行任何爬虫或解析
        meta_str = managers.db.get_val(f"{key}:meta")
        meta = {}
        if meta_str:
            try: 
                import json
                meta = json.loads(meta_str) 
            except: pass
        
        return jsonify({
            "status": "success", 
            "value": v,
            "meta": meta # 这里面包含准确的 chapter_id
        })
        
    return jsonify({"status": "error"})

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