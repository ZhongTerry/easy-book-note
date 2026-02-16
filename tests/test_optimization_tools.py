"""
优化工具测试套件
验证所有新工具的功能
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import Mock, patch, MagicMock
from flask import Flask
import time

# 导入要测试的模块
from utils.api_response import APIResponse
from utils.validators import Validators
from utils.decorators import (
    handle_api_errors, 
    monitor_performance, 
    cache_result,
    rate_limit
)


class TestAPIResponse(unittest.TestCase):
    """测试API响应格式"""
    
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
    
    def tearDown(self):
        self.ctx.pop()
    
    def test_success_response(self):
        """测试成功响应"""
        response = APIResponse.success({'key': 'value'})
        data = response[0].get_json()
        
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['data'], {'key': 'value'})
        self.assertEqual(response[1], 200)
    
    def test_error_response(self):
        """测试错误响应"""
        response = APIResponse.error(500, 'Test error')
        data = response[0].get_json()
        
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['message'], 'Test error')
        self.assertEqual(response[1], 500)
    
    def test_validation_error(self):
        """测试验证错误"""
        response = APIResponse.validation_error({'field': 'error message'})
        data = response[0].get_json()
        
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['code'], 400)
        self.assertIn('validation_errors', data['details'])
    
    def test_not_found(self):
        """测试404响应"""
        response = APIResponse.not_found('Resource not found')
        data = response[0].get_json()
        
        self.assertEqual(data['status'], 'error')
        self.assertEqual(response[1], 404)
    
    def test_unauthorized(self):
        """测试401响应"""
        response = APIResponse.unauthorized('Please login')
        data = response[0].get_json()
        
        self.assertEqual(data['status'], 'error')
        self.assertEqual(response[1], 401)


class TestValidators(unittest.TestCase):
    """测试验证器"""
    
    def setUp(self):
        self.validators = Validators()
    
    def test_validate_url_valid(self):
        """测试有效URL"""
        self.assertTrue(
            self.validators.validate_url('https://fanqienovel.com/page/123')
        )
    
    def test_validate_url_invalid_scheme(self):
        """测试无效URL协议"""
        from utils.validators import ValidationError
        with self.assertRaises(ValidationError):
            self.validators.validate_url('ftp://example.com')
    
    def test_validate_url_with_allowed_schemes(self):
        """测试协议白名单"""
        # 测试 https 协议（在默认白名单中）
        result = self.validators.validate_url('https://fanqienovel.com/book/123')
        self.assertEqual(result, 'https://fanqienovel.com/book/123')
        
        # 测试只允许 http
        from utils.validators import ValidationError
        with self.assertRaises(ValidationError):
            self.validators.validate_url(
                'https://example.com',
                allowed_schemes=['http']
            )
    
    def test_validate_book_key_valid(self):
        """测试有效的book_key"""
        # book_key 格式应该是 domain_bookid，不包含冒号
        result1 = self.validators.validate_book_key('fanqie_123456')
        self.assertEqual(result1, 'fanqie_123456')
        
        result2 = self.validators.validate_book_key('sxg-7890')
        self.assertEqual(result2, 'sxg-7890')
    
    def test_validate_book_key_invalid(self):
        """测试无效的book_key"""
        from utils.validators import ValidationError
        
        # 空字符串
        with self.assertRaises(ValidationError):
            self.validators.validate_book_key('')
        
        # 包含非法字符（冒号不允许）
        with self.assertRaises(ValidationError):
            self.validators.validate_book_key('fanqie:123')
    
    def test_validate_username(self):
        """测试用户名验证"""
        from utils.validators import ValidationError
        
        result1 = self.validators.validate_username('user123')
        self.assertEqual(result1, 'user123')
        
        result2 = self.validators.validate_username('user_name')
        self.assertEqual(result2, 'user_name')
        
        # 太短（少于2个字符）
        with self.assertRaises(ValidationError):
            self.validators.validate_username('a')
        
        # 非法字符
        with self.assertRaises(ValidationError):
            self.validators.validate_username('user@123')
    
    def test_validate_integer(self):
        """测试整数验证"""
        from utils.validators import ValidationError
        
        result = self.validators.validate_integer(5, min_val=1, max_val=10)
        self.assertEqual(result, 5)
        
        # 小于最小值
        with self.assertRaises(ValidationError):
            self.validators.validate_integer(0, min_val=1, max_val=10)
        
        # 大于最大值
        with self.assertRaises(ValidationError):
            self.validators.validate_integer(11, min_val=1, max_val=10)
        
        # 非整数
        with self.assertRaises(ValidationError):
            self.validators.validate_integer('not_int', min_val=1, max_val=10)
    
    def test_validate_string(self):
        """测试字符串验证"""
        from utils.validators import ValidationError
        
        result = self.validators.validate_string('hello', min_length=1, max_length=10)
        self.assertEqual(result, 'hello')
        
        # 空字符串（太短）
        with self.assertRaises(ValidationError):
            self.validators.validate_string('', min_length=1, max_length=10)
        
        # 太长
        with self.assertRaises(ValidationError):
            self.validators.validate_string('a' * 20, min_length=1, max_length=10)
    
    def test_validate_list(self):
        """测试列表验证"""
        from utils.validators import ValidationError
        
        result = self.validators.validate_list([1, 2, 3], min_items=1, max_items=5)
        self.assertEqual(result, [1, 2, 3])
        
        # 空列表
        with self.assertRaises(ValidationError):
            self.validators.validate_list([], min_items=1, max_items=5)
        
        # 太多元素
        with self.assertRaises(ValidationError):
            self.validators.validate_list([1] * 10, min_items=1, max_items=5)
        
        # 非列表
        with self.assertRaises(ValidationError):
            self.validators.validate_list('not_list', min_items=1, max_items=5)


class TestDecorators(unittest.TestCase):
    """测试装饰器"""
    
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
    
    def test_handle_api_errors_success(self):
        """测试错误处理装饰器 - 成功情况"""
        @self.app.route('/test')
        @handle_api_errors
        def test_route():
            return {'status': 'success'}
        
        response = self.client.get('/test')
        self.assertEqual(response.status_code, 200)
    
    def test_handle_api_errors_exception(self):
        """测试错误处理装饰器 - 异常情况"""
        @self.app.route('/test')
        @handle_api_errors
        def test_route():
            raise ValueError("Test error")
        
        response = self.client.get('/test')
        data = response.get_json()
        
        self.assertEqual(data['status'], 'error')
        self.assertIn('Test error', data['message'])
    
    def test_monitor_performance(self):
        """测试性能监控装饰器"""
        call_count = [0]
        
        @monitor_performance()  # 需要调用装饰器
        def slow_function():
            call_count[0] += 1
            time.sleep(0.1)
            return 'done'
        
        result = slow_function()
        
        self.assertEqual(result, 'done')
        self.assertEqual(call_count[0], 1)
    
    def test_cache_result(self):
        """测试缓存装饰器"""
        call_count = [0]
        
        @cache_result(ttl=1)
        def expensive_function(x):
            call_count[0] += 1
            return x * 2
        
        # 第一次调用
        result1 = expensive_function(5)
        self.assertEqual(result1, 10)
        self.assertEqual(call_count[0], 1)
        
        # 第二次调用（应该使用缓存）
        result2 = expensive_function(5)
        self.assertEqual(result2, 10)
        self.assertEqual(call_count[0], 1)  # 没有增加
        
        # 不同参数（不使用缓存）
        result3 = expensive_function(10)
        self.assertEqual(result3, 20)
        self.assertEqual(call_count[0], 2)
    
    def test_rate_limit(self):
        """测试限速装饰器"""
        @self.app.route('/limited')
        @rate_limit(max_requests=2, window=1)
        def limited_route():
            return {'status': 'success'}
        
        # 前两次调用应该成功
        response1 = self.client.get('/limited')
        self.assertEqual(response1.status_code, 200)
        
        response2 = self.client.get('/limited')
        self.assertEqual(response2.status_code, 200)
        
        # 第三次调用应该被限速
        response3 = self.client.get('/limited')
        self.assertEqual(response3.status_code, 429)
        
        # 等待窗口过期
        time.sleep(1.1)
        
        # 应该可以再次调用
        response4 = self.client.get('/limited')
        self.assertEqual(response4.status_code, 200)


class TestIntegration(unittest.TestCase):
    """集成测试"""
    
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
    
    def test_complete_api_flow(self):
        """测试完整的API流程"""
        from flask import request
        
        validators = Validators()
        
        @self.app.route('/api/test', methods=['POST'])
        @handle_api_errors
        @monitor_performance()  # 需要调用装饰器
        def test_api():
            from utils.validators import ValidationError
            data = request.get_json()
            
            # 验证
            keyword = data.get('keyword')
            try:
                validators.validate_string(keyword, min_length=1, max_length=100)
            except ValidationError as e:
                return APIResponse.validation_error({'keyword': str(e)})
            
            # 返回成功
            return APIResponse.success({'keyword': keyword})
        
        # 测试成功情况
        response = self.client.post('/api/test', 
                                   json={'keyword': 'test'},
                                   content_type='application/json')
        data = response.get_json()
        
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['data']['keyword'], 'test')
        
        # 测试验证失败
        response = self.client.post('/api/test', 
                                   json={'keyword': ''},
                                   content_type='application/json')
        data = response.get_json()
        
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['code'], 400)


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("运行优化工具测试套件")
    print("=" * 60)
    
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加所有测试
    suite.addTests(loader.loadTestsFromTestCase(TestAPIResponse))
    suite.addTests(loader.loadTestsFromTestCase(TestValidators))
    suite.addTests(loader.loadTestsFromTestCase(TestDecorators))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 输出摘要
    print("\n" + "=" * 60)
    print("测试摘要")
    print("=" * 60)
    print(f"运行测试: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    
    if result.wasSuccessful():
        print("\n✅ 所有测试通过！")
        return 0
    else:
        print("\n❌ 部分测试失败")
        return 1


if __name__ == '__main__':
    exit_code = run_all_tests()
    sys.exit(exit_code)
