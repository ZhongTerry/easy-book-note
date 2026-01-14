from flask import Blueprint, request, jsonify, render_template
import os
import psutil # 记得 pip install psutil
import platform
from shared import CACHE_DIR, USER_DATA_DIR, admin_required
from managers import role_manager, get_db, cluster_manager
from datetime import datetime, timedelta
import json

# 创建蓝图
admin_bp = Blueprint('admin', __name__)
# routes/admin_bp.py
# routes/admin_bp.py

@admin_bp.route('/api/cluster/heartbeat', methods=['POST'])
def handle_heartbeat():
    auth_header = request.headers.get('Authorization')
    # 默认 Token，生产环境请在 .env 设置
    system_token = os.environ.get('REMOTE_CRAWLER_TOKEN', 'my-secret-token-888')
    
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

@admin_bp.route('/api/admin/cluster_status')
@admin_required
def get_cluster_status():
    nodes = cluster_manager.get_active_nodes()
    # 计算总负载
    total_processing = sum(n['status']['current_tasks'] for n in nodes)
    return jsonify({
        "status": "success",
        "nodes": nodes,
        "summary": {
            "node_count": len(nodes),
            "total_processing": total_processing
        }
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