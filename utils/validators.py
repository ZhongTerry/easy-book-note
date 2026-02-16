"""
输入验证工具
提供各种常用的输入验证函数
"""
import re
from urllib.parse import urlparse
from typing import Optional, List


class ValidationError(ValueError):
    """验证错误异常"""
    pass


class Validators:
    """验证器类集合"""
    
    @staticmethod
    def validate_url(url: str, allowed_schemes: List[str] = None) -> str:
        """
        验证 URL 格式
        
        Args:
            url: 待验证的 URL
            allowed_schemes: 允许的协议列表，默认为 ['http', 'https']
            
        Returns:
            str: 验证通过的 URL
            
        Raises:
            ValidationError: URL 格式不正确
            
        Examples:
            >>> Validators.validate_url("https://example.com")
            'https://example.com'
        """
        if not url:
            raise ValidationError("URL 不能为空")
        
        if len(url) > 2000:
            raise ValidationError("URL 过长（最大 2000 字符）")
        
        try:
            parsed = urlparse(url)
            
            if not parsed.scheme:
                raise ValidationError("URL 缺少协议（http/https）")
            
            if allowed_schemes is None:
                allowed_schemes = ['http', 'https']
            
            if parsed.scheme not in allowed_schemes:
                raise ValidationError(f"不支持的协议: {parsed.scheme}")
            
            if not parsed.netloc:
                raise ValidationError("URL 格式不正确")
            
            return url
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise ValidationError(f"URL 格式错误: {str(e)}")
    
    @staticmethod
    def validate_book_key(key: str) -> str:
        """
        验证书籍 key 格式
        
        Args:
            key: 书籍 key
            
        Returns:
            str: 验证通过的 key
            
        Raises:
            ValidationError: key 格式不正确
        """
        if not key:
            raise ValidationError("书籍 key 不能为空")
        
        if len(key) > 200:
            raise ValidationError("书籍 key 过长（最大 200 字符）")
        
        # key 应该只包含字母、数字、下划线、连字符、中文字符
        if not re.match(r'^[\w\-\u4e00-\u9fa5]+$', key):
            raise ValidationError("书籍 key 包含非法字符")
        
        return key
    
    @staticmethod
    def validate_username(username: str) -> str:
        """
        验证用户名格式
        
        Args:
            username: 用户名
            
        Returns:
            str: 验证通过的用户名
            
        Raises:
            ValidationError: 用户名格式不正确
        """
        if not username:
            raise ValidationError("用户名不能为空")
        
        if len(username) < 2:
            raise ValidationError("用户名至少 2 个字符")
        
        if len(username) > 50:
            raise ValidationError("用户名最多 50 个字符")
        
        # 用户名只能包含字母、数字、下划线
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            raise ValidationError("用户名只能包含字母、数字和下划线")
        
        return username
    
    @staticmethod
    def validate_integer(value: any, min_val: Optional[int] = None, 
                        max_val: Optional[int] = None, field_name: str = "值") -> int:
        """
        验证整数值
        
        Args:
            value: 待验证的值
            min_val: 最小值
            max_val: 最大值
            field_name: 字段名称（用于错误消息）
            
        Returns:
            int: 验证通过的整数
            
        Raises:
            ValidationError: 值不是有效整数或超出范围
        """
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{field_name} 必须是整数")
        
        if min_val is not None and int_value < min_val:
            raise ValidationError(f"{field_name} 不能小于 {min_val}")
        
        if max_val is not None and int_value > max_val:
            raise ValidationError(f"{field_name} 不能大于 {max_val}")
        
        return int_value
    
    @staticmethod
    def validate_string(value: str, min_length: Optional[int] = None,
                       max_length: Optional[int] = None, 
                       pattern: Optional[str] = None,
                       field_name: str = "字符串") -> str:
        """
        验证字符串
        
        Args:
            value: 待验证的字符串
            min_length: 最小长度
            max_length: 最大长度
            pattern: 正则表达式模式
            field_name: 字段名称
            
        Returns:
            str: 验证通过的字符串
            
        Raises:
            ValidationError: 字符串不符合要求
        """
        if not isinstance(value, str):
            raise ValidationError(f"{field_name} 必须是字符串")
        
        if min_length is not None and len(value) < min_length:
            raise ValidationError(f"{field_name} 长度不能小于 {min_length}")
        
        if max_length is not None and len(value) > max_length:
            raise ValidationError(f"{field_name} 长度不能大于 {max_length}")
        
        if pattern and not re.match(pattern, value):
            raise ValidationError(f"{field_name} 格式不正确")
        
        return value
    
    @staticmethod
    def validate_chapter_id(chapter_id: any) -> int:
        """
        验证章节 ID
        
        Args:
            chapter_id: 章节 ID
            
        Returns:
            int: 验证通过的章节 ID
            
        Raises:
            ValidationError: 章节 ID 不合法
        """
        try:
            cid = int(chapter_id)
            if cid < -1:  # -1 表示无效，0 及以上是有效章节
                raise ValidationError("章节 ID 不能小于 -1")
            return cid
        except (TypeError, ValueError):
            raise ValidationError("章节 ID 必须是整数")
    
    @staticmethod
    def validate_list(value: any, min_items: Optional[int] = None,
                     max_items: Optional[int] = None,
                     item_type: Optional[type] = None,
                     field_name: str = "列表") -> list:
        """
        验证列表
        
        Args:
            value: 待验证的值
            min_items: 最小元素数量
            max_items: 最大元素数量
            item_type: 元素类型
            field_name: 字段名称
            
        Returns:
            list: 验证通过的列表
            
        Raises:
            ValidationError: 列表不符合要求
        """
        if not isinstance(value, list):
            raise ValidationError(f"{field_name} 必须是列表")
        
        if min_items is not None and len(value) < min_items:
            raise ValidationError(f"{field_name} 至少需要 {min_items} 个元素")
        
        if max_items is not None and len(value) > max_items:
            raise ValidationError(f"{field_name} 最多允许 {max_items} 个元素")
        
        if item_type is not None:
            for i, item in enumerate(value):
                if not isinstance(item, item_type):
                    raise ValidationError(
                        f"{field_name} 的第 {i+1} 个元素类型不正确，"
                        f"期望 {item_type.__name__}"
                    )
        
        return value
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        清理文件名，移除非法字符
        
        Args:
            filename: 原始文件名
            
        Returns:
            str: 安全的文件名
        """
        if not filename:
            return "unnamed"
        
        # 移除路径分隔符和特殊字符
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', filename)
        
        # 限制长度
        max_length = 200
        if len(filename) > max_length:
            name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            name = name[:max_length - len(ext) - 1]
            filename = f"{name}.{ext}" if ext else name
        
        return filename or "unnamed"


def validate_request_data(data: dict, schema: dict) -> dict:
    """
    根据 schema 验证请求数据
    
    Args:
        data: 请求数据
        schema: 验证规则
        
    Returns:
        dict: 验证后的数据
        
    Raises:
        ValidationError: 数据验证失败
        
    Examples:
        schema = {
            'name': {'type': str, 'required': True, 'max_length': 50},
            'age': {'type': int, 'required': False, 'min': 0, 'max': 150}
        }
        validated = validate_request_data(request.json, schema)
    """
    validated_data = {}
    errors = []
    
    for field, rules in schema.items():
        value = data.get(field)
        
        # 检查必需字段
        if rules.get('required', False) and value is None:
            errors.append(f"字段 '{field}' 是必需的")
            continue
        
        # 如果不是必需且值为空，跳过
        if value is None:
            continue
        
        # 类型检查
        expected_type = rules.get('type')
        if expected_type and not isinstance(value, expected_type):
            errors.append(f"字段 '{field}' 类型错误，期望 {expected_type.__name__}")
            continue
        
        # 字符串长度检查
        if isinstance(value, str):
            min_length = rules.get('min_length')
            max_length = rules.get('max_length')
            
            if min_length and len(value) < min_length:
                errors.append(f"字段 '{field}' 长度不能小于 {min_length}")
                continue
            
            if max_length and len(value) > max_length:
                errors.append(f"字段 '{field}' 长度不能大于 {max_length}")
                continue
        
        # 数值范围检查
        if isinstance(value, (int, float)):
            min_val = rules.get('min')
            max_val = rules.get('max')
            
            if min_val is not None and value < min_val:
                errors.append(f"字段 '{field}' 不能小于 {min_val}")
                continue
            
            if max_val is not None and value > max_val:
                errors.append(f"字段 '{field}' 不能大于 {max_val}")
                continue
        
        # 自定义验证函数
        validator_func = rules.get('validator')
        if validator_func:
            try:
                value = validator_func(value)
            except ValidationError as e:
                errors.append(f"字段 '{field}' 验证失败: {str(e)}")
                continue
        
        validated_data[field] = value
    
    if errors:
        raise ValidationError("; ".join(errors))
    
    return validated_data
