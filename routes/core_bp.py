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
        api_url="", app_version="1.1.3")
    except Exception as e:
        return f"Error loading index: {str(e)}", 500

@core_bp.route('/read')
@login_required
def read_mode():
    u, k = request.args.get('url'), request.args.get('key', '')
    force = request.args.get('force')
    
    # 1. 安全检查
    if not u.startswith('epub:') and not is_safe_url(u): 
        return "Illegal URL", 403
    
    data = None
    
    # 2. 获取数据 (放在 try 块中只负责获取)
    try:
        if u.startswith('epub:'):
            # EPUB 逻辑
            parts = u.split(':')
            filename = parts[1]
            
            if len(parts) >= 3 and parts[2] == 'toc':
                return redirect(url_for('core.toc_page', url=u, key=k))
            
            if len(parts) >= 4:
                identifier = parts[2]
                page_index = int(parts[3])
            else:
                identifier = parts[2]
                page_index = 0
            
            data = epub_handler.get_chapter_content(filename, identifier, page_index)
        else:
            # 网页逻辑
            data = managers.offline_manager.get_chapter(k, u) if k and not force else None
            if not data and not force: data = managers.cache.get(u)
            if not data:
                data = crawler.run(u)
                if data: managers.cache.set(u, data)

    except Exception as e:
        # 捕获爬虫内部的错误
        print(f"[Read Error] {e}")
        return f"解析发生错误: {str(e)}", 500

    # 3. [核心修复] 必须先判断 data 是否存在
    if not data:
        return render_template_string("""
            <div style="text-align:center; padding:50px;">
                <h3>无法获取章节内容</h3>
                <p>可能是源站连接超时，或该章节需要付费/登录。</p>
                <a href="javascript:history.back()">返回</a>
            </div>
        """), 404

    # 4. 后续处理 (此时 data 一定不为 None，可以安全调用 .get)
    try:
        # 记录历史
        if k and data.get('title'):
            managers.history_manager.add_record(k, data['title'], u, data.get('book_name'))

        # 计算 ID
        current_chapter_id = -1
        if data.get('title'):
            current_chapter_id = parse_chapter_id(data['title'])
        
        # 网页版 URL 兜底 ID
        if current_chapter_id <= 0 and not u.startswith('epub:'):
            match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', u.split('?')[0])
            if match: current_chapter_id = int(match.group(1))

        # 5. 渲染页面
        ua = request.headers.get('User-Agent', '').lower()
        is_mobile = any(x in ua for x in ['iphone', 'android', 'phone', 'mobile'])
        
        context = {
            'article': data,
            'current_url': u,
            'db_key': k,
            'chapter_id': current_chapter_id
        }

        if is_mobile:
            return render_template('reader_m.html', **context)
        else:
            return render_template('reader_pc.html', **context)
            
    except Exception as e:
        print(f"[Render Error] {e}")
        return f"渲染错误: {str(e)}", 500
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
    data = None
    
    # 网页逻辑
    # 如果 force 为 true，先不读缓存，也别让 crawler 读缓存
    # 但由于我们在 crawler.run 里强制加了读缓存逻辑，这里需要一点技巧：
    
    # 方案 A: 相信 crawler.run 的缓存机制 (推荐)
    # 我们需要让 crawler.run 知道我们要强制刷新。
    # 但这需要改动 crawler.run 的签名。
    
    # 方案 B (当前代码现状):
    # 既然我们在 crawler.run 里加了缓存检查，那么 routes 里的 managers.cache.get(u) 就可以删掉了？
    # 不完全是。为了兼容性，我们保留 routes 里的逻辑。
    
    # [关键]：如果你想让“强制刷新”生效，你需要在 crawler.run 之前手动清理一下缓存
    if force:
        try:
            # 删掉缓存文件，这样 crawler.run 内部 check cache 就会 miss，从而去远程爬
            from managers import cache
            cache_file = cache._get_filename(u)
            if os.path.exists(cache_file):
                os.remove(cache_file)
        except: pass
    
    if not data:
        data = crawler.get_toc(u)
        print("getting data", u)
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
    # final_value = raw_value
    # if is_manual:
        # 调用爬虫的智能解析
        # final_value = crawler.resolve_start_url(raw_value)
    
    # 保存纠错后的值
    final_value = raw_value
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
    
    # if current_id <= 0:
    #    return jsonify({"status": "error", "msg": "无法识别当前章节ID"})

    # === 搜索 ===
    print(f"[Switch] 准备搜索新源，关键词: {book_name}")
    # 改为直接返回搜索结果，不做耗时的验证
    from spider_core import searcher
    sources = searcher.search_bing_cached(book_name)
    
    if not sources:
        return jsonify({"status": "failed", "msg": "全网未找到相关书籍"})
        
    return jsonify({
        "status": "success", 
        "data": sources,
        # 回传上下文，供前端二次确认使用
        "match_info": {
            "current_id": current_id,
            "current_title": frontend_title
        }
    })

@core_bp.route('/api/source/confirm_switch', methods=['POST'])
@login_required
def api_confirm_switch():
    data = request.json
    target_url = data.get('target_url')
    current_id = data.get('current_id', -1)
    current_title = data.get('current_title', '')
    
    if not target_url: return jsonify({"status": "error", "msg": "Target URL missing"})

    new_url = crawler.find_best_match(target_url, current_id, current_title)
    
    if new_url:
        return jsonify({"status": "success", "new_url": new_url})
    else:
        return jsonify({"status": "failed", "msg": "无法解析目标源"})

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
@core_bp.route('/api/booklists/update_book', methods=['POST'])
@login_required
def api_booklists_update():
    d = request.json
    managers.booklist_manager.update_status(
        d.get('list_id'), 
        d.get('book_key'), 
        d.get('status'), 
        d.get('action')
    )
    # 必须返回最新的 data，因为前端 updateBookStatus 依赖它来刷新页面
    return jsonify({"status": "success", "data": managers.booklist_manager.load()})

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
    # 前端传来的当前阅读 URL 和 Key
    current_url = request.json.get('url') 
    book_key = request.json.get('key')
    
    if not current_url: return jsonify({"status": "error", "msg": "No URL"})

    try:
        # === 1. 智能定位目录页 URL ===
        toc_url = None
        
        # 优先从缓存的“当前阅读页”信息中找目录链接
        cached_page = managers.cache.get(current_url)
        if cached_page and cached_page.get('toc_url'): 
            toc_url = cached_page['toc_url']
        else:
            # 缓存未命中目录链接，尝试爬取当前页获取
            try:
                page_data = crawler.run(current_url)
                if page_data: 
                    toc_url = page_data.get('toc_url')
                    managers.cache.set(current_url, page_data)
            except: pass

        # 兜底猜测
        if not toc_url: 
            toc_url = current_url.rsplit('/', 1)[0] + '/'

        # === 2. [核心] 强制清除目录缓存 ===
        # 确保触发爬虫联网获取最新数据（章节、封面、标签等）
        try:
            from managers import cache
            cache_file = cache._get_filename(toc_url)
            if os.path.exists(cache_file):
                os.remove(cache_file)
                print(f"[Update] 强制刷新，已清理缓存: {toc_url}")
        except Exception as e:
            print(f"[Update] 清理缓存失败: {e}")

        # === 3. 爬取最新目录和元数据 ===
        toc_data = crawler.get_toc(toc_url)
        
        if toc_data and toc_data.get('chapters'):
            # 获取最新章节对象
            latest_chap = toc_data['chapters'][-1]
            
            # === 4. 更新数据库元数据 (封面/作者/简介/标签) ===
            update_payload = {}
            if toc_data.get('cover'): update_payload['cover'] = toc_data['cover']
            if toc_data.get('author'): update_payload['author'] = toc_data['author']
            if toc_data.get('desc'): update_payload['desc'] = toc_data['desc']
            # [新增] 保存官方标签
            if toc_data.get('tags'): update_payload['official_tags'] = toc_data['tags']
            
            if update_payload:
                print(f"[Update] 更新书籍元数据: {book_key} -> {list(update_payload.keys())}")
                managers.db.update(book_key, update_payload)

            # === 5. 更新追更管理器 (UpdateManager) ===
            save_data = {
                "latest_title": latest_chap.get('title') or latest_chap.get('name'),
                "latest_url": latest_chap['url'],
                "latest_id": latest_chap.get('id', -1),
                "toc_url": toc_url
            }
            managers.update_manager.set_update(book_key, save_data)
            
            # === 6. 计算进度差值 (返回给前端) ===
            
            # A. 获取当前阅读章节 ID
            current_id = parse_chapter_id(current_url)
            if current_id <= 0:
                match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
                if match: current_id = int(match.group(1))
            
            latest_id = save_data['latest_id']
            
            # 构造返回数据
            response_data = {
                "latest_title": save_data['latest_title'],
                "latest_url": save_data['latest_url'],
                "unread_count": 0,
                "status_text": "已最新"
            }

            # B. 执行比对
            if latest_id > 0 and current_id > 0:
                diff = latest_id - current_id
                if diff > 0:
                    response_data["unread_count"] = diff
                    response_data["status_text"] = f"落后 {diff} 章"
                else:
                    response_data["status_text"] = "已追平"
            elif current_url != save_data['latest_url']:
                response_data["unread_count"] = 1
                response_data["status_text"] = "有新章节"

            return jsonify({
                "status": "success", 
                "msg": "刷新成功", 
                "data": response_data 
            })
        else:
            return jsonify({"status": "failed", "msg": "目录解析失败"})

    except Exception as e:
        print(f"Check Update Error: {e}")
        return jsonify({"status": "error", "msg": str(e)})
# 2. 获取所有更新状态 (用于前端渲染小红点)
from spider_core import searcher, epub_handler, parse_chapter_id 

# =========================================================
# 核心接口：获取所有书的实时状态
# 重构说明：
# 1. Modern Path: 优先读取 update_sub_manager (SQLite),这是后台自动追更的结果
# 2. Legacy Path: 如果没订阅，回退读取 update_manager (JSON),这是旧版爬虫的结果
# =========================================================

@core_bp.route('/api/updates/status', methods=['GET'])
@login_required
def api_get_updates_status():
    # --- 1. 确定检查范围 ---
    # (只检查 to_read/必读/追更 等书单里的书，避免全库扫描性能爆炸)
    all_lists = managers.booklist_manager.load()
    target_books = []
    
    watch_keywords = ['to_read', '必读', '追更', 'reading', '在读']
    
    for list_data in all_lists.values():
        list_name = list_data.get('name', '').lower()
        if any(k in list_name for k in watch_keywords):
            target_books.extend(list_data.get('books', []))
            
    # 如果没找到特定书单，兜底检查所有标记为 'want' 的书
    if not target_books:
        for list_data in all_lists.values():
            for book in list_data.get('books', []):
                if book.get('status') == 'want':
                    target_books.append(book)

    target_keys = list(set([b['key'] for b in target_books]))
    
    # [核心修复] 必须包含所有“已手动订阅”的书！
    # 无论这本书在不在书单里，只要用户点了“追更”，就必须检查
    username = session.get('user', {}).get('username')
    try:
        subscribed_keys = managers.update_sub_manager.get_all_subscribed(username)
        target_keys.extend(subscribed_keys)
        # 再次去重
        target_keys = list(set(target_keys))
        # print(f"[DEBUG] 检查列表: {target_keys}")
    except Exception as e:
        print(f"[Updates] 获取订阅列表失败: {e}")

    # 获取用户进度
    user_progress = managers.db.list_all().get('data', {})
    
    # 预加载旧版数据 (Legacy Data Source)
    legacy_records = managers.update_manager.load()
    
    response_data = {}

    for key in target_keys:
        # === Step 1: 获取用户当前进度 (Common Logic) ===
        val_obj = user_progress.get(key)
        
        # 提取当前阅读链接
        current_url = ""
        if isinstance(val_obj, dict): current_url = val_obj.get('url', '')
        elif isinstance(val_obj, str): current_url = val_obj
        if not current_url: continue

        # 计算当前章节 ID (Current ID)
        current_id = -1
        # cached_page = managers.cache.get(current_url) 
        
        match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
        if match: current_id = int(match.group(1))

        if current_id <= 0: continue 

        # === Step 2: 获取最新章节信息 (Logic Branching) ===
        latest_id = -1
        latest_title = ""
        data_source = "none" 

        # --- A. Modern Path (新逻辑: SQLite) ---
        sub_status = managers.update_sub_manager.get_book_status(key)
        
        # [关键判定] 只要 subscribed 且 remote_id > 0，就采信
        if sub_status and sub_status.get('subscribed') and sub_status.get('remote_id', 0) > 0:
            latest_id = sub_status['remote_id']
            latest_title = "最新章节" 
            data_source = "modern_sql"
        
        # --- B. Legacy Path (旧逻辑: JSON) ---
        if latest_id <= 0:
            legacy_info = legacy_records.get(key)
            if legacy_info:
                lid = int(legacy_info.get('latest_id', -1))
                if lid <= 0 and legacy_info.get('latest_title'):
                    lid = parse_chapter_id(legacy_info['latest_title'])
                
                if lid > 0:
                    latest_id = lid
                    latest_title = legacy_info.get('latest_title', '')
                    data_source = "legacy_json"

        # === Step 3: 计算更新 (Payload Construction) ===
        status_payload = {
            "unread_count": 0,
            "status_text": "已最新",
            "latest_title": latest_title,
            "debug_source": data_source
        }

        if latest_id > 0:
            diff = latest_id - current_id
            if diff > 0:
                status_payload['unread_count'] = diff
                status_payload['status_text'] = f"落后 {diff} 章"
            else:
                status_payload['status_text'] = "已追平"
        
        response_data[key] = status_payload

    return jsonify(response_data)

# =========================================================

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

# === 追更 API ===

import threading # 确保导入
@core_bp.route('/api/updates/subscribe', methods=['POST'])
@login_required
def api_subscribe():
    username = session.get('user', {}).get('username')
    data = request.json
    key = data.get('key')
    enable = data.get('enable')
    toc_url = data.get('toc_url')
    
    current_id = data.get('current_id', 0)

    if enable:
        # 1. [修复] 提前在主线程预取数据，防止子线程 Context 丢失
        user_db_val = None
        try:
            user_db_val = managers.db.get_val(key)
        except: pass

        managers.update_sub_manager.subscribe(username, key, toc_url, current_id)
        
        def _instant_check(pre_fetched_val):
            print(f"[Instant Check] ⚡ 用户手动订阅 {key}，正在立即检查更新...")
            try:
                # 0. [核心新增] 强力清除目录页缓存 (无论爬虫怎么想，物理删除缓存文件)
                try:
                    from managers import cache
                    cache_file = cache._get_filename(toc_url)
                    if os.path.exists(cache_file):
                        # 检查一下文件最后修改时间，如果是1分钟内生成的，可能没必要删
                        # 但为了保证“立即检查”的承诺，还是删了好
                        os.remove(cache_file)
                        print(f"[Instant Check] 已强制清理TOC缓存: {toc_url}")
                except Exception as e:
                    print(f"[Instant Check] 清理缓存失败(可能文件被占用): {e}")

                # =========================================================
                # 核心逻辑修正：对比基准应该是 [本地缓存TOC的最后一章]
                # 而不是 [用户当前的阅读进度]
                # =========================================================

                # --- 1. 获取本地已知进度 (Local Knowledge) ---
                local_seq = -1
                local_title = "未知"
                
                # 策略A (最准确)：读取本地缓存的目录文件的最后一章
                cached_toc = managers.cache.get(toc_url)
                if cached_toc and cached_toc.get('chapters'):
                    local_last_chap = cached_toc['chapters'][-1]
                    local_title = local_last_chap.get('title', '')
                    local_seq = parse_chapter_id(local_title)
                    # 如果标题解析失败，尝试用原始ID (针对番茄等特殊源)
                    if local_seq == -1 and 'id' in local_last_chap:
                        # 注意：这里如果是番茄的长ID，后面会在比较环节处理
                        pass 
                    
                    print(f"[Check] 本地缓存TOC命中: 最后一章 {local_title} -> {local_seq}")

                # 策略B (兜底)：如果完全没有TOC缓存，才退化为使用阅读进度
                # (场景：刚加书架还没点开过目录，或者缓存被清空)
                if local_seq == -1:
                    print(f"[Check] 本地无TOC缓存，尝试使用阅读进度作为基准...")
                    current_reading_url = None
                    if isinstance(pre_fetched_val, dict):
                        current_reading_url = pre_fetched_val.get('url')
                    elif isinstance(pre_fetched_val, str):
                        current_reading_url = pre_fetched_val
                    
                    if current_reading_url:
                        cached_chap = managers.cache.get(current_reading_url)
                        if cached_chap and cached_chap.get('title'):
                            local_title = cached_chap['title']
                            local_seq = parse_chapter_id(local_title)
                            print(f"[Check] 阅读进度兜底: {local_title} -> {local_seq}")
                
                # --- 2. 获取远程进度 (Remote) ---
                latest_data = crawler.get_latest_chapter(toc_url, no_cache=True)
                remote_seq = -1
                remote_title = "未知"
                remote_id = -1
                
                if latest_data:
                    remote_title = latest_data.get('title', '')
                    remote_id = latest_data.get('id')
                    remote_seq = parse_chapter_id(remote_title)
                    if remote_seq == -1 and isinstance(latest_data.get('id'), int):
                         remote_seq = latest_data['id']
                    
                    # [核心修复] 决定入库的 ID
                    # 如果能解析出序号(如 1704)，必须存序号，否则会导致前端计算出几亿的差值
                    # 只有解析失败时，才存原始 ID
                    id_to_save = remote_seq if remote_seq > 0 else remote_id;
                    
                    print(f"[Check] 远程获取成功: {remote_title} -> 序号 {remote_seq} (原始ID: {remote_id})")
                else:
                    return

                # --- 3. 核心比对 ---
                print(f"[Check] 最终比对: Local({local_seq}) vs Remote({remote_seq})")
                
                has_update = False
                
                # A. 序号比对 (最优先)
                if local_seq > 0 and remote_seq > 0:
                    if remote_seq > local_seq:
                        has_update = True
                
                # B. 标题比对 (兜底，防止序号解析失败)
                # 只有当本地已经有一定的数据(local_seq != -1)才对比，否则刚加书架没缓存全报更新也不太对
                elif local_title != "未知" and local_title != remote_title:
                     has_update = True
                     # 如果是番茄源长ID场景，可能走到这里
                     print(f"[Check] 标题/ID 变动触发更新: {local_title} != {remote_title}")

                if has_update:
                     # [修复] 传入 id_to_save 而不是 remote_id
                     managers.update_sub_manager.update_status(key, id_to_save, True)
                     print(f"✅ 发现更新 (存入ID: {id_to_save})")
                else:
                     # 关键：如果没有更新，也要更新一下 update_sub_manager 里的 last_check_time 和 latest_id
                     # 这样前端可以显示“刚刚检查过”
                     # [修复] 传入 id_to_save 而不是 remote_id
                     managers.update_sub_manager.update_status(key, id_to_save, False)
                     print(f"💤 无更新 (已同步状态, 存入ID: {id_to_save})")

            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[Instant Check] 失败: {e}")

        threading.Thread(target=_instant_check, args=(user_db_val,), daemon=True).start()

        return jsonify({"status": "success", "msg": "已开启追更，正在后台立即检查..."})
    else:
        managers.update_sub_manager.unsubscribe(key)
        return jsonify({"status": "success", "msg": "已取消追更"})

@core_bp.route('/api/updates/status', methods=['POST'])
@login_required
def api_updates_status():
    """返回给定 key 的追更状态，包含是否有红点"""
    key = request.json.get('key')
    # [修改] 调用新方法获取详细信息
    status = managers.update_sub_manager.get_book_status(key)
    return jsonify({
        "status": "success", 
        "subscribed": status['subscribed'],
        "has_update": status['has_update'] # 告诉前端有没有新章节
    })

@core_bp.route('/api/updates/all_red_dots')
@login_required
def api_all_red_dots():
    """首页用：一次性返回所有有红点的 book_key"""
    username = session.get('user', {}).get('username')
    keys = managers.update_sub_manager.get_all_updates(username)
    return jsonify({"status": "success", "data": keys})