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

if __name__ == '__main__':
    app.run(debug=False, port=5000, host='0.0.0.0')