"""
优化后的 API 路由示例
展示如何使用新的工具模块改进代码质量
"""
from flask import Blueprint, request
from utils import (
    APIResponse, 
    handle_api_errors, 
    validate_json, 
    monitor_performance,
    rate_limit,
    Validators,
    ValidationError,
    info, error
)
from shared import login_required, pro_required, is_safe_url
from services import book_service

# 创建蓝图
api_v2_bp = Blueprint('api_v2', __name__, url_prefix='/api/v2')


@api_v2_bp.route('/books', methods=['GET'])
@login_required
@handle_api_errors
@monitor_performance("获取书架")
def get_books():
    """
    获取用户书架列表（优化版）
    
    支持过滤和搜索功能
    """
    from flask import session
    
    username = session.get('user', {}).get('username')
    if not username:
        return APIResponse.unauthorized()
    
    # 获取过滤参数
    filters = {}
    if request.args.get('tag'):
        filters['tag'] = request.args.get('tag')
    if request.args.get('keyword'):
        filters['keyword'] = request.args.get('keyword')
    if request.args.get('has_update'):
        filters['has_update'] = request.args.get('has_update') == 'true'
    
    # 使用服务层获取数据
    result = book_service.get_user_library(username, filters)
    
    info("API", f"获取书架: {username}, 共 {result['stats']['total']} 本书")
    
    return APIResponse.success(
        data=result,
        message="获取成功"
    )


@api_v2_bp.route('/books', methods=['POST'])
@login_required
@handle_api_errors
@validate_json('key', 'data')
@monitor_performance("保存书籍")
def save_book():
    """
    保存书籍（优化版）
    
    使用统一的验证和错误处理
    """
    from flask import session
    
    username = session.get('user', {}).get('username')
    if not username:
        return APIResponse.unauthorized()
    
    try:
        # 获取请求数据
        request_data = request.get_json()
        book_key = request_data.get('key')
        book_data = request_data.get('data')
        
        # 验证参数
        book_key = Validators.validate_book_key(book_key)
        
        if not isinstance(book_data, dict):
            return APIResponse.bad_request("书籍数据格式错误")
        
        # 使用服务层保存
        result = book_service.save_book(username, book_key, book_data)
        
        if result['status'] == 'success':
            return APIResponse.success(
                data={'key': book_key},
                message="保存成功"
            )
        else:
            return APIResponse.error(400, result.get('msg', '保存失败'))
            
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


@api_v2_bp.route('/books/<book_key>', methods=['DELETE'])
@login_required
@handle_api_errors
@monitor_performance("删除书籍")
def delete_book(book_key):
    """
    删除书籍（优化版）
    """
    from flask import session
    
    username = session.get('user', {}).get('username')
    if not username:
        return APIResponse.unauthorized()
    
    try:
        # 验证 book_key
        book_key = Validators.validate_book_key(book_key)
        
        # 使用服务层删除
        result = book_service.delete_book(username, book_key)
        
        if result['status'] == 'success':
            return APIResponse.success(message="删除成功")
        else:
            return APIResponse.error(400, result.get('msg', '删除失败'))
            
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


@api_v2_bp.route('/books/<book_key>/progress', methods=['POST'])
@login_required
@handle_api_errors
@validate_json('url', 'title')
@monitor_performance("更新进度")
def update_progress(book_key):
    """
    更新阅读进度（优化版）
    """
    from flask import session
    
    username = session.get('user', {}).get('username')
    if not username:
        return APIResponse.unauthorized()
    
    try:
        # 验证参数
        book_key = Validators.validate_book_key(book_key)
        
        request_data = request.get_json()
        chapter_url = Validators.validate_url(request_data.get('url'))
        chapter_title = Validators.validate_string(
            request_data.get('title'),
            min_length=1,
            max_length=500,
            field_name="章节标题"
        )
        
        chapter_id = request_data.get('chapter_id', -1)
        if chapter_id != -1:
            chapter_id = Validators.validate_chapter_id(chapter_id)
        
        # 使用服务层更新进度
        result = book_service.update_reading_progress(
            username, book_key, chapter_url, chapter_title, chapter_id
        )
        
        if result['status'] == 'success':
            return APIResponse.success(message="进度已保存")
        else:
            return APIResponse.error(400, result.get('msg', '保存失败'))
            
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


@api_v2_bp.route('/books/<book_key>/stats', methods=['GET'])
@login_required
@handle_api_errors
@monitor_performance("获取统计")
def get_book_stats(book_key):
    """
    获取书籍统计信息（优化版）
    """
    from flask import session
    
    username = session.get('user', {}).get('username')
    if not username:
        return APIResponse.unauthorized()
    
    try:
        # 验证参数
        book_key = Validators.validate_book_key(book_key)
        
        # 获取统计信息
        stats = book_service.get_book_statistics(username, book_key)
        
        return APIResponse.success(
            data=stats,
            message="获取成功"
        )
        
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


@api_v2_bp.route('/search', methods=['POST'])
@login_required
@handle_api_errors
@validate_json('keyword')
@rate_limit(max_requests=30, window=60)  # 限制搜索频率
@monitor_performance("搜索")
def search_books():
    """
    搜索书籍（优化版 + 限流）
    """
    try:
        request_data = request.get_json()
        keyword = Validators.validate_string(
            request_data.get('keyword'),
            min_length=1,
            max_length=100,
            field_name="搜索关键词"
        )
        
        # 这里调用搜索逻辑
        from spider_core import searcher
        
        results = searcher.search(keyword)
        
        info("API", f"搜索: {keyword}, 找到 {len(results)} 个结果")
        
        return APIResponse.success(
            data={'results': results},
            message="搜索完成"
        )
        
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


@api_v2_bp.route('/crawler/run', methods=['POST'])
@login_required
@handle_api_errors
@validate_json('url')
@monitor_performance("爬取章节")
def crawl_chapter():
    """
    爬取章节内容（优化版）
    
    增加了完整的参数验证和安全检查
    """
    try:
        request_data = request.get_json()
        
        # 验证 URL
        url = Validators.validate_url(request_data.get('url'))
        
        # SSRF 安全检查
        if not is_safe_url(url):
            return APIResponse.forbidden("该 URL 不被允许访问")
        
        # 调用爬虫
        from spider_core import crawler_instance
        
        result = crawler_instance.run(url)
        
        if result:
            return APIResponse.success(
                data=result,
                message="爬取成功"
            )
        else:
            return APIResponse.error(500, "爬取失败，请检查 URL 是否有效")
            
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


# ================================
# 高级功能示例
# ================================

@api_v2_bp.route('/books/batch', methods=['POST'])
@pro_required
@handle_api_errors
@validate_json('operations')
@monitor_performance("批量操作")
def batch_operations():
    """
    批量操作书籍（Pro 功能）
    
    支持批量删除、批量修改标签等
    """
    from flask import session
    
    username = session.get('user', {}).get('username')
    if not username:
        return APIResponse.unauthorized()
    
    try:
        request_data = request.get_json()
        operations = request_data.get('operations', [])
        
        # 验证操作列表
        operations = Validators.validate_list(
            operations,
            min_items=1,
            max_items=100,
            item_type=dict,
            field_name="操作列表"
        )
        
        results = []
        success_count = 0
        fail_count = 0
        
        # 执行批量操作
        for op in operations:
            op_type = op.get('type')
            book_key = op.get('key')
            
            try:
                if op_type == 'delete':
                    result = book_service.delete_book(username, book_key)
                elif op_type == 'update_tags':
                    # 更新标签逻辑
                    pass
                else:
                    result = {'status': 'error', 'msg': f'未知操作: {op_type}'}
                
                if result['status'] == 'success':
                    success_count += 1
                else:
                    fail_count += 1
                
                results.append({
                    'key': book_key,
                    'status': result['status'],
                    'msg': result.get('msg')
                })
                
            except Exception as e:
                fail_count += 1
                results.append({
                    'key': book_key,
                    'status': 'error',
                    'msg': str(e)
                })
        
        return APIResponse.success(
            data={
                'results': results,
                'summary': {
                    'total': len(operations),
                    'success': success_count,
                    'failed': fail_count
                }
            },
            message=f"批量操作完成: 成功 {success_count}, 失败 {fail_count}"
        )
        
    except ValidationError as e:
        return APIResponse.bad_request(str(e))


@api_v2_bp.route('/health', methods=['GET'])
def health_check():
    """
    健康检查接口
    """
    import os
    
    status = {
        'status': 'healthy',
        'version': '2.0',
        'timestamp': int(time.time()),
        'services': {
            'database': 'ok',
            'cache': 'ok',
            'crawler': 'ok'
        }
    }
    
    # 检查 Redis 连接
    try:
        import managers
        if managers.cluster_manager.use_redis:
            managers.cluster_manager.r.ping()
            status['services']['redis'] = 'ok'
        else:
            status['services']['redis'] = 'disabled'
    except:
        status['services']['redis'] = 'error'
        status['status'] = 'degraded'
    
    return APIResponse.success(data=status)


# 注册错误处理器
@api_v2_bp.errorhandler(404)
def not_found(error):
    return APIResponse.not_found("API 端点不存在")


@api_v2_bp.errorhandler(500)
def internal_error(error):
    return APIResponse.internal_error()
