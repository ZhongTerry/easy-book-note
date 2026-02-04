"""
全文缓存管理 API
提供全文下载、增量更新、删除、查询等功能
"""
from flask import Blueprint, request, jsonify, session, render_template
from shared import login_required, debug, info, warn, error
from managers import fulltext_cache_manager, db
from spider_core import crawler_instance as crawler

cache_bp = Blueprint('cache', __name__, url_prefix='/api/cache')


@cache_bp.route('/manager')
@login_required
def cache_manager_page():
    """缓存管理页面（增强版）"""
    return render_template('cache_manager_v2.html')


@cache_bp.route('/status/<book_key>', methods=['GET'])
@login_required
def get_cache_status(book_key):
    """获取指定书籍的缓存状态"""
    try:
        status = fulltext_cache_manager.get_cache_status(book_key)
        return jsonify(status)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/list', methods=['GET'])
@login_required
def list_all_caches():
    """列出当前用户的所有全文缓存"""
    try:
        result = fulltext_cache_manager.list_all_caches()
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/download', methods=['POST'])
@login_required
def start_download():
    """开始全文下载任务"""
    try:
        data = request.get_json()
        book_key = data.get('book_key')
        toc_url = data.get('toc_url')
        interval = data.get('interval', 0.5)  # 默认0.5秒间隔
        max_workers = data.get('max_workers', 8)  # 默认8线程
        
        if not book_key:
            return jsonify({'status': 'error', 'message': '缺少 book_key'}), 400
        
        # 如果没有提供 toc_url，尝试从数据库获取
        if not toc_url:
            book_data = db.get_full_data(book_key)
            if book_data:
                toc_url = book_data.get('url')
        
        if not toc_url:
            return jsonify({'status': 'error', 'message': '无法获取目录 URL'}), 400
        
        # 获取目录信息
        toc_data = crawler.get_toc(toc_url)
        if not toc_data or 'chapters' not in toc_data:
            return jsonify({'status': 'error', 'message': '获取目录失败'}), 400
        
        book_name = toc_data.get('book_name', book_key)
        chapters = toc_data['chapters']
        
        if not chapters:
            return jsonify({'status': 'error', 'message': '目录为空'}), 400
        
        # 启动下载任务
        task_id = fulltext_cache_manager.start_full_download(
            book_key=book_key,
            book_name=book_name,
            toc_url=toc_url,
            chapters=chapters,
            crawler_instance=crawler,
            interval=interval,
            max_workers=max_workers
        )
        
        return jsonify({
            'status': 'success',
            'message': '下载任务已启动',
            'task_id': task_id,
            'total_chapters': len(chapters)
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/task/<task_id>', methods=['GET'])
@login_required
def get_task_status(task_id):
    """获取下载任务状态"""
    try:
        status = fulltext_cache_manager.get_task_status(task_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/update/<book_key>', methods=['POST'])
@login_required
def incremental_update(book_key):
    """增量更新缓存"""
    try:
        result = fulltext_cache_manager.incremental_update(book_key, crawler)
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/delete/<book_key>', methods=['DELETE'])
@login_required
def delete_cache(book_key):
    """删除全文缓存"""
    try:
        result = fulltext_cache_manager.delete_cache(book_key)
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/chapter', methods=['POST'])
@login_required
def get_chapter_from_cache():
    """从缓存中获取特定章节"""
    try:
        data = request.get_json()
        book_key = data.get('book_key')
        chapter_url = data.get('chapter_url')
        
        if not book_key or not chapter_url:
            return jsonify({'status': 'error', 'message': '缺少必要参数'}), 400
        
        result = fulltext_cache_manager.get_chapter_from_cache(book_key, chapter_url)
        
        if result:
            return jsonify({'status': 'success', 'data': result})
        else:
            return jsonify({'status': 'error', 'message': '章节未缓存'}), 404
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/batch_status', methods=['POST'])
@login_required
def batch_get_status():
    """批量获取多本书的缓存状态"""
    try:
        data = request.get_json()
        book_keys = data.get('book_keys', [])
        
        if not isinstance(book_keys, list):
            return jsonify({'status': 'error', 'message': 'book_keys 必须是数组'}), 400
        
        results = {}
        for book_key in book_keys:
            results[book_key] = fulltext_cache_manager.get_cache_status(book_key)
        
        return jsonify({'status': 'success', 'data': results})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ==================== 任务控制接口 ====================

@cache_bp.route('/tasks', methods=['GET'])
@login_required
def list_active_tasks():
    """列出所有活动任务"""
    try:
        result = fulltext_cache_manager.list_active_tasks()
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/task/<task_id>/pause', methods=['POST'])
@login_required
def pause_task(task_id):
    """暂停任务"""
    try:
        result = fulltext_cache_manager.pause_task(task_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/task/<task_id>/resume', methods=['POST'])
@login_required
def resume_task(task_id):
    """继续任务"""
    try:
        result = fulltext_cache_manager.resume_task(task_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/task/<task_id>/cancel', methods=['POST'])
@login_required
def cancel_task(task_id):
    """取消任务"""
    try:
        result = fulltext_cache_manager.cancel_task(task_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@cache_bp.route('/task/<task_id>/settings', methods=['PUT'])
@login_required
def update_task_settings(task_id):
    """更新任务设置（仅暂停状态）"""
    try:
        data = request.get_json()
        interval = data.get('interval')
        max_workers = data.get('max_workers')
        
        result = fulltext_cache_manager.update_task_settings(
            task_id, 
            interval=interval, 
            max_workers=max_workers
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

