import os
import time
import requests
import json
import logging
import sys
from unittest.mock import MagicMock
from dotenv import load_dotenv
# 强制加载同目录下的 config.env 文件
load_dotenv('config.env') 
# === 环境模拟 ===
sys.modules['managers'] = MagicMock()
sys.modules['managers.cache'] = MagicMock()
from spider_core import crawler_instance as crawler, searcher

# === 配置区 ===
# 注意：Pull 模式下，Worker 不需要公网 IP，也不需要 Port
MASTER_URL = os.environ.get("MASTER_URL", "https://book.ztrztr.top")
AUTH_TOKEN = os.environ.get("REMOTE_CRAWLER_TOKEN", "my-secret-token-888")
NODE_NAME = os.environ.get("NODE_NAME", "NoIP-Worker-01")

def do_work(task):
    """执行具体任务"""
    endpoint = task['endpoint']
    payload = task['payload']
    url = payload.get('url')
    
    print(f"⚡ [Job] 接到任务: {endpoint} -> {url}")
    
    result = {"status": "failed", "msg": "Unknown error"}
    
    try:
        data = None
        if endpoint == 'run':
            data = crawler.run(url)
        elif endpoint == 'toc':
            data = crawler.get_toc(url)
        elif endpoint == 'search':
            data = searcher.search_bing(payload.get('keyword'))
            
        if data:
            result = {"status": "success", "data": data}
        else:
            result = {"status": "failed", "msg": "Empty data"}
            
    except Exception as e:
        print(f"❌ 任务出错: {e}")
        result = {"status": "error", "msg": str(e)}
        
    return result

def worker_loop():
    print(f"🚀 Worker [{NODE_NAME}] 启动 (Pull Mode)")
    print(f"🔗 连接 Master: {MASTER_URL}")
    
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {AUTH_TOKEN}"})
    
    while True:
        try:
            # 1. 索要任务
            # print("☁️ 正在询问任务...") 
            resp = session.post(f"{MASTER_URL}/api/cluster/fetch_task", timeout=10)
            
            if resp.status_code == 403:
                print("🔒 鉴权失败，请检查 Token！")
                time.sleep(10)
                continue
                
            res_json = resp.json()
            
            if res_json.get('status') == 'success':
                # 2. 有任务！开干
                task = res_json['task']
                task_id = task['id']
                
                # 执行爬虫
                crawl_result = do_work(task)
                
                # 3. 交作业
                submit_payload = {
                    "task_id": task_id,
                    "result": crawl_result
                }
                session.post(f"{MASTER_URL}/api/cluster/submit_result", json=submit_payload)
                print(f"✅ [Job] 任务 {task_id} 已回传")
                
            else:
                # 没任务，休息一下，防止把 Master 刷爆
                time.sleep(1) 
                
        except Exception as e:
            print(f"⚠️ 连接中断: {e}")
            time.sleep(5)

if __name__ == '__main__':
    worker_loop()