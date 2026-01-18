# === dbserver.py (修复版) ===
import os
from dotenv import load_dotenv # 1. 引入这个库

# 2. 【关键】必须在导入其他本地模块（如 routes, managers）之前加载 .env
# 否则 routes/core_bp.py 初始化时读不到环境变量
load_dotenv() 
import sqlite3
from flask import Flask, render_template
from datetime import timedelta
import threading
import time
from spider_core import crawler_instance
# 导入配置
from shared import USER_DATA_DIR
import managers
import json
# 导入蓝图 (这时候 .env 已经加载好了，core_bp 能读到正确的 SERVER)
from routes.core_bp import core_bp
from routes.admin_bp import admin_bp
from routes.pro_bp import pro_bp
# [新增] 引入解析函数
from spider_core import parse_chapter_id

app = Flask(__name__)

# 这里也能正确读到 KEY 了
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-unsafe-key')
app.permanent_session_lifetime = timedelta(days=30)
app.config['SESSION_COOKIE_NAME'] = 'simplenote_session'

app.register_blueprint(core_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(pro_bp)

def schedule_cache_cleanup():
    time.sleep(10)
    managers.cache.cleanup_expired()
    while True:
        time.sleep(86400)
        managers.cache.cleanup_expired()

threading.Thread(target=schedule_cache_cleanup, daemon=True).start()
# === 在 dbserver.py ===
import random
@app.route('/reader_m')
def reader_m():
    """处理/reader_m路由，返回reader_m.html模板页面"""
    return render_template('reader_m.html')
def schedule_auto_check():
    """
    后台线程：每 5 小时检查一次 'book_updates' 表的更新
    """
    time.sleep(60) # 启动后等一会再跑
    
    while True:
        print("[AutoCheck] 🕒 开始后台追更检查...")
        try:
            # 1. 扫描 data.sqlite (针对主数据库模式)
            # 或者扫描 user_data/ 下的所有 .sqlite 文件
            db_files = [f for f in os.listdir(managers.USER_DATA_DIR) if f == 'data.sqlite']
            
            for db_f in db_files:
                db_path = os.path.join(managers.USER_DATA_DIR, db_f)
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    
                    # 检查表是否存在
                    try: cursor.execute("SELECT * FROM book_updates LIMIT 1")
                    except: 
                        conn.close()
                        continue

                    # 获取所有订阅
                    cursor.execute("SELECT book_key, toc_url, last_local_id FROM book_updates")
                    tasks = cursor.fetchall()
                    
                    print(f"[AutoCheck] 发现 {len(tasks)} 个追更任务 (DB: {db_f})")
                    
                    for task in tasks:
                        key = task['book_key']
                        toc_url = task['toc_url']
                        local_id = task['last_local_id']
                        
                        if not toc_url: continue
                        
                        try:
                            # === [核心修复] 修正本地基准 (同步 api_subscribe 逻辑) ===
                            # 即使数据库里记的是 Ch 1，但如果缓存里已经有了 Ch 100，
                            # 我们应该以 Ch 100 为基准，避免误报 "发现更新"。
                            cached_toc = managers.cache.get(toc_url)
                            if cached_toc and cached_toc.get('chapters'):
                                last_chap = cached_toc['chapters'][-1]
                                cached_id = last_chap.get('id')
                                # 如果 id 不存在或异常，尝试从标题解析
                                if not cached_id or cached_id <= 0:
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
                                raw_id = latest_chap.get('id', 0)
                                if isinstance(raw_id, str) and not raw_id.isdigit():
                                    raw_id = 0
                                raw_id = int(raw_id)
                                
                                # 🔥 严格判断：只信小于 10000 的 raw_id（防止数据库 ID 被误认为章节号）
                                if remote_seq == -1 and 0 < raw_id < 10000:
                                     remote_seq = raw_id
                                elif remote_seq == -1:
                                     # 如果解析不出章节号，且 raw_id 太大或为 0，直接跳过此次检查
                                     print(f"   ⚠️ [{key}] 无法识别章节号: title={remote_title}, raw_id={raw_id}")
                                     continue

                                # 决策入库 ID
                                id_to_save = remote_seq if remote_seq > 0 else raw_id
                                
                                # 调试打印
                                # print(f"   [Check] {key}: Seq={remote_seq}, Raw={raw_id} -> Save={id_to_save}")

                                has_u = False
                                if id_to_save > local_id:
                                    has_u = True
                                    print(f"   🔥 [UPDATE] {key}: 本地{local_id} -> 远程{id_to_save}")
                                
                                # 无论有无更新，都刷新 last_remote_id，确保下次比较的基础是正确的
                                # 否则如果数据库里已经是错的 3亿，这里不 update 回去，就永远是错的
                                cursor.execute("UPDATE book_updates SET last_remote_id=?, has_update=?, updated_at=CURRENT_TIMESTAMP WHERE book_key=?", 
                                             (id_to_save, 1 if has_u else 0, key))
                                conn.commit()
                            
                            # 随机休眠
                            time.sleep(random.uniform(3, 8))
                            
                        except Exception as e:
                            print(f"   ❌ 检查失败 {key}: {e}")
                            
                    conn.close()
                except Exception as e:
                    print(f"Db Error: {e}")

        except Exception as e:
            print(f"[AutoCheck] 线程出错: {e}")
            
        # 休眠 5 小时 (18000 秒)
        print("[AutoCheck] 休眠 5 小时...")
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
        print("🔧 [Dev Mode] 开发者模式已启用（支持代码热重载）")
        app.run(debug=True, port=5000, host='0.0.0.0')
    else:
        print("🚀 [Production Mode] 生产模式运行")
        app.run(debug=False, port=5000, host='0.0.0.0')