from flask import Blueprint, request, jsonify, render_template
import os
import psutil # 记得 pip install psutil
import platform
from shared import CACHE_DIR, USER_DATA_DIR, admin_required
from managers import role_manager

# 创建蓝图
admin_bp = Blueprint('admin', __name__)

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