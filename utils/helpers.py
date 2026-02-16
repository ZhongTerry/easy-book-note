"""
常用工具函数
提供项目中通用的辅助功能
"""
import re
import hashlib
from typing import Optional, Dict, Any
from urllib.parse import urlparse


def normalize_host(host: str) -> str:
    """
    规范化主机名
    将 localhost、127.0.0.1、0.0.0.0 统一为 localhost
    
    Args:
        host: 主机名
        
    Returns:
        规范化后的主机名
    """
    return 'localhost' if host in ('localhost', '127.0.0.1', '0.0.0.0') else host


def extract_domain(url: str) -> Optional[str]:
    """
    从 URL 中提取域名
    
    Args:
        url: 完整URL
        
    Returns:
        域名，失败返回 None
    """
    try:
        parsed = urlparse(url)
        return parsed.hostname
    except:
        return None


def generate_cache_key(url: str) -> str:
    """
    生成缓存键
    使用 MD5 哈希生成唯一键
    
    Args:
        url: URL 或任意字符串
        
    Returns:
        MD5 哈希值
    """
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def extract_number_from_text(text: str) -> int:
    """
    从文本中提取第一个数字
    
    Args:
        text: 文本内容
        
    Returns:
        提取的数字，未找到返回 -1
    """
    match = re.search(r'\d+', text)
    return int(match.group()) if match else -1


def safe_int(value: Any, default: int = 0) -> int:
    """
    安全地将值转换为整数
    
    Args:
        value: 任意值
        default: 转换失败时的默认值
        
    Returns:
        整数值
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    安全地将值转换为浮点数
    
    Args:
        value: 任意值
        default: 转换失败时的默认值
        
    Returns:
        浮点数值
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断文本到指定长度
    
    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后缀
        
    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def is_valid_book_key(key: str) -> bool:
    """
    验证书籍键是否有效
    
    Args:
        key: 书籍键
        
    Returns:
        是否有效
    """
    if not key or not isinstance(key, str):
        return False
    # 只允许字母、数字、下划线、连字符
    return bool(re.match(r'^[\w\-]+$', key))


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除非法字符
    
    Args:
        filename: 原始文件名
        
    Returns:
        清理后的文件名
    """
    # 移除或替换非法字符
    illegal_chars = r'[<>:"/\\|?*]'
    return re.sub(illegal_chars, '_', filename)


def format_file_size(size_bytes: int) -> str:
    """
    格式化文件大小为人类可读格式
    
    Args:
        size_bytes: 字节数
        
    Returns:
        格式化后的字符串，如 "1.5 MB"
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def merge_dicts(base: Dict, updates: Dict) -> Dict:
    """
    深度合并两个字典
    
    Args:
        base: 基础字典
        updates: 更新字典
        
    Returns:
        合并后的字典
    """
    result = base.copy()
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def clamp(value: float, min_value: float, max_value: float) -> float:
    """
    将值限制在指定范围内
    
    Args:
        value: 原始值
        min_value: 最小值
        max_value: 最大值
        
    Returns:
        限制后的值
    """
    return max(min_value, min(value, max_value))
