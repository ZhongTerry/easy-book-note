"""NoteDB - 主服务器应用
轻量级在线小说阅读系统
"""
import os
import json
import sqlite3
import threading
import time
import random
from datetime import timedelta
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session, render_template

# 必须在导入本地模块前加载环境变量
load_dotenv()

from spider_core import crawler_instance, parse_chapter_id
from shared import USER_DATA_DIR, info, warn, error
from config import SESSION_LIFETIME_DAYS, SESSION_COOKIE_NAME
import managers

# 导入路由蓝图
from routes.core_bp import core_bp
from routes.admin_bp import admin_bp
from routes.pro_bp import pro_bp
from routes.cache_bp import cache_bp

# 初始化 Flask 应用
app = Flask(__name__)

# 配置会话密钥
secret_key = os.environ.get('FLASK_SECRET_KEY')
if not secret_key:
    secret_key = os.urandom(32)
    info("Security", "FLASK_SECRET_KEY 未配置，使用随机密钥（重启后会失效）")

app.secret_key = secret_key
app.permanent_session_lifetime = timedelta(days=SESSION_LIFETIME_DAYS)
app.config['SESSION_COOKIE_NAME'] = SESSION_COOKIE_NAME
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'

app.register_blueprint(core_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(pro_bp)
app.register_blueprint(cache_bp)

@app.before_request
def basic_csrf_guard():
    """基础 CSRF 防护：校验同源请求"""
    info("Request", f"{request.method} {request.path} | User: {session.get('user', {}).get('username', 'None')}")
    
    # 只拦截非 GET 请求
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH') and session.get('user'):
        origin = request.headers.get('Origin') or request.headers.get('Referer')
        if origin:
            from urllib.parse import urlparse
            origin_parsed = urlparse(origin)
            origin_host = origin_parsed.netloc.split(':')[0]
            request_host = request.host.split(':')[0]
            
            # 规范化本地地址
            def normalize_host(h):
                return 'localhost' if h in ('localhost', '127.0.0.1', '0.0.0.0') else h
            
            if normalize_host(origin_host) != normalize_host(request_host):
                error("CSRF", f"请求被拒: {origin_host} != {request_host}")
                return jsonify({"status": "error", "msg": "CSRF 验证失败"}), 403

@app.route('/reader_m')
def reader_m():
    """移动端阅读器页面"""
    return render_template('reader_m.html')


def schedule_cache_cleanup():
    """定时清理过期缓存"""
    from config import CACHE_CLEANUP_INTERVAL
    time.sleep(10)  # 启动后稍等
    managers.cache.cleanup_expired()
    
    while True:
        time.sleep(CACHE_CLEANUP_INTERVAL)
        managers.cache.cleanup_expired()


def schedule_auto_check():
    """后台线程：定期检查书籍更新"""
    from config import AUTO_CHECK_INTERVAL, AUTO_CHECK_RANDOM_SLEEP_MIN, AUTO_CHECK_RANDOM_SLEEP_MAX
    
    time.sleep(60)  # 启动后稍等
    
    while True:
        info("AutoCheck", "开始后台追更检查...")
        try:
            db_files = [f for f in os.listdir(managers.USER_DATA_DIR) if f == 'data.sqlite']
            for db_f in db_files:
                db_path = os.path.join(managers.USER_DATA_DIR, db_f)
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    # 检查表是否存在
                    try:
                        cursor.execute("SELECT * FROM book_updates LIMIT 1")
                    except:
                        conn.close()
                        continue

                    # 获取所有订阅
                    cursor.execute("SELECT book_key, content, username FROM books_v2")
                    all_books = cursor.fetchall()
                    
                    tasks = []
                    for b in all_books:
                        try:
                            c = json.loads(b['content'])
                            u_info = c.get('update_info')
                            if u_info and u_info.get('toc_url'):
                                tasks.append({
                                    "book_key": b['book_key'],
                                    "toc_url": u_info['toc_url'],
                                    "last_local_id": u_info.get('last_local_id', 0),
                                    "username": b['username'],
                                    "content": c
                                })
                        except:
                            continue

                    info("Server", f"发现 {len(tasks)} 个追更任务 (DB: {db_f})")
                    
                    for task in tasks:
                        key = task['book_key']
                        toc_url = task['toc_url']
                        local_id = task['last_local_id']
                        username = task['username']
                        content = task['content']
                        
                        if not toc_url: continue
                        
                        try:
                            # === [核心修复] 修正本地基准 (同步 api_subscribe 逻辑) ===
                            # 即使数据库里记的是 Ch 1，但如果缓存里已经有了 Ch 100，
                            # 我们应该以 Ch 100 为基准，避免误报 "发现更新"。
                            cached_toc = managers.cache.get(toc_url)
                            if cached_toc and cached_toc.get('chapters'):
                                last_chap = cached_toc['chapters'][-1]
                                cached_id = parse_chapter_id(last_chap.get('title', ''))
                                
                                # 取大者作为基准
                                if cached_id > local_id:
                                    # print(f"   [AutoCheck] 基准修正 {key}: DB({local_id}) -> Cache({cached_id})")
                                    local_id = cached_id

                            # === 爬取最新章节 ===
                            # 1. 获取目录
                            latest_chap = crawler_instance.get_latest_chapter(toc_url, no_cache=True)
                            
                            if latest_chap:
                                remote_title = latest_chap.get('title', '')
                                
                                # [核心修复] 优先解析自然序号 (和 core_bp.py 保持一致)
                                remote_seq = parse_chapter_id(remote_title)
                                if remote_seq <= 0:
                                    error("Server", f"   ⚠️ [{key}] 无法识别章节号: title={remote_title}")
                                    continue

                                # 决策入库 ID
                                id_to_save = remote_seq
                                
                                # 调试打印
                                # print(f"   [Check] {key}: Seq={remote_seq} -> Save={id_to_save}")

                                has_u = False
                                if id_to_save > local_id:
                                    has_u = True
                                    info("Server", f"   🔥 [UPDATE] {key}: 本地{local_id} -> 远程{id_to_save}")
                                
                                # 无论有无更新，都刷新 last_remote_id (V2: 更新 content 字段)
                                content['update_info']['last_remote_id'] = id_to_save
                                content['update_info']['has_update'] = has_u
                                content['update_info']['updated_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
                                
                                cursor.execute("UPDATE books_v2 SET content=?, updated_at=CURRENT_TIMESTAMP WHERE username=? AND book_key=?", 
                                             (json.dumps(content, ensure_ascii=False), username, key))
                                conn.commit()

                            
                            # 随机休眠
                            time.sleep(random.uniform(3, 8))
                            
                        except Exception as e:
                            error("Server", f"   ❌ 检查失败 {key}: {e}")
                            
                    conn.close()
                except Exception as e:
                    error("Server", f"Db Error: {e}")

        except Exception as e:
            info("AutoCheck", f"线程出错: {e}")
            
        # 休眠 5 小时 (18000 秒)
        info("AutoCheck", "休眠 5 小时...")
        time.sleep(18000)

# 在 main 中启动
threading.Thread(target=schedule_auto_check, daemon=True).start()

if __name__ == '__main__':
    # 🔥 从环境变量读取开发模式配置
    # DEV_MODE=true 或 DEBUG=true 启用开发者模式（自动重载）
    # 默认为生产模式（debug=False）
    is_dev_mode = os.environ.get('DEV_MODE', '').lower() in ('true', '1', 'yes') or \
                  os.environ.get('DEBUG', '').lower() in ('true', '1', 'yes')
    
    if is_dev_mode:
        info("Server", "🔧 [Dev Mode] 开发者模式已启用（支持代码热重载）")
        app.run(debug=True, port=5000, host='0.0.0.0')
    else:
        info("Server", "🚀 [Production Mode] 生产模式运行")
        app.run(debug=False, port=5000, host='0.0.0.0')