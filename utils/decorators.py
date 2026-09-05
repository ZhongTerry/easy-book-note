"""
常用装饰器集合
提供错误处理、性能监控、参数验证等装饰器
"""
from functools import wraps
from flask import request
import time
import traceback
from .api_response import APIResponse
from .logger import error as log_error, info as log_info


def handle_api_errors(f):
    """
    统一 API 错误处理装饰器
    
    捕获函数执行过程中的异常，并返回标准化的错误响应
    
    Examples:
        @app.route('/api/test')
        @handle_api_errors
        def test_api():
            # 如果这里抛出异常，会自动被捕获并返回标准错误响应
            return {"result": "success"}
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            # 参数错误
            log_error("API", f"ValueError in {f.__name__}: {str(e)}")
            return APIResponse.bad_request(str(e))
        except KeyError as e:
            # 缺少必需参数
            log_error("API", f"KeyError in {f.__name__}: {str(e)}")
            return APIResponse.bad_request(f"缺少必需参数: {str(e)}")
        except PermissionError as e:
            # 权限错误
            log_error("API", f"PermissionError in {f.__name__}: {str(e)}")
            return APIResponse.forbidden(str(e))
        except Exception as e:
            # 未预期的错误
            error_traceback = traceback.format_exc()
            log_error("API", f"Unexpected error in {f.__name__}: {str(e)}\n{error_traceback}")
            
            # 生产环境隐藏详细错误信息
            import os
            if os.environ.get('DEBUG') == '1':
                return APIResponse.internal_error(f"Internal error: {str(e)}")
            else:
                return APIResponse.internal_error()
    
    return wrapper


def monitor_performance(operation_name: str = None):
    """
    性能监控装饰器
    
    记录函数执行时间，用于性能分析
    
    Args:
        operation_name: 操作名称，用于日志记录
        
    Examples:
        @monitor_performance("爬取章节")
        def crawl_chapter(url):
            # ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            op_name = operation_name or f.__name__
            start_time = time.time()
            
            try:
                result = f(*args, **kwargs)
                duration = (time.time() - start_time) * 1000  # 转换为毫秒
                
                # 记录性能日志
                if duration > 3000:  # 超过3秒报错
                    log_error("Performance", f"❌ {op_name} 耗时 {duration:.0f}ms （严重超时）")
                elif duration > 1000:  # 超过1秒警告
                    log_info("Performance", f"⚠️ {op_name} 耗时 {duration:.0f}ms （慢）")
                else:
                    log_info("Performance", f"✅ {op_name} 耗时 {duration:.0f}ms")
                
                return result
            except Exception as e:
                duration = (time.time() - start_time) * 1000
                log_error("Performance", f"❌ {op_name} 执行失败，耗时 {duration:.0f}ms: {str(e)}")
                raise
        
        return wrapper
    return decorator


def validate_json(*required_fields):
    """
    JSON 参数验证装饰器
    
    验证 POST 请求的 JSON 数据是否包含必需字段
    
    Args:
        *required_fields: 必需的字段名列表
        
    Examples:
        @app.route('/api/test', methods=['POST'])
        @validate_json('name', 'age')
        def test_api():
            # 确保 request.json 包含 name 和 age 字段
            pass
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not request.is_json:
                return APIResponse.bad_request("请求必须是 JSON 格式")
            
            data = request.get_json()
            if not data:
                return APIResponse.bad_request("请求体为空")
            
            # 检查必需字段
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                return APIResponse.validation_error({
                    field: "该字段是必需的" for field in missing_fields
                })
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


def cache_result(ttl: int = 300):
    """
    结果缓存装饰器
    
    缓存函数返回结果，避免重复计算
    
    Args:
        ttl: 缓存时间（秒）
        
    Examples:
        @cache_result(ttl=600)
        def expensive_operation(param):
            # 耗时操作
            return result
    """
    cache = {}
    cache_time = {}
    
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"{f.__name__}:{str(args)}:{str(kwargs)}"
            
            # 检查缓存
            if cache_key in cache:
                cached_time = cache_time.get(cache_key, 0)
                if time.time() - cached_time < ttl:
                    log_info("Cache", f"✅ 缓存命中: {f.__name__}")
                    return cache[cache_key]
            
            # 执行函数
            result = f(*args, **kwargs)
            
            # 存入缓存
            cache[cache_key] = result
            cache_time[cache_key] = time.time()
            
            return result
        
        return wrapper
    return decorator


def rate_limit(max_requests: int = 60, window: int = 60):
    """
    速率限制装饰器
    
    限制单个 IP 在指定时间窗口内的请求次数
    
    Args:
        max_requests: 最大请求次数
        window: 时间窗口（秒）
        
    Examples:
        @app.route('/api/search')
        @rate_limit(max_requests=30, window=60)
        def search_api():
            pass
    """
    request_history = {}
    
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # 获取客户端 IP
            client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            if ',' in client_ip:
                client_ip = client_ip.split(',')[0].strip()
            
            current_time = time.time()
            
            # 清理过期记录
            if client_ip in request_history:
                request_history[client_ip] = [
                    t for t in request_history[client_ip]
                    if current_time - t < window
                ]
            else:
                request_history[client_ip] = []
            
            # 检查是否超过限制
            if len(request_history[client_ip]) >= max_requests:
                log_error("RateLimit", f"❌ IP {client_ip} 超过速率限制")
                return APIResponse.error(
                    429,
                    f"请求过于频繁，请 {window} 秒后再试",
                    "RATE_LIMIT_EXCEEDED"
                )
            
            # 记录本次请求
            request_history[client_ip].append(current_time)
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


def require_fields(**field_types):
    """
    字段类型验证装饰器
    
    验证请求参数的类型
    
    Args:
        **field_types: 字段名和类型的映射
        
    Examples:
        @app.route('/api/test', methods=['POST'])
        @require_fields(name=str, age=int, email=str)
        def test_api():
            pass
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            data = request.get_json() if request.is_json else request.form.to_dict()
            errors = {}
            
            for field, expected_type in field_types.items():
                if field not in data:
                    errors[field] = "该字段是必需的"
                    continue
                
                value = data[field]
                if not isinstance(value, expected_type):
                    errors[field] = f"类型错误，期望 {expected_type.__name__}"
            
            if errors:
                return APIResponse.validation_error(errors)
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator
