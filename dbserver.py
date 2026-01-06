# === dbserver.py (修复版) ===
import os
from dotenv import load_dotenv # 1. 引入这个库

# 2. 【关键】必须在导入其他本地模块（如 routes, managers）之前加载 .env
# 否则 routes/core_bp.py 初始化时读不到环境变量
load_dotenv() 

from flask import Flask
from datetime import timedelta
import threading
import time

# 导入配置
from shared import USER_DATA_DIR
import managers
import json
# 导入蓝图 (这时候 .env 已经加载好了，core_bp 能读到正确的 SERVER)
from routes.core_bp import core_bp
from routes.admin_bp import admin_bp
from routes.pro_bp import pro_bp

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

def schedule_auto_check():
    """
    后台线程：每 4 小时检查一次 'to_read' 书单的更新
    """
    time.sleep(60) # 启动后等一会再跑
    
    while True:
        print("[AutoCheck] 开始检查必读书单更新...")
        try:
            # 遍历 user_data 下所有的 booklist 文件
            # 因为是后台线程，没有 session，需要物理扫描文件
            for f in os.listdir(managers.USER_DATA_DIR):
                if f.endswith("_booklists.json"):
                    username = f.replace("_booklists.json", "")
                    filepath = os.path.join(managers.USER_DATA_DIR, f)
                    
                    with open(filepath, 'r', encoding='utf-8') as bf:
                        lists = json.load(bf)
                    
                    # 寻找名为 to_read 的书单 (不区分大小写)
                    target_list = None
                    for lid, data in lists.items():
                        if data['name'].lower() in ['to_read', '必读', '追更']:
                            target_list = data['books']
                            break
                    
                    if target_list:
                        print(f"[AutoCheck] 正在为用户 {username} 检查 {len(target_list)} 本书...")
                        # 遍历书籍 (注意：这里我们缺 toc_url，只能用 key 去 kv_store 反查 value)
                        # 为了简化，我们假设 value 就是最新的阅读进度 URL，爬虫能从这个 URL 找到目录
                        
                        # 加载该用户的 KV 库
                        user_db_path = os.path.join(managers.USER_DATA_DIR, f"{username}.sqlite")
                        conn = sqlite3.connect(user_db_path)
                        cursor = conn.cursor()
                        
                        for book in target_list:
                            key = book['key']
                            cursor.execute("SELECT value FROM kv_store WHERE key=?", (key,))
                            row = cursor.fetchone()
                            if row:
                                current_url = row[0]
                                # 调用爬虫 (这里复用 crawler 实例)
                                # 注意：这里是耗时操作
                                try:
                                    # 先找目录
                                    page_info = managers.crawler_instance.run(current_url)
                                    toc_url = page_info.get('toc_url') or current_url
                                    
                                    latest = managers.crawler_instance.get_latest_chapter(toc_url)
                                    if latest:
                                        managers.update_manager.set_update(key, latest, username)
                                        print(f"   -> {book['title']} 更新至: {latest['title']}")
                                        # 随机休眠，防止被封 IP
                                        time.sleep(random.uniform(2, 5))
                                except Exception as e:
                                    print(f"   -> 检查失败 {key}: {e}")
                        
                        conn.close()

        except Exception as e:
            print(f"[AutoCheck] 线程出错: {e}")
            
        # 休眠 4 小时 (14400 秒)
        time.sleep(14400)

# 在 main 中启动
threading.Thread(target=schedule_auto_check, daemon=True).start()
if __name__ == '__main__':
    app.run(debug=False, port=5000, host='0.0.0.0')