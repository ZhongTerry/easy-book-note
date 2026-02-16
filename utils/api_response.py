"""
统一 API 响应格式
提供标准化的 API 响应结构，确保前后端交互的一致性
"""
from flask import jsonify
import time
from typing import Any, Dict, Optional


class APIResponse:
    """统一 API 响应格式类"""
    
    @staticmethod
    def success(data: Any = None, message: str = "操作成功", **kwargs) -> tuple:
        """
        返回成功响应
        
        Args:
            data: 响应数据，可以是任意可序列化的对象
            message: 成功消息
            **kwargs: 额外的响应字段
            
        Returns:
            tuple: (响应体, 状态码)
            
        Examples:
            >>> APIResponse.success({"books": []})
            >>> APIResponse.success(message="保存成功")
        """
        response = {
            "status": "success",
            "code": 200,
            "message": message,
            "data": data,
            "timestamp": int(time.time())
        }
        response.update(kwargs)
        return jsonify(response), 200
    
    @staticmethod
    def error(
        code: int = 400,
        message: str = "操作失败",
        error_type: Optional[str] = None,
        details: Optional[Dict] = None,
        **kwargs
    ) -> tuple:
        """
        返回错误响应
        
        Args:
            code: HTTP 状态码
            message: 错误消息
            error_type: 错误类型（如 VALIDATION_ERROR, NETWORK_ERROR 等）
            details: 详细错误信息
            **kwargs: 额外的响应字段
            
        Returns:
            tuple: (响应体, 状态码)
            
        Examples:
            >>> APIResponse.error(400, "参数错误", "INVALID_INPUT")
            >>> APIResponse.error(500, "服务器内部错误", details={"trace": "..."})
        """
        response = {
            "status": "error",
            "code": code,
            "message": message,
            "timestamp": int(time.time())
        }
        
        if error_type:
            response["error_type"] = error_type
            
        if details:
            response["details"] = details
            
        response.update(kwargs)
        return jsonify(response), code
    
    @staticmethod
    def not_found(message: str = "资源不存在") -> tuple:
        """返回 404 错误"""
        return APIResponse.error(404, message, "NOT_FOUND")
    
    @staticmethod
    def unauthorized(message: str = "未授权，请先登录") -> tuple:
        """返回 401 错误"""
        return APIResponse.error(401, message, "UNAUTHORIZED")
    
    @staticmethod
    def forbidden(message: str = "权限不足") -> tuple:
        """返回 403 错误"""
        return APIResponse.error(403, message, "FORBIDDEN")
    
    @staticmethod
    def bad_request(message: str = "请求参数错误", details: Optional[Dict] = None) -> tuple:
        """返回 400 错误"""
        return APIResponse.error(400, message, "BAD_REQUEST", details)
    
    @staticmethod
    def internal_error(message: str = "服务器内部错误") -> tuple:
        """返回 500 错误"""
        return APIResponse.error(500, message, "INTERNAL_ERROR")
    
    @staticmethod
    def validation_error(errors: Dict) -> tuple:
        """
        返回参数验证错误
        
        Args:
            errors: 验证错误字典，格式如 {"field": "error message"}
        """
        return APIResponse.error(
            400,
            "参数验证失败",
            "VALIDATION_ERROR",
            details={"validation_errors": errors}
        )


# 便捷函数
def success(data=None, message="操作成功", **kwargs):
    """便捷成功响应函数"""
    return APIResponse.success(data, message, **kwargs)


def error(code=400, message="操作失败", **kwargs):
    """便捷错误响应函数"""
    return APIResponse.error(code, message, **kwargs)
