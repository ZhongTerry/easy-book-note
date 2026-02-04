from shared import debug, info, warn, error
import os
import time
import requests
import json
import logging
import sys
import threading
import psutil
import uuid
from dotenv import load_dotenv

# 加载配置
load_dotenv('config.env')

# =========================================================
# 🛡️ 核心修复：构建一个“哑巴”环境 (Dumb Mock)
# =========================================================
# 定义一个只会返回 None 的空类，防止 MagicMock 自动生成对象
class EmptyObject:
    def __getattr__(self, name):
        return None  # 访问任何属性都返回 None
    def __call__(self, *args, **kwargs):
        return None  # 调用任何方法都返回 None
    def __getitem__(self, key):
        return None
    def get(self, key, default=None):
        return default
    def __bool__(self):
        return False # 关键：让 if obj: 判断为 False

# 1. 创建假的 managers
class MockManagers:
    # 模拟 cache
    class MockCache:
        def get(self, *args): return None  # 核心：强制未命中缓存
        def set(self, *args): pass         # 核心：假装写入缓存，实际啥也不干
        def cleanup_expired(self): pass
        def _get_filename(self, *args): return "/tmp/dummy"

    # 模拟 db
    class MockDB:
        def get_val(self, *args): return None
        def list_all(self): return {"data": {}}
        def update(self, *args): return {"status": "success"}
    
    # 模拟其他组件 (全部返回 EmptyObject)
    class MockGeneric(EmptyObject):
        def load(self, *args): return {} # load 返回空字典，方便迭代
        def get_all(self, *args): return {}

    # 实例化
    cache = MockCache()
    db = MockDB()
    offline_manager = MockGeneric()
    booklist_manager = MockGeneric()
    tag_manager = MockGeneric()
    stats_manager = MockGeneric()
    history_manager = MockGeneric()
    update_manager = MockGeneric()
    role_manager = MockGeneric()

    # 模拟配置变量 (防止报错)
    USER_DATA_DIR = "/tmp"
    CACHE_DIR = "/tmp"
    DL_DIR = "/tmp"
    # 模拟 cluster_manager (防止延迟导入报错)
    cluster_manager = MockGeneric() 

# 2. 强行注入系统模块
# 这样 spider_core 导入 managers 时，拿到的就是我们定义的这个“哑巴”对象
sys.modules['managers'] = MockManagers()
sys.modules['managers.cache'] = MockManagers.cache

# =========================================================
# 导入爬虫核心 (必须在注入之后)
# =========================================================
from spider_core import crawler_instance as crawler, searcher

# === 配置区 ===
MASTER_URL = os.environ.get("MASTER_URL", "https://book.ztrztr.top")
AUTH_TOKEN = os.environ.get("REMOTE_CRAWLER_TOKEN", "my-secret-token-888")
NODE_NAME = os.environ.get("NODE_NAME", "Worker-Node")
NODE_UUID = str(uuid.uuid4())

CURRENT_TASKS = 0
TASK_LOCK = threading.Lock()

# 补全配置对象
NODE_CONFIG = {
    "name": NODE_NAME,
    "region": os.environ.get("NODE_REGION", "GLOBAL"),
    "max_tasks": int(os.environ.get("NODE_MAX_TASKS", 20)),
    "public_url": os.environ.get("NODE_PUBLIC_URL", ""),
    "port": int(os.environ.get("PORT", 12345))
}

def get_node_payload():
    return {
        "uuid": NODE_UUID,
        "config": NODE_CONFIG,
        "status": {
            "cpu": psutil.cpu_percent(interval=None),
            "memory": psutil.virtual_memory().percent,
            "current_tasks": CURRENT_TASKS,
            "timestamp": time.time()
        }
    }
def run_speedtest_async(task):
    """
    [新增] 独立的测速线程函数
    """
    payload = task['payload']
    target_url = payload.get('url')
    info("Crawler", f"🚀 [SpeedTest] 后台启动测速: {target_url}")
    
    try:
        import time
        t_start = time.time()
        status_code = 0
        error_msg = ""
        size = 0
        
        try:
            # 使用 requests 直接测速 (不走 curl_cffi，更轻量)
            # 设置短超时，防止卡线程
            r = requests.get(
                target_url, 
                headers={'User-Agent': 'Mozilla/5.0'}, 
                timeout=10, 
                verify=False
            )
            status_code = r.status_code
            size = len(r.content)
        except Exception as req_e:
            error_msg = str(req_e)
        
        latency = int((time.time() - t_start) * 1000)
        info("latency", "")
        # 构造结果
        result = {
            "is_speedtest": True,
            "worker_uuid": NODE_UUID,
            "worker_name": NODE_CONFIG['name'],
            "region": NODE_CONFIG['region'],
            "target": target_url,
            "latency": latency,
            "status_code": status_code,
            "size": size,
            "error": error_msg
        }
        
        # 独立回传结果 (不走主循环)
        # 重新建立一个 session 也可以，或者直接 post
        requests.post(
            f"{MASTER_URL}/api/cluster/submit_result",
            json={"task_id": task['id'], "result": result},
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=5
        )
        # print(f"✅ [SpeedTest] 结果已回传: {latency}ms")
        
    except Exception as e:
        error("Crawler", f"❌ [SpeedTest] 线程出错: {e}")
def do_work(task):
    """执行具体任务"""
    global CURRENT_TASKS # 声明全局变量
    endpoint = task['endpoint']
    payload = task['payload']
    url = payload.get('url')
    
    info("Crawler", f"⚡ [Job] 执行: {endpoint} -> {url}")
    
    result = {"status": "failed", "msg": "Unknown error", "worker_uuid": NODE_UUID}
    
    with TASK_LOCK: CURRENT_TASKS += 1
    try:
        data = None
        # [关键修复] Worker节点强制本地爬取，跳过_remote_request
        # 设置环境变量告诉crawler跳过远程请求
        import os
        original_flag = os.environ.get('FORCE_LOCAL_CRAWL')
        os.environ['FORCE_LOCAL_CRAWL'] = '1'
        
        try:
            no_cache = payload.get('no_cache', False)
            # 强制爬取逻辑
            if endpoint == 'run':
                data = crawler.run(url, no_cache=no_cache)
            elif endpoint == 'toc':
                data = crawler.get_toc(url, no_cache=no_cache)
            elif endpoint == 'search':
                data = searcher.search_bing(payload.get('keyword'))
        finally:
            # 恢复环境变量
            if original_flag is None:
                os.environ.pop('FORCE_LOCAL_CRAWL', None)
            else:
                os.environ['FORCE_LOCAL_CRAWL'] = original_flag
            
        if data:
            # 数据清洗：防止任何非标对象混入
            # 有时候 soup 对象或者 lxml 对象会混进来，导致 JSON 序列化失败
            try:
                json.dumps(data) 
            except TypeError:
                warn("Crawler", "⚠️ 检测到不可序列化数据，执行深度清洗...")
                data = clean_data(data)

            result = {"status": "success", "data": data, "worker_uuid": NODE_UUID}
        else:
            result = {"status": "failed", "msg": "Empty data from crawler", "worker_uuid": NODE_UUID}
            
    except Exception as e:
        error("Crawler", f"❌ 任务出错: {e}")
        # import traceback; traceback.print_exc() # 调试用
        result = {"status": "error", "msg": str(e), "worker_uuid": NODE_UUID}
    finally:
        with TASK_LOCK: CURRENT_TASKS -= 1
        
    return result

def clean_data(obj):
    """递归清洗数据，把所有非基本类型转为字符串"""
    if isinstance(obj, dict):
        return {k: clean_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_data(v) for v in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj) 

def worker_loop():
    info("Crawler", f"🚀 Worker [{NODE_NAME}] 启动 (Pull Mode)")
    info("Crawler", f"🆔 UUID: {NODE_UUID}")
    info("Crawler", f"🔗 连接 Master: {MASTER_URL}")
    info("Crawler", f"🛡️  Mock层已就绪，全量实时爬取")
    
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SmartNoteDB-Worker"
    })
    
    while True:
        try:
            # 取任务 (带状态)
            resp = session.post(f"{MASTER_URL}/api/cluster/fetch_task", json=get_node_payload(), timeout=10)
            
            if resp.status_code == 403:
                error("Crawler", "🔒 Token 错误")
                time.sleep(10); continue
            
            try:
                res_json = resp.json()
            except:
                time.sleep(5); continue
            
            if res_json.get('status') == 'success':
                task = res_json['task']
                task_id = task['id']
                endpoint = task.get('endpoint') # Master 传回来的 endpoint
                
                # === [核心逻辑] 分流处理 ===
                if endpoint == 'speedtest':
                    # 1. 如果是测速任务，启动线程，立即继续循环
                    threading.Thread(target=run_speedtest_async, args=(task,)).start()
                    # 不 sleep，立即去取下一个可能的爬虫任务
                    continue 
                else:
                    # 2. 如果是爬虫任务，阻塞执行 (防止并发过高)
                    crawl_result = do_work(task)
                    
                    # 回传
                    session.post(f"{MASTER_URL}/api/cluster/submit_result", json={
                        "task_id": task_id,
                        "result": crawl_result
                    })
                    info("Crawler", f"✅ [Job] 任务 {task_id} 完成")
            else:
                time.sleep(1) # 没任务，休息
                
        except Exception as e:
            warn("Crawler", f"⚠️ 网络波动: {e}")
            time.sleep(5)

# 独立心跳线程 (作为补充)
def heartbeat_thread():
    while True:
        try:
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