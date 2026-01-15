from flask import Blueprint, request, jsonify, render_template
import os
import psutil # 记得 pip install psutil
import platform
from shared import CACHE_DIR, USER_DATA_DIR, admin_required
from managers import role_manager, get_db, cluster_manager
from datetime import datetime, timedelta
import json
import managers
# 创建蓝图
admin_bp = Blueprint('admin', __name__)
# routes/admin_bp.py
# routes/admin_bp.py
# routes/admin_bp.py
import uuid
import json
import time

# ... (原有代码)

# === [新增] 任务队列接口 (Pull 模式) ===

@admin_bp.route('/api/cluster/fetch_task', methods=['POST'])
def fetch_task():
    """Worker 来取任务"""
    # 1. 鉴权
    auth_header = request.headers.get('Authorization')
    system_token = os.environ.get('REMOTE_CRAWLER_TOKEN', 'my-secret-token-888')
    if auth_header != f"Bearer {system_token}":
        return jsonify({"status": "error"}), 403

    # 2. 尝试从 Redis 队列弹出一个任务
    # 使用 Redis 的 RPOP (右出)
    try:
        if managers.cluster_manager.use_redis:
            # 这里的 queue_key 需要和 spider_core 里一致
            task_json = managers.cluster_manager.r.rpop("crawler:queue:pending")
            if task_json:
                return jsonify({"status": "success", "task": json.loads(task_json)})
    except Exception as e:
        print(f"Redis Error: {e}")
    try:
        req_data = request.json or {}
        node_uuid = req_data.get('uuid')
        if node_uuid and managers.cluster_manager.use_redis:
            speed_cmd = managers.cluster_manager.should_dispatch_speedtest(node_uuid)
            if speed_cmd:
                return jsonify({
                    "status": "success", 
                    "task": {
                        "id": speed_cmd['id'],
                        "endpoint": "speedtest",
                        "payload": {"url": speed_cmd['url']}
                    }
                })
        if req_data.get('uuid') and 'config' in req_data and 'status' in req_data:
            # 如果有完整数据，直接调用管理器进行全量更新！
            # 这样 CPU、内存、任务数都会被写入 Redis
            print("获取到了整体数据")
            managers.cluster_manager.update_heartbeat(req_data, real_ip)
        elif node_uuid and managers.cluster_manager.use_redis:
            key = f"crawler:node:{node_uuid}"
            
            # 尝试从 Redis 读取现有数据
            raw_data = managers.cluster_manager.r.get(key)
            
            if raw_data:
                # 情况 A: 节点已存在，更新时间戳并续命
                node_data = json.loads(raw_data)
                node_data['last_seen'] = time.time()
                # 重新写入，并重置 60 秒过期
                managers.cluster_manager.r.setex(key, 60, json.dumps(node_data))
            else:
                # 情况 B: 节点不存在 (新节点或已过期)，必须创建一个基础记录！
                # 注意：这里我们拿不到 CPU/内存信息，只能填默认值
                # 但至少能让它在 status 列表里显示出来
                
                # 尝试获取 IP
                if request.headers.getlist("X-Forwarded-For"):
                    real_ip = request.headers.getlist("X-Forwarded-For")[0]
                else:
                    real_ip = request.remote_addr
                
                new_node_data = {
                    "uuid": node_uuid,
                    "name": node_uuid,
                    "config": {
                        "name": f"{node_uuid}", # 暂时不知道名字
                        "region": "GLOBAL",
                        "max_tasks": 20,
                    },
                    "status": {
                        "cpu": 0, "memory": 0, "current_tasks": 0,
                        "timestamp": time.time()
                    },
                    "last_seen": time.time()
                }
                managers.cluster_manager.r.setex(key, 60, json.dumps(new_node_data))
                print(f"➕ [Cluster] 被动注册新节点: {node_uuid}")

    except Exception as e:
        print(f"Keep-alive error: {e}")
    return jsonify({"status": "empty"}) # 没任务，让 Worker 歇会儿

@admin_bp.route('/api/cluster/submit_result', methods=['POST'])
def submit_result():
    """Worker 交作业"""
    # 1. 鉴权 (同上)
    auth_header = request.headers.get('Authorization')
    system_token = os.environ.get('REMOTE_CRAWLER_TOKEN', 'my-secret-token-888')
    if auth_header != f"Bearer {system_token}":
        return jsonify({"status": "error"}), 403

    data = request.json
    task_id = data.get('task_id')
    result = data.get('result') # 爬到的数据
    
    if task_id and managers.cluster_manager.use_redis:
        
        # 3. 把结果写入结果队列，供 spider_core 读取
        # 设置 60秒过期，防止垃圾堆积
        if result.get('is_speedtest'):
            worker_uuid = result.get('worker_uuid')
            target_url = result.get('target')
            # 提取延迟 (如果报错或超时，记为 -1)
            latency = result.get('latency', 9999)
            if result.get('error') or result.get('status_code') != 200:
                latency = -1 
            
            # 存入数据库供下次路由使用
            managers.cluster_manager.record_latency(target_url, worker_uuid, latency)
        if result.get('is_speedtest'):
            # 1. 检查是否超时 (5秒原则)
            # 读取该任务的元数据
            meta_json = managers.cluster_manager.r.get(f"crawler:speedtest:meta:{task_id}")
            if meta_json:
                meta = json.loads(meta_json)
                start_time = meta.get('start_time', 0)
                # 超过 5.5 秒 (给0.5秒网络宽容度) 拒收
                if time.time() - start_time > 5.5:
                    print(f"⏱️ [Cluster] 拒收超时测速结果: {task_id}")
                    return jsonify({"status": "ignored", "msg": "Timeout"})
            else:
                # 元数据没了说明任务早过期了
                return jsonify({"status": "ignored", "msg": "Expired"})
        if result.get('is_speedtest'):
            worker_uuid = result.get('worker_uuid')
            
            # 1. 标记该 Worker 已完成 (加入黑名单，防止重复发任务)
            managers.cluster_manager.r.sadd(f"crawler:speedtest:done:{task_id}", worker_uuid)
            
            # 2. 存入结果 Hash 表
            managers.cluster_manager.r.hset(
                f"crawler:speedtest:results:{task_id}", 
                worker_uuid, 
                json.dumps(result)
            )
            # 结果保留 5 分钟
            managers.cluster_manager.r.expire(f"crawler:speedtest:results:{task_id}", 300)
            managers.cluster_manager.r.expire(f"crawler:speedtest:done:{task_id}", 300)
            
            return jsonify({"status": "success"})
        key = f"crawler:result:{task_id}"
        managers.cluster_manager.r.setex(key, 60, json.dumps(result))
        
    return jsonify({"status": "success"})
@admin_bp.route('/api/admin/speedtest/start', methods=['POST'])
@admin_required
def start_speed_test():
    url = request.json.get('url')
    if not url: return jsonify({"status": "error", "msg": "URL required"})
    
    test_id = managers.cluster_manager.start_speed_test(url)
    if not test_id:
        return jsonify({"status": "error", "msg": "Redis unavailable"})
        
    return jsonify({"status": "success", "test_id": test_id})

# src/routes/admin_bp.py

@admin_bp.route('/api/admin/speedtest/results/<test_id>')
@admin_required
def get_speed_test_results(test_id):
    # 1. 获取包含元数据的完整结果 (这是一个字典)
    # 格式: {'state': 'running', 'total': 5, 'data': [...]}
    res = managers.cluster_manager.get_speed_test_results(test_id)
    
    # 2. [核心修复] 对内部的 'data' 列表进行排序
    if 'data' in res and isinstance(res['data'], list):
        res['data'].sort(key=lambda x: x.get('latency', 9999))
        
    # 3. 返回给前端
    # 前端 JS (admin.html) 里的 const info = json.result; 对应这里的 res
    return jsonify({"status": "success", "result": res})
@admin_bp.route('/api/cluster/heartbeat', methods=['POST'])
def handle_heartbeat():
    auth_header = request.headers.get('Authorization')
    # 默认 Token，生产环境请在 .env 设置
    system_token = os.environ.get('REMOTE_CRAWLER_TOKEN', 'my-secret-token-888')
    print("----------------------------------------")
    # 使用 repr() 可以把看不见的空格、换行符显示出来
    print(f"🔍 [Debug] 收到 Header: {repr(auth_header)}")
    print(f"🔍 [Debug] 系统 期望值: {repr(f'Bearer {system_token}')}")
    print("----------------------------------------")
    
    if auth_header != f"Bearer {system_token}":
        return jsonify({"status": "error", "msg": "Forbidden"}), 403
        
    data = request.json
    
    # [修复] 获取真实 IP (兼容反向代理)
    if request.headers.getlist("X-Forwarded-For"):
        real_ip = request.headers.getlist("X-Forwarded-For")[0]
    else:
        real_ip = request.remote_addr
        
    cluster_manager.update_heartbeat(data, real_ip)
    return jsonify({"status": "success"})

# routes/admin_bp.py

# === [修复] 补全缺失的集群状态接口 ===
@admin_bp.route('/api/admin/cluster_status')
@admin_required
def get_cluster_status():
    """
    [重构版] 获取集群详细状态面板
    """
    # 必须确保 managers 模块已导入
    import time
    
    # 获取节点数据
    raw_nodes = managers.cluster_manager.get_active_nodes()
    
    nodes = []
    now = time.time()
    
    # 全局统计指标
    summary = {
        "total_nodes": 0,
        "online_nodes": 0,
        "total_tasks": 0,     # 当前正在跑的任务
        "max_capacity": 0,    # 集群最大并发能力
        "avg_cpu": 0,
        "regions": {"CN": 0, "GLOBAL": 0}
    }
    
    cpu_sum = 0

    for n in raw_nodes:
        # 1. 计算时间差 (心跳延迟)
        last_seen = n.get('last_seen', 0)
        lag = int(now - last_seen)
        
        # 2. 判断健康状态
        if lag <= 15:
            status = "online"   # 🟢 健康
            summary["online_nodes"] += 1
        elif lag <= 35:
            status = "warning"  # 🟡 网络波动
        else:
            status = "offline"  # 🔴 疑似掉线
            
        # 3. 提取配置
        cfg = n.get('config', {})
        sys_stat = n.get('status', {})
        
        # 4. 统计累加
        tasks = sys_stat.get('current_tasks', 0)
        max_tasks = cfg.get('max_tasks', 20)
        
        summary["total_nodes"] += 1
        summary["total_tasks"] += tasks
        summary["max_capacity"] += max_tasks
        cpu_sum += sys_stat.get('cpu', 0)
        
        region = cfg.get('region', 'GLOBAL')
        summary["regions"][region] = summary["regions"].get(region, 0) + 1

        # 5. 格式化单个节点数据 (返回给前端)
        nodes.append({
            "uuid": n['uuid'],
            "name": cfg.get('name', 'Unknown'),
            "region": region,
            "ip": cfg.get('public_url', '').replace('http://', '').replace('https://', '').split(':')[0],
            "status": status,
            "lag": f"{lag}s",
            "load": f"{tasks}/{max_tasks}",
            "load_pct": round((tasks / max_tasks) * 100, 1) if max_tasks > 0 else 0,
            "cpu": sys_stat.get('cpu', 0),
            "mem": sys_stat.get('memory', 0),
            "version": "v1.0"
        })
    
    # 计算平均 CPU
    if summary["total_nodes"] > 0:
        summary["avg_cpu"] = round(cpu_sum / summary["total_nodes"], 1)

    # 按名称排序
    nodes.sort(key=lambda x: x['name'])

    return jsonify({
        "status": "success",
        "timestamp": now,
        "summary": summary,
        "nodes": nodes
    })
@admin_bp.route('/api/admin/system_summary')
@admin_required
def api_admin_system_summary():
    try:
        with get_db() as conn:
            # 1. 统计总用户数
            user_count = conn.execute("SELECT COUNT(DISTINCT username) FROM user_books").fetchone()[0]
            
            # 2. 统计总藏书量（排除 meta 和系统键）
            book_count = conn.execute("SELECT COUNT(*) FROM user_books WHERE book_key NOT LIKE '@%' AND book_key NOT LIKE '%:meta'").fetchone()[0]
            
            # 3. 统计全站活跃数据
            rows = conn.execute("SELECT json_content FROM user_modules WHERE module_type='stats'").fetchall()
            total_time = 0
            total_words = 0
            for row in rows:
                stats = json.loads(row[0])
                for d in stats.get('daily_stats', {}).values():
                    total_time += d.get('time', 0)
                    total_words += d.get('words', 0)
            
            return jsonify({
                "status": "success",
                "users": user_count,
                "books": book_count,
                "total_time_hr": round(total_time / 60, 1),
                "total_words_wan": round(total_words / 10000, 2)
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
@admin_bp.route('/api/admin/activity_stats')
@admin_required
def api_admin_activity_stats():
    try:
        with get_db() as conn:
            # 获取所有用户的 stats 模块
            rows = conn.execute("SELECT json_content FROM user_modules WHERE module_type='stats'").fetchall()
            
            # 聚合每天的总阅读时长
            aggregate = {}
            for row in rows:
                stats = json.loads(row[0])
                daily = stats.get('daily_stats', {})
                for date_str, data in daily.items():
                    aggregate[date_str] = aggregate.get(date_str, 0) + data.get('time', 0)
            
            # 转换为 Chart.js 格式（最近 30 天）
            today = datetime.now()
            labels = []
            values = []
            for i in range(29, -1, -1):
                d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
                labels.append(d[5:]) # 只取 MM-DD
                values.append(round(aggregate.get(d, 0) / 60, 1)) # 转为小时
            
            return jsonify({"status": "success", "labels": labels, "values": values})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- 2. 获取单个用户详细数据 ---
@admin_bp.route('/api/admin/user_detail/<username>')
@admin_required
def api_admin_user_detail(username):
    try:
        with get_db() as conn:
            # A. 获取统计信息
            stats_row = conn.execute("SELECT json_content FROM user_modules WHERE username=? AND module_type='stats'", (username,)).fetchone()
            stats = json.loads(stats_row[0]) if stats_row else {"daily_stats": {}}
            
            # B. 获取历史记录 (取前 5)
            hist_row = conn.execute("SELECT json_content FROM user_modules WHERE username=? AND module_type='history'", (username,)).fetchone()
            history = json.loads(hist_row[0]).get('records', [])[:5] if hist_row else []
            
            # C. 获取藏书总数
            book_count = conn.execute("SELECT COUNT(*) FROM user_books WHERE username=? AND book_key NOT LIKE '@%'", (username,)).fetchone()[0]
            
            # 计算总时长和总字数
            total_time = sum(d.get('time', 0) for d in stats.get('daily_stats', {}).values())
            total_words = sum(d.get('words', 0) for d in stats.get('daily_stats', {}).values())

            return jsonify({
                "status": "success",
                "data": {
                    "username": username,
                    "total_books": book_count,
                    "total_time_min": total_time,
                    "total_words": total_words,
                    "history": history
                }
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
@admin_bp.route('/api/admin/dashboard')
@admin_required
def api_admin_dashboard():
    # 统计缓存
    cache_count = len(os.listdir(CACHE_DIR))
    cache_size = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in os.listdir(CACHE_DIR) if os.path.isfile(os.path.join(CACHE_DIR, f))) / (1024*1024)
    # 统计用户
    user_count = len([f for f in os.listdir(USER_DATA_DIR) if f.endswith('.sqlite')])
    # 系统信息
    sys_info = {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage('/').percent,
        "platform": platform.platform()
    }
    return jsonify({
        "status": "success",
        "stats": {
            "users": user_count,
            "cache_files": cache_count,
            "cache_size_mb": round(cache_size, 2),
            "system": sys_info
        }
    })

# routes/admin_bp.py

@admin_bp.route('/api/admin/users', methods=['GET', 'POST'])
@admin_required
def api_admin_users():
    if request.method == 'POST':
        data = request.json
        role_manager.set_role(data['username'], data['role'])
        return jsonify({"status": "success"})
    
    users = []
    # 1. 修复 load() 调用 (前提是你已经按上面第1步修改了 managers.py)
    roles_data = role_manager.load() 
    
    # 2. 【重要修复】从 SQL 数据库中获取所有注册过的用户名，而不是扫描磁盘
    try:
        from managers import get_db
        with get_db() as conn:
            # 从 user_books 表中获取所有不重复的用户名
            cursor = conn.execute("SELECT DISTINCT username FROM user_books")
            usernames = [row[0] for row in cursor.fetchall()]
            
            for uname in usernames:
                if uname == 'default_user': continue # 过滤掉默认占位符
                
                role = "user"
                if uname in roles_data.get("admins", []):
                    role = "admin"
                elif uname in roles_data.get("pros", []):
                    role = "pro"
                
                users.append({"username": uname, "role": role})
    except Exception as e:
        print(f"Admin API Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "success", "users": users})

@admin_bp.route('/api/admin/clear_cache', methods=['POST'])
@admin_required
def api_admin_clear_cache():
    for f in os.listdir(CACHE_DIR):
        try: os.remove(os.path.join(CACHE_DIR, f))
        except: pass
    return jsonify({"status": "success", "msg": "Cache cleared"})

# 渲染管理面板页面
@admin_bp.route('/admin')
@admin_required
def admin_page():
    return render_template('admin.html')