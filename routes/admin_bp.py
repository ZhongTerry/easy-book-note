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

@admin_bp.route('/api/admin/users', methods=['GET', 'POST'])
@admin_required
def api_admin_users():
    if request.method == 'POST':
        data = request.json
        role_manager.set_role(data['username'], data['role'])
        return jsonify({"status": "success"})
    
    users = []
    roles_data = role_manager.load()
    for f in os.listdir(USER_DATA_DIR):
        if f.endswith('.sqlite'):
            uname = f.replace('.sqlite', '')
            role = "admin" if uname in roles_data["admins"] else ("pro" if uname in roles_data["pros"] else "user")
            users.append({"username": uname, "role": role})
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