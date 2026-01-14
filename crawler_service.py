import os
import time
import requests
import json
import logging
import sys
from dotenv import load_dotenv

# 加载配置
load_dotenv('config.env')

# =========================================================
# 🛡️ 核心修复：构建一个“哑巴”环境
# =========================================================
# 定义一个只会返回 None 的空类，防止 MagicMock 自动生成对象
class EmptyObject:
    def __getattr__(self, name):
        return None
    def __call__(self, *args, **kwargs):
        return None
    def __getitem__(self, key):
        return None
    def get(self, key, default=None):
        return default

# 1. 创建假的 managers
class MockManagers:
    # 模拟 cache
    class MockCache:
        def get(self, *args): return None  # 核心：强制未命中缓存
        def set(self, *args): pass         # 核心：假装写入缓存，实际啥也不干
        def cleanup_expired(self): pass

    # 模拟 db
    class MockDB:
        def get_val(self, *args): return None
        def list_all(self): return {"data": {}}
    
    # 模拟其他组件
    class MockGeneric:
        def __getattr__(self, name): return EmptyObject()
        def load(self, *args): return {} # 返回空字典
        def get_chapter(self, *args): return None

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

def do_work(task):
    """执行具体任务"""
    endpoint = task['endpoint']
    payload = task['payload']
    url = payload.get('url')
    
    print(f"⚡ [Job] 执行: {endpoint} -> {url}")
    
    result = {"status": "failed", "msg": "Unknown error"}
    
    try:
        data = None
        # 强制爬取逻辑
        if endpoint == 'run':
            data = crawler.run(url)
        elif endpoint == 'toc':
            data = crawler.get_toc(url)
        elif endpoint == 'search':
            data = searcher.search_bing(payload.get('keyword'))
            
        if data:
            # 再次检查数据里有没有混入 Mock 对象 (防御性编程)
            # 如果有，说明 spider_core 里有漏网之鱼，这里将其清洗为字符串
            try:
                json.dumps(data) # 尝试序列化
            except TypeError:
                print("⚠️ 检测到脏数据，正在清洗...")
                data = clean_data(data)

            result = {"status": "success", "data": data}
        else:
            result = {"status": "failed", "msg": "Empty data from crawler"}
            
    except Exception as e:
        print(f"❌ 任务出错: {e}")
        import traceback
        traceback.print_exc()
        result = {"status": "error", "msg": str(e)}
        
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
        return str(obj) # 强制转字符串 (处理漏网的 Mock 对象)

def worker_loop():
    print(f"🚀 Worker [{NODE_NAME}] 启动 (Pull Mode)")
    print(f"🔗 连接 Master: {MASTER_URL}")
    print(f"🛡️  缓存层已屏蔽，全量实时爬取")
    
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    while True:
        try:
            payload = {"uuid": NODE_NAME}
            resp = session.post(f"{MASTER_URL}/api/cluster/fetch_task", json=payload, timeout=10)
            
            if resp.status_code == 403:
                print("🔒 Token 错误")
                time.sleep(10)
                continue
            
            if resp.status_code != 200:
                print(f"⚠️ API 异常: {resp.status_code}")
                time.sleep(5)
                continue
                
            try:
                res_json = resp.json()
            except:
                print("⚠️ 非 JSON 响应")
                time.sleep(5)
                continue
            
            if res_json.get('status') == 'success':
                task = res_json['task']
                crawl_result = do_work(task)
                
                # 回传结果
                session.post(f"{MASTER_URL}/api/cluster/submit_result", json={
                    "task_id": task['id'],
                    "result": crawl_result
                })
                print(f"✅ [Job] 完成")
            else:
                time.sleep(1) # 空闲等待
                
        except Exception as e:
            print(f"⚠️ 网络波动: {e}")
            time.sleep(5)

if __name__ == '__main__':
    worker_loop()