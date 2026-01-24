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

def detect_page_type(data):
    """
    智能检测页面类型（增强版：优先使用适配器标记）
    返回: 'toc' (目录页), 'chapter' (章节页), 'unknown' (无法判断)
    """
    if not data or not isinstance(data, dict):
        return 'unknown'
    
    # === [优先级1] 适配器明确标记 ===
    if 'page_type' in data:
        declared_type = data['page_type']
        if declared_type in ('toc', 'chapter'):
            print(f"[Smart Detect] 适配器声明类型: {declared_type}")
            return declared_type
    
    # === [优先级2] 数据结构特征检测 ===
    # 检查是否有 chapters 列表（典型的目录页特征）
    chapters = data.get('chapters', [])
    if isinstance(chapters, list) and len(chapters) > 3:  # 至少3章才算目录
        print(f"[Smart Detect] 发现 {len(chapters)} 个章节 → 判定为目录页")
        return 'toc'
    
    # 检查是否有 content（典型的章节页特征）
    content = data.get('content')
    
    # 如果 content 是列表且包含有效内容
    if isinstance(content, list):
        # [优化] 过滤掉空字符串和失败信息
        valid_lines = [line for line in content if line and '提取失败' not in line and '无法获取' not in line and '获取失败' not in line]
        if len(valid_lines) > 3:  # [修复] 降低阈值，只要3行就认为是章节
            total_length = sum(len(line) for line in valid_lines)
            if total_length > 100:  # [修复] 降低阈值，100字符就够了
                print(f"[Smart Detect] 发现 {len(valid_lines)} 行有效内容 (共{total_length}字符) → 判定为章节页")
                return 'chapter'
        # [修复] 如果只有1-2行，也可能是章节页（特别短的章节或失败信息）
        # 不要直接判定为目录页，继续检查其他特征
    
    # 如果 content 是字符串
    if isinstance(content, str):
        if '提取失败' in content or '无法获取' in content or '获取失败' in content:
            # [修复] 失败信息不一定是目录页，继续检查其他特征
            print(f"[Smart Detect] 检测到失败信息，继续检查...")
        elif len(content) > 100:  # [修复] 降低阈值
            # 有足够长的内容，可能是章节页
            print(f"[Smart Detect] 内容长度 {len(content)} → 判定为章节页")
            return 'chapter'
    
    # 如果有 next_url, prev_url 等章节导航，很可能是章节页
    # 但要排除指向 index.html 的情况（那是目录链接）
    next_url = data.get('next_url') or data.get('next') or ''
    prev_url = data.get('prev_url') or data.get('prev') or ''
    
    if (next_url and 'index.html' not in next_url) or (prev_url and 'index.html' not in prev_url):
        print(f"[Smart Detect] 发现章节导航链接 → 判定为章节页")
        return 'chapter'
    
    # [新增] 如果有 toc_url 字段，说明这是从章节页提取的
    if data.get('toc_url'):
        print(f"[Smart Detect] 发现 toc_url 字段 → 判定为章节页")
        return 'chapter'
    
    print(f"[Smart Detect] 无法判断页面类型")
    return 'unknown'

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
# ...existing code...

@core_bp.route('/api/memos', methods=['GET'])
@login_required
def api_get_memos():
    """获取所有备忘录"""
    username = session.get('user', {}).get('username')
    memos = managers.memo_manager.get_all_memos(username)
    response = jsonify({"status": "success", "data": memos})
    # 禁用缓存
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@core_bp.route('/api/memos/<int:memo_id>', methods=['GET'])
@login_required
def api_get_memo(memo_id):
    """获取单条备忘录"""
    memo = managers.memo_manager.get_memo(memo_id)
    if memo:
        response = jsonify({"status": "success", "data": memo})
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    return jsonify({"status": "error", "message": "备忘录不存在"}), 404

@core_bp.route('/api/memos/save', methods=['POST'])
@login_required
def api_save_memo():
    """保存备忘录（支持实时自动保存）"""
    username = session.get('user', {}).get('username')
    data = request.json
    
    memo_id = managers.memo_manager.save_memo(
        username=username,
        memo_id=data.get('id'),
        title=data.get('title'),
        content=data.get('content'),
        tags=data.get('tags')
    )
    
    return jsonify({"status": "success", "memo_id": memo_id})

@core_bp.route('/api/memos/<int:memo_id>', methods=['DELETE'])
@login_required
def api_delete_memo(memo_id):
    """删除备忘录"""
    managers.memo_manager.delete_memo(memo_id)
    return jsonify({"status": "success"})

@core_bp.route('/api/memos/<int:memo_id>/pin', methods=['POST'])
@login_required
def api_toggle_pin(memo_id):
    """置顶/取消置顶"""
    managers.memo_manager.toggle_pin(memo_id)
    return jsonify({"status": "success"})

@core_bp.route('/api/memos/search', methods=['GET'])
@login_required
def api_search_memos():
    """搜索备忘录"""
    username = session.get('user', {}).get('username')
    keyword = request.args.get('q', '')
    memos = managers.memo_manager.search_memos(username, keyword)
    return jsonify({"status": "success", "data": memos})
@core_bp.route('/memo', methods=['GET'])
@login_required
def memo_page():
    """备忘录主页面"""
    response = render_template("memo.html")
    # 禁用 HTML 页面缓存
    return response, 200, {
        'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
        'Pragma': 'no-cache',
        'Expires': '0'
    }
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

@core_bp.route('/search')
@login_required
def search_page():
    """独立搜索页面"""
    return render_template("search.html")

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

    # 3. [智能检测] 如果获取的内容实际上是目录页，自动跳转到目录页
    if data and not u.startswith('epub:'):
        page_type = detect_page_type(data)
        if page_type == 'toc':
            print(f"[Smart Redirect] 检测到章节URL返回了目录内容，重定向到目录页: {u}")
            # 重定向到目录页，保持 key 参数
            return redirect(url_for('core.toc_page', url=u, key=k))
    
    # 4. [核心修复] 必须先判断 data 是否存在
    if not data:
        return render_template_string("""
            <!DOCTYPE html>
            <html><head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>解析失败</title>
                <style>
                    body { font-family: -apple-system, sans-serif; text-align:center; padding:50px; background:#f9fafb; }
                    .error-box { max-width:500px; margin:0 auto; background:white; padding:40px; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.1); }
                    h2 { color:#ef4444; margin-bottom:15px; }
                    p { color:#6b7280; line-height:1.6; margin-bottom:20px; }
                    .tips { text-align:left; background:#fef3c7; padding:15px; border-radius:8px; margin-top:20px; font-size:14px; color:#92400e; }
                    .btn { display:inline-block; padding:10px 20px; background:#4f46e5; color:white; text-decoration:none; border-radius:6px; margin-top:15px; }
                    .btn:hover { background:#4338ca; }
                    .debug { margin-top:20px; padding:15px; background:#f3f4f6; border-radius:8px; text-align:left; font-size:12px; color:#6b7280; overflow-wrap:break-word; }
                </style>
            </head><body>
                <div class="error-box">
                    <h2>🚫 内容提取失败</h2>
                    <p>可能原因：</p>
                    <ul style="text-align:left; color:#6b7280; line-height:1.8;">
                        <li>源站连接超时或暂时不可用</li>
                        <li>该章节需要登录或付费才能阅读</li>
                        <li>网站结构变动，解析规则需要更新</li>
                        <li>被反爬虫机制拦截</li>
                    </ul>
                    <div class="tips">
                        <strong>💡 解决建议：</strong><br>
                        1. 返回目录尝试其他章节<br>
                        2. 稍后重试，或检查源站是否正常<br>
                        3. 考虑更换书源（在搜索页重新搜索该书）
                    </div>
                    <a href="javascript:history.back()" class="btn">← 返回上一页</a>
                    <div class="debug">
                        <strong>调试信息：</strong><br>
                        URL: {{ url }}<br>
                        Key: {{ key }}
                    </div>
                </div>
            </body></html>
        """, url=u, key=k), 404

    # 5. 后续处理 (此时 data 一定不为 None，可以安全调用 .get)
    try:
        # [优化] 记录历史前先检测页面类型，目录页不记录
        if k and data.get('title'):
            # 检测页面类型，只有章节页才记录历史
            page_type = detect_page_type(data)
            if page_type != 'toc':  # 只记录章节页，不记录目录页
                # [关键修复] 检查 key 是否存在于数据库，避免记录不存在的 key
                db_value = managers.db.find(k)
                if db_value and db_value.get('status') == 'success':
                    # key 存在，记录历史
                    managers.history_manager.add_record(k, data['title'], u, data.get('book_name'))
                else:
                    print(f"[History] 跳过不存在的 key: {k}")
            else:
                print(f"[History] 跳过目录页历史记录: {data.get('title')}")

        # 计算 ID
        current_chapter_id = -1
        if data.get('title'):
            current_chapter_id = parse_chapter_id(data['title'])
        
        # 网页版 URL 兜底 ID
        if current_chapter_id <= 0 and not u.startswith('epub:'):
            match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', u.split('?')[0])
            if match: current_chapter_id = int(match.group(1))

        # [新增] AJAX 模式支持 (用于前端骨架屏无刷新加载)
        if request.args.get('mode') == 'ajax':
            return jsonify({
                'code': 0,
                'data': {
                    'title': data.get('title'),
                    'content': data.get('content'),
                    'prev_url': data.get('prev') or data.get('prev_url'),
                    'next_url': data.get('next') or data.get('next_url'),
                    'book_name': data.get('book_name') or data.get('book_title') or '',
                    # 尝试推断 toc_url，优先用 data 里的，没有则回退到 key 对应的链接
                    'toc_url': data.get('toc_url') or (managers.db.find(k)['url'] if k and managers.db.find(k) else '')
                },
                'current_url': u,
                'chapter_id': current_chapter_id
            })

        # 6. 渲染页面
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
    
    # [智能检测] 如果获取的内容实际上是章节页，自动跳转到阅读页
    if data:
        page_type = detect_page_type(data)
        if page_type == 'chapter':
            print(f"[Smart Redirect] 检测到目录URL返回了章节内容，重定向到阅读页: {u}")
            # 如果是API调用，返回错误提示
            if is_api:
                return jsonify({
                    "status": "redirect",
                    "message": "该URL是章节页而非目录页",
                    "redirect_url": url_for('core.read_mode', url=u, key=k)
                })
            # 否则直接重定向到阅读页
            return redirect(url_for('core.read_mode', url=u, key=k))
    
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

@core_bp.route('/api/quick_save', methods=['POST'])
@login_required
def api_quick_save():
    """
    快速保存当前阅读的书籍到书架
    用于搜索页未保存，但阅读时想保存的场景
    """
    key = request.json.get('key')
    url = request.json.get('url')  # 目录 URL
    
    if not key or not url:
        return jsonify({"status": "error", "message": "缺少参数"})
    
    # 保存到数据库
    res = managers.db.insert(key, url)
    
    if res.get('status') == 'success':
        return jsonify({"status": "success", "message": "已保存到书架"})
    else:
        return jsonify({"status": "error", "message": res.get('message', '保存失败')})

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
    
    # [新增] 调试日志：打印识别结果
    print(f"[Sync Debug] 书籍={key}, 标题=\"{title}\", 识别ID={real_id}")
    
    # 只有当 real_id 是有效正整数时才更新 meta
    # 如果返回 -1 (未识别)，这里直接跳过，数据库里旧的 meta 会保留
    if real_id > 0:
        try:
            import json
            # 获取旧 meta
            meta_key = f"{key}:meta"
            old_meta_str = managers.db.get_val(meta_key)
            meta = json.loads(old_meta_str) if old_meta_str else {}
            
            # 更新序号和时间戳
            meta['chapter_id'] = real_id
            meta['updated_at'] = int(time.time())
            
            # [关键调试] 打印即将保存的内容
            print(f"[Sync Debug] 准备保存 - Key='{meta_key}', Value='{json.dumps(meta)}'")
            
            save_result = managers.db.update(meta_key, json.dumps(meta))
            
            # [关键调试] 打印保存结果
            print(f"[Sync Debug] 保存结果: {save_result}")
            
            # 验证保存是否成功（立即读回来检查）
            verify_str = managers.db.get_val(meta_key)
            if verify_str:
                print(f"[Sync] ✅ 识别并保存成功：{title} -> ID {real_id}, 验证读取: {verify_str}")
            else:
                print(f"[Sync] ❌ 保存失败！无法读回数据")
                
        except Exception as e:
            print(f"[Sync] Meta save error: {e}")
            import traceback
            traceback.print_exc()
    else:
        # 如果没识别到，打印一个日志方便调试，但不写库
        print(f"[Sync] ⚠️ 章节识别失败，跳过 Meta 记录: \"{title}\"")

    # 3. 历史版本 (仅手动)
    if is_manual and res.get('status') == 'success':
        managers.db.add_version(key, final_value)
    
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
    manual_book_name = (request.json.get('manual_book_name') or '').strip()
    force = bool(request.json.get('force'))
    
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

    # 2. 手动书名优先 (避免现场爬取/命中缓存)
    if manual_book_name:
        book_name = manual_book_name

    # 3. 【关键补丁】如果书单没找到，直接“现场爬取”当前阅读页提取书名
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
    sources = searcher.search_bing(book_name) if force else searcher.search_bing_cached(book_name)
    
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
        meta_key = f"{key}:meta"
        meta_str = managers.db.get_val(meta_key)
        meta = {}
        
        # [关键调试] 打印读取的详细信息
        print(f"[GetValue Debug] 书籍={key}")
        print(f"[GetValue Debug] 读取Key='{meta_key}'")
        print(f"[GetValue Debug] 读取结果='{meta_str}'")
        
        if meta_str:
            try: 
                import json
                meta = json.loads(meta_str)
                print(f"[GetValue Debug] 解析后meta={meta}")
            except Exception as e:
                print(f"[GetValue Error] 书籍={key}, meta解析失败: {e}")
        else:
            print(f"[GetValue Debug] meta_str为空或None")
        
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
def api_search():
    try:
        keyword = request.json.get('keyword') if request.json else None
        if not keyword: return jsonify({"status": "error", "message": "缺少关键词"})
        tid = managers.task_manager.submit(_worker_search, keyword)
        return jsonify({"status": "pending", "task_id": tid})
    except Exception as e:
        print(f"[Search Error] {e}")
        return jsonify({"status": "error", "message": str(e)})

@core_bp.route('/api/upload_epub', methods=['POST'])
@login_required
def api_upload_epub():
    try:
        if 'file' not in request.files: 
            return jsonify({"status": "error", "message": "未检测到文件"})
        f = request.files['file']
        if not f.filename:
            return jsonify({"status": "error", "message": "文件名为空"})
        if not f.filename.lower().endswith('.epub'):
            return jsonify({"status": "error", "message": "仅支持EPUB格式"})
        fn = epub_handler.save_file(f)
        k = searcher.get_pinyin_key(os.path.splitext(fn)[0])
        v = f"epub:{fn}:toc"
        managers.db.insert(k, v)
        return jsonify({"status": "success", "key": k, "value": v})
    except Exception as e:
        print(f"[Upload Error] {e}")
        return jsonify({"status": "error", "message": str(e)})
# ... 引入 update_manager ...
from managers import db, update_manager, booklist_manager, task_manager, get_current_user

# === 异步任务 Worker 函数 ===

def _worker_search(keyword, callback=None):
    """后台搜索任务"""
    # 如果有 callback (即来自 TaskManager 的 update_task), 传入 search_concurrent
    if callback:
         return searcher.search_concurrent(keyword, callback)
    # 否则兼容旧调用
    return searcher.search_bing(keyword)

def _worker_check_update(book_key, current_url, callback=None, username=None):
    """后台检查更新任务"""
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
        
        # === 4. 更新数据库元数据 ===
        update_payload = {}
        if toc_data.get('cover'): update_payload['cover'] = toc_data['cover']
        if toc_data.get('author'): update_payload['author'] = toc_data['author']
        if toc_data.get('desc'): update_payload['desc'] = toc_data['desc']
        if toc_data.get('tags'): update_payload['official_tags'] = toc_data['tags']

        # === 4.1 [新增] 目录元数据不足时，尝试番茄 + 起点综合补全 ===
        need_fallback = (
            not update_payload.get('cover') or
            not update_payload.get('author') or
            not update_payload.get('desc') or
            (update_payload.get('author') in ['未知作者', '', None])
        )
        if need_fallback:
            try:
                book_data = managers.db.get_full_data(book_key, username=username) or {}
                book_name = book_data.get('book_name') or book_data.get('title') or book_data.get('name') or book_key
                print(f"[Update] Meta缺失，尝试综合补全: {book_name}")
                extra_meta = crawler.get_meta_from_qidian_fanqie(book_name)
                if extra_meta:
                    if not update_payload.get('cover') and extra_meta.get('cover'):
                        update_payload['cover'] = extra_meta['cover']
                    if (not update_payload.get('author') or update_payload.get('author') == '未知作者') and extra_meta.get('author'):
                        update_payload['author'] = extra_meta['author']
                    if not update_payload.get('desc') and extra_meta.get('desc'):
                        update_payload['desc'] = extra_meta['desc']
                    if not update_payload.get('official_tags') and extra_meta.get('tags'):
                        update_payload['official_tags'] = extra_meta['tags']
            except Exception as e:
                print(f"[Update] 综合补全失败: {e}")
        
        if update_payload:
            managers.db.update(book_key, update_payload, username=username)

        # === 5. 更新追更管理器 ===
        save_data = {
            "latest_title": latest_chap.get('title') or latest_chap.get('name'),
            "latest_url": latest_chap['url'],
            "latest_id": latest_chap.get('id', -1),
            "toc_url": toc_url
        }
        managers.update_manager.set_update(book_key, save_data, username=username)

        # === 6. 计算进度差值 (返回给前端) ===
        response_data = {
            "latest_title": save_data['latest_title'],
            "latest_url": save_data['latest_url'],
            "unread_count": 0,
            "status_text": "已最新"
        }
        
        # A. 获取当前阅读章节 ID
        current_id = parse_chapter_id(current_url)
        if current_id <= 0:
            match = re.search(r'/(\d+)(?:_\d+)?(?:\.html)?$', current_url)
            if match: current_id = int(match.group(1))
        
        latest_id = save_data['latest_id']
        
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

        return {"status": "success", "data": response_data, "msg": "刷新成功"}
    
    return {"status": "failed", "msg": "未获取到目录"}

# === API 路由 ===

@core_bp.route('/api/task_status/<task_id>')
@login_required
def api_task_status(task_id):
    t = managers.task_manager.get_status(task_id)
    if t: return jsonify(t)
    return jsonify({"status": "not_found"})

# 1. 手动检查单本更新 (异步版)
@core_bp.route('/api/check_update', methods=['POST'])
@login_required
def api_check_update():
    current_url = request.json.get('url') 
    book_key = request.json.get('key')
    
    if not current_url: return jsonify({"status": "error", "msg": "No URL"})
    
    # 提交异步任务
    username = get_current_user()
    tid = managers.task_manager.submit(_worker_check_update, book_key, current_url, username=username)
    return jsonify({"status": "pending", "task_id": tid})


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

@core_bp.route('/api/updates/manual_check', methods=['POST'])
@login_required
def api_manual_check():
    """手动立即检查指定书籍更新"""
    data = request.json
    key = data.get('key')
    toc_url = data.get('toc_url')
    
    if not key or not toc_url:
        return jsonify({"status": "error", "msg": "参数不完整"})
    
    try:
        # 强制清除缓存
        from managers import cache
        cache_file = cache._get_filename(toc_url)
        if os.path.exists(cache_file):
            os.remove(cache_file)
        
        # 获取本地最后已知章节
        local_seq = -1
        cached_toc = managers.cache.get(toc_url)
        if cached_toc and cached_toc.get('chapters'):
            local_last = cached_toc['chapters'][-1]
            local_seq = parse_chapter_id(local_last.get('title', ''))
        
        # 获取远程最新章节
        latest_data = crawler.get_latest_chapter(toc_url, no_cache=True)
        if not latest_data:
            return jsonify({"status": "error", "msg": "无法获取远程数据"})
        
        remote_title = latest_data.get('title', '')
        remote_seq = parse_chapter_id(remote_title)
        raw_id = latest_data.get('id', 0)
        
        # 严格判断章节号
        if remote_seq == -1 and 0 < raw_id < 10000:
            remote_seq = raw_id
        
        id_to_save = remote_seq if remote_seq > 0 else raw_id
        has_update = id_to_save > local_seq if local_seq > 0 else False
        
        # 更新数据库状态
        managers.update_sub_manager.update_status(key, id_to_save, has_update)
        
        return jsonify({
            "status": "success",
            "has_update": has_update,
            "latest_title": remote_title,
            "latest_id": id_to_save
        })
    except Exception as e:
        print(f"[Manual Check Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})

@core_bp.route('/api/rename_key', methods=['POST'])
@login_required
def api_rename_key():
    """重命名书签Key，同时迁移所有关联数据"""
    data = request.json
    old_key = data.get('old_key')
    new_key = data.get('new_key')
    
    if not old_key or not new_key:
        return jsonify({"status": "error", "msg": "参数不完整"})
    
    if old_key == new_key:
        return jsonify({"status": "error", "msg": "新旧Key相同"})
    
    # 检查新Key是否已存在
    existing_val = managers.db.get_val(new_key)
    if existing_val:
        return jsonify({"status": "error", "msg": f"Key [{new_key}] 已存在，请换一个名字"})
    
    try:
        # 1. 迁移主数据
        old_val = managers.db.get_val(old_key)
        if not old_val:
            return jsonify({"status": "error", "msg": "原Key不存在"})
        
        managers.db.insert(new_key, old_val)
        managers.db.remove(old_key)
        
        # 2. 迁移标签
        all_tags = managers.tag_manager.load()
        if old_key in all_tags:
            managers.tag_manager.update_tags(new_key, all_tags[old_key])
            managers.tag_manager.update_tags(old_key, [])  # 清空旧的
        
        # 3. 迁移追更订阅（如果有）
        try:
            username = session.get('user', {}).get('username')
            old_sub = managers.update_sub_manager.get_book_status(old_key)
            if old_sub['subscribed']:
                # 复制订阅数据到新Key
                conn = managers.get_db()
                conn.execute('''
                    INSERT INTO book_updates (book_key, toc_url, last_local_id, last_remote_id, has_update, username)
                    SELECT ?, toc_url, last_local_id, last_remote_id, has_update, username
                    FROM book_updates WHERE book_key = ? AND username = ?
                ''', (new_key, old_key, username))
                # 删除旧的
                conn.execute('DELETE FROM book_updates WHERE book_key = ? AND username = ?', (old_key, username))
                conn.commit()
        except Exception as e:
            print(f"[Rename] 迁移追更失败（可忽略）: {e}")
        
        # 4. 迁移历史记录（最近阅读）
        try:
            history_data = managers.history_manager.load()
            if 'records' in history_data:
                for record in history_data['records']:
                    if record.get('key') == old_key:
                        record['key'] = new_key
                managers.history_manager.save(history_data)
        except Exception as e:
            print(f"[Rename] 迁移历史失败（可忽略）: {e}")
        
        return jsonify({"status": "success", "msg": f"已重命名: {old_key} → {new_key}"})
    
    except Exception as e:
        print(f"[Rename Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})

# ==========================================
# 导出 API
# ==========================================
@core_bp.route('/api/export/start', methods=['POST'])
@login_required
def start_export():
    """启动导出任务"""
    try:
        data = request.json
        toc_url = data.get('toc_url')
        book_name = data.get('book_name')
        export_format = data.get('format', 'txt')  # txt 或 epub
        key = data.get('key', '')
        
        if not book_name:
            return jsonify({"status": "error", "msg": "缺少书名"})
        
        # 如果没有提供 toc_url，尝试从 key 获取书籍信息
        if not toc_url:
            book_info = managers.db.find(key)
            if book_info and book_info.get('status') == 'success':
                book_data = book_info['data'].get(key, {})
                toc_url = book_data.get('url')
        
        if not toc_url:
            return jsonify({"status": "error", "msg": "无法获取书籍 URL"})
        
        print(f"[Export] 开始导出: {book_name}, URL: {toc_url}, 格式: {export_format}")
        
        # 获取目录信息
        # 如果 toc_url 是章节页，爬虫会自动获取其目录页
        toc = managers.cache.get(toc_url)
        if not toc:
            print(f"[Export] 缓存未命中，正在从网络获取目录...")
            toc = crawler.get_toc(toc_url)
            print(f"[Export] 爬虫返回结果: {type(toc)}, keys: {toc.keys() if isinstance(toc, dict) else 'N/A'}")
        
        if not toc:
            return jsonify({"status": "error", "msg": "爬虫返回空数据，请检查网络或适配器状态"})
        
        if not isinstance(toc, dict):
            return jsonify({"status": "error", "msg": f"爬虫返回数据格式错误，类型: {type(toc)}"})
        
        chapters = toc.get('chapters', [])
        print(f"[Export] 第一次解析到章节数量: {len(chapters)}")
        
        # 如果没有 chapters 但有 toc_url，说明传入的是章节页，需要重新获取目录页
        if not chapters and toc.get('toc_url'):
            real_toc_url = toc.get('toc_url')
            print(f"[Export] 检测到章节页，重定向到目录页: {real_toc_url}")
            
            # 从目录页重新获取
            toc = managers.cache.get(real_toc_url) or crawler.get_toc(real_toc_url)
            if toc:
                chapters = toc.get('chapters', [])
                print(f"[Export] 从目录页解析到章节数量: {len(chapters)}")
        
        if not chapters:
            # 提供更详细的错误信息
            error_msg = "目录中没有章节。"
            
            # 判断是否是番茄小说
            if 'fanqie' in toc_url.lower():
                error_msg += "\n\n您正在导出番茄小说，需要先启动番茄适配器服务。"
                error_msg += "\n请运行: cd tools/fanqie_api && python app.py"
            else:
                error_msg += "\n\n可能原因："
                error_msg += "\n1. 网络连接问题"
                error_msg += "\n2. 源站反爬限制"
                error_msg += "\n3. 页面结构变化"
                
            # 打印完整的 toc 结构以便调试
            print(f"[Export Debug] TOC 完整内容: {toc}")
            
            return jsonify({"status": "error", "msg": error_msg})
        
        # 准备元数据（用于 EPUB）
        book_info = managers.db.find(key)
        metadata = {
            'author': book_info.get('author', '未知作者') if book_info else '未知作者',
            'description': book_info.get('intro', '') if book_info else '',
            'language': 'zh'
        }
        
        print(f"[Export] 启动导出任务，章节数: {len(chapters)}")
        
        # 检查是否是续传
        resume_task_id = data.get('resume_task_id')
        delay = data.get('delay', 0.5)  # 获取用户设置的延迟，默认 0.5 秒
        
        # 启动导出任务
        task_id = managers.exporter.start_export(
            book_name=book_name,
            chapters=chapters,
            crawler_instance=crawler,
            export_format=export_format,
            metadata=metadata,
            resume_task_id=resume_task_id,
            delay=delay
        )
        
        return jsonify({"status": "success", "task_id": task_id})
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Export Start Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})

@core_bp.route('/api/export/status')
@login_required
def export_status():
    """查询导出状态"""
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({"status": "error", "msg": "缺少 task_id"})
    
    task = managers.exporter.get_status(task_id)
    if not task:
        return jsonify({"status": "error", "msg": "任务不存在"})
    
    return jsonify({
        "status": "success",
        "task": {
            "book_name": task['book_name'],
            "total": task['total'],
            "current": task['current'],
            "status": task['status'],
            "format": task['format'],
            "filename": task.get('filename', ''),
            "error_msg": task.get('error_msg', '')
        }
    })

@core_bp.route('/api/export/pause', methods=['POST'])
@login_required
def pause_export():
    """暂停导出任务"""
    data = request.get_json()
    task_id = data.get('task_id')
    
    if not task_id:
        return jsonify({"success": False, "msg": "缺少任务ID"})
    
    managers.exporter.pause_export(task_id)
    return jsonify({"success": True})

@core_bp.route('/api/export/resume', methods=['POST'])
@login_required
def resume_export():
    """恢复暂停的导出任务"""
    data = request.get_json()
    task_id = data.get('task_id')
    url = data.get('url')
    delay = data.get('delay', 0.5)  # 获取用户设置的延迟
    
    if not task_id:
        return jsonify({"success": False, "msg": "缺少任务ID"})
    
    # 直接使用全局 crawler 实例
    success = managers.exporter.resume_export(task_id, crawler)
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "msg": "任务不存在或状态错误"})

@core_bp.route('/api/export/list')
@login_required
def export_list():
    """获取所有导出任务（包括已完成和未完成的）"""
    tasks = []
    for task_id, task in managers.exporter.exports.items():
        # 只返回已完成或暂停的任务
        if task['status'] in ['completed', 'paused']:
            tasks.append({
                'task_id': task_id,
                'book_name': task['book_name'],
                'format': task['format'],
                'status': task['status'],
                'total': task['total'],
                'current': task.get('current', 0),
                'filename': task.get('filename', ''),
                'created_at': task.get('created_at', '')
            })
    
    # 按创建时间倒序排列
    tasks.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify({"success": True, "tasks": tasks})

@core_bp.route('/api/export/download')
@login_required
def export_download():
    """下载导出文件"""
    task_id = request.args.get('task_id')
    if not task_id:
        return "Missing task_id", 400
    
    task = managers.exporter.get_status(task_id)
    if not task:
        return "Task not found", 404
    
    if task['status'] != 'completed':
        return "Export not completed", 400
    
    return send_from_directory(DL_DIR, task['filename'], as_attachment=True)

@core_bp.route('/api/export/check_unfinished', methods=['POST'])
@login_required
def check_unfinished_export():
    """检查是否有未完成的导出任务"""
    try:
        data = request.json
        book_name = data.get('book_name')
        
        if not book_name:
            return jsonify({"status": "error", "msg": "缺少书名"})
        
        task_id = managers.exporter.find_unfinished_task(book_name)
        
        if task_id:
            task = managers.exporter.get_status(task_id)
            return jsonify({
                "status": "success",
                "has_unfinished": True,
                "task_id": task_id,
                "task": {
                    "total": task['total'],
                    "current": task.get('current', 0),
                    "format": task['format'],
                    "delay": task.get('delay', 0.5)  # 返回任务的延迟设置
                }
            })
        else:
            return jsonify({"status": "success", "has_unfinished": False})
    
    except Exception as e:
        print(f"[Export Check Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})

@core_bp.route('/api/cluster/latency_stats')
@login_required
def api_latency_stats():
    """查看集群延迟统计（监控权重调整效果）"""
    try:
        if not managers.cluster_manager.use_redis:
            return jsonify({"status": "error", "msg": "未启用Redis集群模式"})
        
        # 获取所有延迟记录的域名
        pattern = "crawler:latency:*"
        keys = managers.cluster_manager.r.keys(pattern)
        
        stats = {}
        for key in keys:
            domain = key.replace("crawler:latency:", "")
            latencies = managers.cluster_manager.r.hgetall(key)
            
            if latencies:
                # 转换为可读格式
                node_stats = {}
                for node_uuid, latency_str in latencies.items():
                    latency = float(latency_str)
                    # 计算该延迟对应的权重系数
                    coefficient = managers.cluster_manager._get_speed_coefficient(latency)
                    node_stats[node_uuid] = {
                        "latency_ms": round(latency, 0),
                        "weight_coefficient": round(coefficient, 2),
                        "status": "极快" if latency < 500 else "正常" if latency < 2000 else "较慢" if latency < 5000 else "很慢"
                    }
                
                stats[domain] = node_stats
        
        return jsonify({
            "status": "success",
            "algorithm": "EWMA (α=0.15) + 异常值过滤 + 熔断保护",
            "data": stats
        })
    
    except Exception as e:
        print(f"[Latency Stats Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})

@core_bp.route('/api/cluster/latency_update', methods=['POST'])
@login_required
def api_latency_update():
    """手动更新节点延迟权重（管理员补救功能）"""
    try:
        # 权限检查
        user_role = managers.role_manager.get_role(session.get('user', {}).get('username'))
        if user_role not in ['admin', 'pro']:
            return jsonify({"status": "error", "msg": "权限不足，需要管理员权限"})
        
        if not managers.cluster_manager.use_redis:
            return jsonify({"status": "error", "msg": "未启用Redis集群模式"})
        
        data = request.json
        domain = data.get('domain')
        node_uuid = data.get('node_uuid')
        latency_ms = data.get('latency_ms')
        
        if not all([domain, node_uuid, latency_ms is not None]):
            return jsonify({"status": "error", "msg": "缺少必需参数"})
        
        # 验证延迟值合理性
        try:
            latency_ms = float(latency_ms)
            if latency_ms < -1 or latency_ms > 60000:
                return jsonify({"status": "error", "msg": "延迟值必须在-1到60000之间"})
        except ValueError:
            return jsonify({"status": "error", "msg": "延迟值必须是数字"})
        
        # 直接写入Redis（跳过EWMA平滑，管理员强制设置）
        key = f"crawler:latency:{domain}"
        managers.cluster_manager.r.hset(key, node_uuid, int(latency_ms))
        managers.cluster_manager.r.expire(key, 7 * 86400)
        
        # 计算新的权重系数
        coefficient = managers.cluster_manager._get_speed_coefficient(latency_ms)
        
        return jsonify({
            "status": "success",
            "msg": "权重已更新",
            "new_coefficient": round(coefficient, 2)
        })
    
    except Exception as e:
        print(f"[Latency Update Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})

@core_bp.route('/api/cluster/latency_reset', methods=['POST'])
@login_required
def api_latency_reset():
    """重置某个域名或节点的权重数据"""
    try:
        # 权限检查
        user_role = managers.role_manager.get_role(session.get('user', {}).get('username'))
        if user_role not in ['admin', 'pro']:
            return jsonify({"status": "error", "msg": "权限不足，需要管理员权限"})
        
        if not managers.cluster_manager.use_redis:
            return jsonify({"status": "error", "msg": "未启用Redis集群模式"})
        
        data = request.json
        domain = data.get('domain')
        node_uuid = data.get('node_uuid')
        
        if domain:
            key = f"crawler:latency:{domain}"
            if node_uuid:
                # 删除特定节点
                managers.cluster_manager.r.hdel(key, node_uuid)
                msg = f"已重置 {domain} 的节点 {node_uuid[:8]}"
            else:
                # 删除整个域名
                managers.cluster_manager.r.delete(key)
                msg = f"已重置 {domain} 的所有节点权重"
        else:
            # 删除所有权重数据
            pattern = "crawler:latency:*"
            keys = managers.cluster_manager.r.keys(pattern)
            if keys:
                managers.cluster_manager.r.delete(*keys)
            msg = f"已重置所有权重数据（共 {len(keys)} 个域名）"
        
        return jsonify({"status": "success", "msg": msg})
    
    except Exception as e:
        print(f"[Latency Reset Error] {e}")
        return jsonify({"status": "error", "msg": str(e)})