import os
import time
import psutil
import requests
import threading
import uuid
import logging
import sys
from unittest.mock import MagicMock
from flask import Flask, request, jsonify

# === [黑魔法：环境模拟] ===
# Worker 节点没有数据库，但 spider_core 可能引用 managers。
# 这里 Mock 掉 managers 模块，防止导入 spider_core 时报错。
sys.modules['managers'] = MagicMock()
sys.modules['managers.cache'] = MagicMock()

# 导入核心爬虫 (必须在 Mock 之后)
from spider_core import crawler_instance as crawler, searcher

# === [配置区] ===
NODE_CONFIG = {
    "name": os.environ.get("NODE_NAME", f"Worker-{os.urandom(2).hex()}"),
    "region": os.environ.get("NODE_REGION", "GLOBAL"), # GLOBAL 或 CN
    # 显式指定公网地址 (推荐)，若不填则由 Master 根据请求 IP 猜测
    "public_url": os.environ.get("NODE_PUBLIC_URL", ""), 
    "max_bandwidth": int(os.environ.get("NODE_BW", 100)),
    "max_tasks": int(os.environ.get("NODE_MAX_TASKS", 20)),
    "master_url": os.environ.get("MASTER_URL", "http://127.0.0.1:5000"),
    # 务必修改此 Token
    "auth_token": os.environ.get("REMOTE_CRAWLER_TOKEN", "my-secret-token-888"),
    "port": int(os.environ.get("PORT", 12345))
}

NODE_UUID = str(uuid.uuid4())
CURRENT_TASKS = 0
TASK_LOCK = threading.Lock()

app = Flask(__name__)
# 禁用 Flask 默认日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ==========================================
# 1. 心跳线程 (Heartbeat)
# ==========================================
def heartbeat_loop():
    print(f"💓 [Heartbeat] 启动 | UUID: {NODE_UUID} | Target: {NODE_CONFIG['master_url']}")
    
    while True:
        try:
            payload = {
                "uuid": NODE_UUID,
                "config": {
                    "name": NODE_CONFIG['name'],
                    "region": NODE_CONFIG['region'],
                    "max_tasks": NODE_CONFIG['max_tasks'],
                    "public_url": NODE_CONFIG['public_url'],
                    "port": NODE_CONFIG['port']
                },
                "status": {
                    "cpu": psutil.cpu_percent(interval=None),
                    "memory": psutil.virtual_memory().percent,
                    "current_tasks": CURRENT_TASKS,
                    "timestamp": time.time()
                }
            }
            
            headers = {"Authorization": f"Bearer {NODE_CONFIG['auth_token']}"}
            url = f"{NODE_CONFIG['master_url']}/api/cluster/heartbeat"
            
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            
            if resp.status_code == 401 or resp.status_code == 403:
                print(f"⚠️ [Heartbeat] 鉴权失败，请检查 Token 配置！")
            elif resp.status_code != 200:
                print(f"⚠️ [Heartbeat] Master 返回异常: {resp.status_code}")
                
        except Exception as e:
            print(f"❌ [Heartbeat] 连接失败: {str(e)[:50]}")
            
        time.sleep(10)

# ==========================================
# 2. 鉴权装饰器
# ==========================================
def auth_required(f):
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if token != f"Bearer {NODE_CONFIG['auth_token']}":
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated

# ==========================================
# 3. 任务接口
# ==========================================
@app.route('/api/crawl/run', methods=['POST'])
@auth_required
def remote_run():
    global CURRENT_TASKS
    url = request.json.get('url')
    print(f"🕷️ [Task] 抓取正文: {url}")
    
    with TASK_LOCK: CURRENT_TASKS += 1
    try:
        data = crawler.run(url)
        if data:
            return jsonify({"status": "success", "data": data})
        return jsonify({"status": "failed", "msg": "Empty result"})
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"status": "error", "msg": str(e)}), 500
    finally:
        with TASK_LOCK: CURRENT_TASKS -= 1

@app.route('/api/crawl/toc', methods=['POST'])
@auth_required
def remote_toc():
    global CURRENT_TASKS
    url = request.json.get('url')
    print(f"📑 [Task] 抓取目录: {url}")
    
    with TASK_LOCK: CURRENT_TASKS += 1
    try:
        data = crawler.get_toc(url)
        if data:
            return jsonify({"status": "success", "data": data})
        return jsonify({"status": "failed"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500
    finally:
        with TASK_LOCK: CURRENT_TASKS -= 1

@app.route('/api/crawl/search', methods=['POST'])
@auth_required
def remote_search():
    global CURRENT_TASKS
    keyword = request.json.get('keyword')
    print(f"🔍 [Task] 搜索: {keyword}")
    
    with TASK_LOCK: CURRENT_TASKS += 1
    try:
        data = searcher.search_bing(keyword)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500
    finally:
        with TASK_LOCK: CURRENT_TASKS -= 1

if __name__ == '__main__':
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    print(f"🚀 Worker Running on 0.0.0.0:{NODE_CONFIG['port']}")
    app.run(host='0.0.0.0', port=NODE_CONFIG['port'], threaded=True)