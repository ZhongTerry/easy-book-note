import os
import time
import requests
import json
import logging
import sys
import psutil # 确保安装了 psutil
from unittest.mock import MagicMock
from dotenv import load_dotenv
import threading
load_dotenv('config.env') 

# === [黑魔法：环境模拟] ===
mock_managers = MagicMock()
def configure_mock_manager(manager_mock):
    manager_mock.get.return_value = None
    manager_mock.get_val.return_value = None
    manager_mock.get_chapter.return_value = None
    manager_mock.get_toc.return_value = None
    manager_mock.find.return_value = None
    manager_mock.load.return_value = {}
    manager_mock.get_all.return_value = []
    manager_mock.list_all.return_value = {}
    manager_mock.get_history.return_value = []

known_managers = ['cache', 'db', 'offline_manager', 'booklist_manager', 'tag_manager', 'stats_manager', 'history_manager', 'update_manager', 'role_manager']
for name in known_managers:
    configure_mock_manager(getattr(mock_managers, name))

sys.modules['managers'] = mock_managers
sys.modules['managers.cache'] = mock_managers.cache
for name in known_managers:
    sys.modules[f'managers.{name}'] = getattr(mock_managers, name)

from spider_core import crawler_instance as crawler, searcher

# === 配置区 ===
MASTER_URL = os.environ.get("MASTER_URL", "https://book.ztrztr.top")
AUTH_TOKEN = os.environ.get("REMOTE_CRAWLER_TOKEN", "my-secret-token-888")
NODE_NAME = os.environ.get("NODE_NAME", "Worker-Node")
# 导入 uuid
import uuid
# 全局 UUID (启动生成一次，不变)
NODE_UUID = str(uuid.uuid4())

# 任务计数锁
CURRENT_TASKS = 0
TASK_LOCK = threading.Lock() # 需要导入 threading
import threading

# === [新增] 状态生成辅助函数 ===
def get_node_payload():
    """生成完整的节点状态数据"""
    return {
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

# 补全配置对象
NODE_CONFIG = {
    "name": NODE_NAME,
    "region": os.environ.get("NODE_REGION", "GLOBAL"),
    "max_tasks": int(os.environ.get("NODE_MAX_TASKS", 20)),
    "public_url": os.environ.get("NODE_PUBLIC_URL", ""),
    "port": int(os.environ.get("PORT", 12345))
}


def do_work(task):
    endpoint = task['endpoint']
    payload = task['payload']
    url = payload.get('url')
    print(f"⚡ [Job] 执行: {endpoint} -> {url}")
    result = {"status": "failed", "msg": "Unknown error"}
    
    with TASK_LOCK: CURRENT_TASKS += 1
    try:
        data = None
        if endpoint == 'run': data = crawler.run(url)
        elif endpoint == 'toc': data = crawler.get_toc(url)
        elif endpoint == 'search': data = searcher.search_bing(payload.get('keyword'))
            
        if data:
            # 简单清洗防序列化错误
            try: json.dumps(data)
            except: data = str(data)
            result = {"status": "success", "data": data}
        else:
            result = {"status": "failed", "msg": "Empty data"}
    except Exception as e:
        print(f"❌ 任务出错: {e}")
        result = {"status": "error", "msg": str(e)}
    finally:
        with TASK_LOCK: CURRENT_TASKS -= 1
        
    return result

def worker_loop():
    print(f"🚀 Worker [{NODE_NAME}] 启动 (Hybrid Mode)")
    print(f"🆔 UUID: {NODE_UUID}")
    print(f"🔗 连接 Master: {MASTER_URL}")
    
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SmartNoteDB-Worker"
    })
    
    while True:
        try:
            # === [核心修复] 取任务时，携带完整的状态包 ===
            # 这样即使心跳线程挂了，只要还在取任务，状态就能更新
            full_payload = get_node_payload()
            
            resp = session.post(f"{MASTER_URL}/api/cluster/fetch_task", json=full_payload, timeout=10)
            
            if resp.status_code == 403:
                print("🔒 Token 错误")
                time.sleep(10); continue
            
            # 处理响应
            try:
                res_json = resp.json()
            except:
                time.sleep(5); continue
            
            if res_json.get('status') == 'success':
                task = res_json['task']
                crawl_result = do_work(task)
                session.post(f"{MASTER_URL}/api/cluster/submit_result", json={
                    "task_id": task['id'], "result": crawl_result
                })
                print(f"✅ [Job] 完成")
            else:
                time.sleep(1) 
                
        except Exception as e:
            print(f"⚠️ 网络波动: {e}")
            time.sleep(5)

# 保留心跳线程作为空闲时的保活手段
def heartbeat_thread():
    while True:
        try:
            # 复用同一个 payload 生成函数
            requests.post(
                f"{MASTER_URL}/api/cluster/heartbeat", 
                json=get_node_payload(),
                headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
                timeout=5
            )
        except: pass
        time.sleep(10)

if __name__ == '__main__':
    threading.Thread(target=heartbeat_thread, daemon=True).start()
    worker_loop()