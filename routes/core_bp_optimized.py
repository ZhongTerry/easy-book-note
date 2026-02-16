"""
优化后的核心API路由示例
展示如何使用新工具优化现有API
"""
from flask import Blueprint, request, jsonify, session
from utils.decorators import (
    handle_api_errors, 
    monitor_performance, 
    validate_json, 
    require_fields,
    rate_limit
)
from utils.api_response import APIResponse
from utils.validators import Validators
from services.book_service import BookService
from shared import login_required
import managers
from spider_core import crawler_instance as crawler, searcher

# 创建蓝图
optimized_bp = Blueprint('optimized', __name__, url_prefix='/api/v2')

# 初始化服务
book_service = BookService()
validators = Validators()

# ========================================
# 优化示例 1: 搜索API (添加性能监控和错误处理)
# ========================================

@optimized_bp.route('/search', methods=['POST'])
@handle_api_errors
@monitor_performance  # 自动记录性能
@rate_limit(max_calls=30, window=60)  # 限速：60秒30次
@require_fields(['keyword'])
def search_books():
    """
    优化后的搜索API
    - 自动错误处理
    - 性能监控
    - 限速保护
    - 输入验证
    """
    data = request.get_json()
    keyword = data.get('keyword', '').strip()
    
    # 输入验证
    if not validators.validate_string(keyword, min_length=1, max_length=100):
        return APIResponse.validation_error("搜索关键词长度需在1-100之间")
    
    # 获取用户配置
    username = session.get('username', 'guest')
    
    try:
        # 执行搜索
        results = searcher.search_universal(keyword)
        
        # 数据增强（添加用户相关信息）
        for item in results:
            # 检查用户是否已收藏
            book_key = f"{item.get('domain', '')}:{item.get('book_id', '')}"
            item['is_collected'] = managers.lib_manager.is_book_collected(username, book_key)
        
        return APIResponse.success({
            'results': results,
            'count': len(results),
            'keyword': keyword
        })
    
    except Exception as e:
        return APIResponse.error(f"搜索失败: {str(e)}")


# ========================================
# 优化示例 2: 书库API (使用服务层)
# ========================================

@optimized_bp.route('/library', methods=['GET'])
@login_required
@handle_api_errors
@monitor_performance
def get_library():
    """
    优化后的书库API
    - 使用服务层（避免N+1查询）
    - 支持过滤和排序
    - 分页支持
    """
    username = session.get('username')
    
    # 获取查询参数
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    sort_by = request.args.get('sort_by', 'update_time')
    
    # 参数验证
    if not validators.validate_integer(page, min_val=1, max_val=10000):
        return APIResponse.validation_error("页码需在1-10000之间")
    
    if not validators.validate_integer(per_page, min_val=1, max_val=100):
        return APIResponse.validation_error("每页数量需在1-100之间")
    
    # 使用服务层获取数据（已优化查询）
    books = book_service.get_user_library(
        username,
        sort_by=sort_by,
        page=page,
        per_page=per_page
    )
    
    # 计算总数
    total = len(managers.lib_manager.get_user_library(username))
    
    return APIResponse.success({
        'books': books,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page
    })


# ========================================
# 优化示例 3: 添加书籍API (完整验证)
# ========================================

@optimized_bp.route('/library/book', methods=['POST'])
@login_required
@handle_api_errors
@validate_json(['book_key', 'title'])
@monitor_performance
def add_book():
    """
    优化后的添加书籍API
    - JSON自动验证
    - 完整的输入验证
    - 使用服务层保存
    """
    username = session.get('username')
    data = request.get_json()
    
    book_key = data.get('book_key')
    title = data.get('title')
    author = data.get('author', '未知作者')
    cover = data.get('cover', '')
    
    # 验证book_key格式
    if not validators.validate_book_key(book_key):
        return APIResponse.validation_error("book_key格式错误，需为 'domain:book_id'")
    
    # 验证标题
    if not validators.validate_string(title, min_length=1, max_length=100):
        return APIResponse.validation_error("书名长度需在1-100之间")
    
    # 检查是否已存在
    if book_service.is_book_exists(username, book_key):
        return APIResponse.error("该书籍已在书库中", code=400)
    
    # 保存书籍
    success = book_service.save_book(
        username=username,
        book_key=book_key,
        title=title,
        author=author,
        cover=cover
    )
    
    if success:
        return APIResponse.success({
            'message': '添加成功',
            'book_key': book_key
        }, code=201)
    else:
        return APIResponse.error("添加失败")


# ========================================
# 优化示例 4: 删除书籍API (批量支持)
# ========================================

@optimized_bp.route('/library/book', methods=['DELETE'])
@login_required
@handle_api_errors
@validate_json(['book_keys'])
def delete_books():
    """
    优化后的删除API
    - 支持批量删除
    - 返回详细结果
    """
    username = session.get('username')
    data = request.get_json()
    book_keys = data.get('book_keys', [])
    
    # 验证输入
    if not validators.validate_list(book_keys, min_length=1, max_length=100):
        return APIResponse.validation_error("一次最多删除100本书")
    
    # 验证每个book_key
    for book_key in book_keys:
        if not validators.validate_book_key(book_key):
            return APIResponse.validation_error(f"无效的book_key: {book_key}")
    
    # 批量删除
    success_count = 0
    failed_keys = []
    
    for book_key in book_keys:
        if book_service.delete_book(username, book_key):
            success_count += 1
        else:
            failed_keys.append(book_key)
    
    return APIResponse.success({
        'message': f'成功删除 {success_count}/{len(book_keys)} 本书',
        'success_count': success_count,
        'failed_keys': failed_keys
    })


# ========================================
# 优化示例 5: 爬取章节API (SSRF保护)
# ========================================

@optimized_bp.route('/crawler/fetch', methods=['POST'])
@login_required
@handle_api_errors
@monitor_performance
@rate_limit(max_calls=60, window=60)  # 限速
@require_fields(['url'])
def fetch_content():
    """
    优化后的爬取API
    - SSRF保护
    - URL验证
    - 限速保护
    """
    data = request.get_json()
    url = data.get('url')
    
    # URL验证（防止SSRF）
    allowed_domains = [
        'fanqienovel.com',
        'book.sxgread.com',
        'xbqg77.com',
        'biquge365.pro'
    ]
    
    if not validators.validate_url(url, allowed_domains=allowed_domains):
        return APIResponse.validation_error(
            "URL格式错误或域名不在允许列表中",
            details={'allowed_domains': allowed_domains}
        )
    
    try:
        # 执行爬取
        result = crawler.fetch_page(url)
        
        if result and result.get('status') == 'success':
            return APIResponse.success({
                'data': result.get('data'),
                'page_type': result.get('page_type', 'unknown')
            })
        else:
            return APIResponse.error("爬取失败", details=result)
    
    except Exception as e:
        return APIResponse.error(f"爬取异常: {str(e)}")


# ========================================
# 优化示例 6: 阅读进度API (使用服务层)
# ========================================

@optimized_bp.route('/reading/progress', methods=['POST'])
@login_required
@handle_api_errors
@require_fields(['book_key', 'chapter_index'])
def update_progress():
    """
    优化后的阅读进度API
    - 使用服务层
    - 完整验证
    """
    username = session.get('username')
    data = request.get_json()
    
    book_key = data.get('book_key')
    chapter_index = data.get('chapter_index')
    chapter_title = data.get('chapter_title', '')
    
    # 验证
    if not validators.validate_book_key(book_key):
        return APIResponse.validation_error("book_key格式错误")
    
    if not validators.validate_integer(chapter_index, min_val=0):
        return APIResponse.validation_error("章节索引必须为非负整数")
    
    # 更新进度
    success = book_service.update_reading_progress(
        username=username,
        book_key=book_key,
        chapter_index=chapter_index,
        chapter_title=chapter_title
    )
    
    if success:
        return APIResponse.success({'message': '进度已更新'})
    else:
        return APIResponse.error("更新失败")


# ========================================
# 优化示例 7: 统计API (数据聚合)
# ========================================

@optimized_bp.route('/stats', methods=['GET'])
@login_required
@handle_api_errors
@monitor_performance
def get_stats():
    """
    优化后的统计API
    - 数据聚合
    - 缓存友好
    """
    username = session.get('username')
    
    # 获取各类统计数据
    library = managers.lib_manager.get_user_library(username)
    
    # 计算统计信息
    total_books = len(library)
    
    # 按状态分组
    reading_count = sum(1 for book in library.values() 
                       if book.get('status') == 'reading')
    finished_count = sum(1 for book in library.values() 
                        if book.get('status') == 'finished')
    
    # 最近阅读
    recent_books = sorted(
        library.values(),
        key=lambda x: x.get('last_read_time', 0),
        reverse=True
    )[:5]
    
    return APIResponse.success({
        'total_books': total_books,
        'reading_count': reading_count,
        'finished_count': finished_count,
        'recent_books': [{
            'book_key': book['book_key'],
            'title': book['title'],
            'author': book['author'],
            'current_chapter': book.get('current_chapter', 0),
            'last_read_time': book.get('last_read_time', 0)
        } for book in recent_books]
    })


# ========================================
# 优化示例 8: 健康检查API
# ========================================

@optimized_bp.route('/health', methods=['GET'])
@handle_api_errors
def health_check():
    """
    健康检查API
    - 无需认证
    - 快速响应
    """
    import time
    
    # 检查数据库连接
    db_ok = False
    try:
        conn = managers.get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        db_ok = True
    except:
        pass
    
    return APIResponse.success({
        'status': 'healthy' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
        'timestamp': int(time.time())
    })


# ========================================
# 注册蓝图提示
# ========================================
"""
在 dbserver.py 中注册此蓝图：

from routes.core_bp_optimized import optimized_bp
app.register_blueprint(optimized_bp)

然后前端可以调用：
- POST /api/v2/search
- GET /api/v2/library
- POST /api/v2/library/book
- DELETE /api/v2/library/book
- POST /api/v2/crawler/fetch
- POST /api/v2/reading/progress
- GET /api/v2/stats
- GET /api/v2/health
"""
