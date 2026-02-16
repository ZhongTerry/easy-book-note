"""
代码迁移辅助工具
提供从旧API到新API的自动迁移建议
"""
import re
import ast
from typing import List, Dict, Tuple
from pathlib import Path

class APIMigrationAnalyzer:
    """API迁移分析器"""
    
    def __init__(self):
        # 旧API到新API的映射
        self.api_mapping = {
            '/api/search': {
                'new': '/api/v2/search',
                'method': 'POST',
                'changes': [
                    '使用 OptimizedAPIClient.searchBooks()',
                    '返回格式已统一为 {success, data, message}',
                    '错误处理已自动化'
                ]
            },
            '/api/library': {
                'new': '/api/v2/library',
                'method': 'GET',
                'changes': [
                    '使用 OptimizedAPIClient.getLibrary()',
                    '支持分页参数: page, per_page',
                    '使用BookService避免N+1查询'
                ]
            },
            '/api/crawler': {
                'new': '/api/v2/crawler/fetch',
                'method': 'POST',
                'changes': [
                    '使用 OptimizedAPIClient.fetchContent()',
                    '新增SSRF保护',
                    '新增限速保护'
                ]
            }
        }
        
        # 需要替换的模式
        self.patterns = {
            'fetch_call': r'fetch\(["\']([^"\']+)["\']',
            'jsonify': r'jsonify\((.+?)\)',
            'error_handling': r'try:\s*\n(.+?)\nexcept',
        }
    
    def analyze_python_file(self, file_path: str) -> Dict:
        """
        分析Python文件中可优化的部分
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        suggestions = []
        
        # 检查是否可以使用装饰器
        if '@app.route' in content or '@bp.route' in content:
            # 检查是否缺少错误处理
            if 'try:' not in content or 'except' not in content:
                suggestions.append({
                    'type': 'error_handling',
                    'line': None,
                    'suggestion': '添加 @handle_api_errors 装饰器以自动处理错误'
                })
            
            # 检查是否可以使用APIResponse
            if 'jsonify(' in content and 'APIResponse' not in content:
                suggestions.append({
                    'type': 'response_format',
                    'line': None,
                    'suggestion': '使用 APIResponse.success() 统一响应格式'
                })
        
        # 检查是否有验证逻辑可以简化
        if 'if not' in content and 'return' in content:
            if 'Validators' not in content:
                suggestions.append({
                    'type': 'validation',
                    'line': None,
                    'suggestion': '使用 Validators 类简化输入验证'
                })
        
        # 检查是否有重复的查询逻辑
        if 'cursor.execute' in content:
            execute_count = content.count('cursor.execute')
            if execute_count > 3:
                suggestions.append({
                    'type': 'service_layer',
                    'line': None,
                    'suggestion': f'检测到 {execute_count} 次数据库查询，考虑使用 BookService 或创建新的服务类'
                })
        
        return {
            'file': file_path,
            'suggestions': suggestions
        }
    
    def analyze_javascript_file(self, file_path: str) -> Dict:
        """
        分析JavaScript文件中可优化的部分
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        suggestions = []
        
        # 检查fetch调用
        fetch_calls = re.findall(r'fetch\(["\']([^"\']+)["\']', content)
        for api_url in fetch_calls:
            # 检查是否可以使用新的API客户端
            suggestions.append({
                'type': 'api_client',
                'api': api_url,
                'suggestion': f'使用 apiClient 替代直接的 fetch 调用，减少样板代码'
            })
        
        # 检查错误处理
        if 'fetch(' in content:
            if '.catch(' not in content:
                suggestions.append({
                    'type': 'error_handling',
                    'line': None,
                    'suggestion': '添加 .catch() 处理网络错误，或使用 handleAPIError()'
                })
        
        # 检查是否有重复的加载状态管理
        if 'loading = true' in content or 'isLoading = true' in content:
            if 'LoadingManager' not in content:
                suggestions.append({
                    'type': 'loading_state',
                    'line': None,
                    'suggestion': '使用 LoadingManager 统一管理加载状态'
                })
        
        return {
            'file': file_path,
            'suggestions': suggestions
        }
    
    def generate_migration_code(self, old_code: str, code_type: str = 'python') -> str:
        """
        生成迁移后的代码示例
        """
        if code_type == 'python':
            return self._generate_python_migration(old_code)
        elif code_type == 'javascript':
            return self._generate_js_migration(old_code)
        else:
            return old_code
    
    def _generate_python_migration(self, old_code: str) -> str:
        """生成Python迁移代码"""
        new_code = old_code
        
        # 添加装饰器
        if '@app.route' in new_code or '@bp.route' in new_code:
            if '@handle_api_errors' not in new_code:
                # 在路由装饰器下方添加
                new_code = re.sub(
                    r'(@\w+\.route\([^\)]+\))',
                    r'\1\n@handle_api_errors\n@monitor_performance',
                    new_code
                )
        
        # 替换jsonify为APIResponse
        new_code = re.sub(
            r'return jsonify\(\{"status": "success".*?\}\)',
            'return APIResponse.success({...})',
            new_code
        )
        
        new_code = re.sub(
            r'return jsonify\(\{"status": "error".*?\}\)',
            'return APIResponse.error("...")',
            new_code
        )
        
        return new_code
    
    def _generate_js_migration(self, old_code: str) -> str:
        """生成JavaScript迁移代码"""
        new_code = old_code
        
        # 替换fetch调用为apiClient
        new_code = re.sub(
            r'fetch\(["\']([^"\']+)["\'],\s*\{[^}]+\}\)',
            r'apiClient.post("\1", data)',
            new_code
        )
        
        return new_code
    
    def scan_project(self, project_dir: str) -> List[Dict]:
        """
        扫描整个项目，生成迁移报告
        """
        project_path = Path(project_dir)
        results = []
        
        # 扫描Python文件
        for py_file in project_path.rglob('*.py'):
            if 'venv' in str(py_file) or '__pycache__' in str(py_file):
                continue
            try:
                result = self.analyze_python_file(str(py_file))
                if result['suggestions']:
                    results.append(result)
            except Exception as e:
                pass
        
        # 扫描JavaScript文件
        for js_file in project_path.rglob('*.js'):
            if 'node_modules' in str(js_file):
                continue
            try:
                result = self.analyze_javascript_file(str(js_file))
                if result['suggestions']:
                    results.append(result)
            except Exception as e:
                pass
        
        return results


class MigrationGuide:
    """迁移指南生成器"""
    
    @staticmethod
    def generate_checklist(files_to_migrate: List[str]) -> str:
        """
        生成迁移清单
        """
        checklist = "# 迁移清单\n\n"
        
        for i, file in enumerate(files_to_migrate, 1):
            checklist += f"- [ ] {i}. {file}\n"
        
        checklist += "\n## 迁移步骤\n\n"
        checklist += "### 后端文件\n"
        checklist += "1. 导入新工具: `from utils.decorators import handle_api_errors, monitor_performance`\n"
        checklist += "2. 添加装饰器到路由函数\n"
        checklist += "3. 替换 jsonify 为 APIResponse\n"
        checklist += "4. 使用 Validators 验证输入\n"
        checklist += "5. 测试API功能\n\n"
        
        checklist += "### 前端文件\n"
        checklist += "1. 引入 api-client.js\n"
        checklist += "2. 初始化 apiClient 或 uiClient\n"
        checklist += "3. 替换 fetch 调用为 apiClient 方法\n"
        checklist += "4. 移除手动错误处理代码\n"
        checklist += "5. 测试API调用\n"
        
        return checklist
    
    @staticmethod
    def generate_migration_example(api_name: str) -> Dict[str, str]:
        """
        生成具体API的迁移示例
        """
        examples = {
            'search': {
                'old_backend': '''
@core_bp.route('/api/search', methods=['POST'])
def search():
    try:
        data = request.get_json()
        keyword = data.get('keyword')
        
        if not keyword:
            return jsonify({"status": "error", "msg": "关键词不能为空"}), 400
        
        results = searcher.search_universal(keyword)
        return jsonify({"status": "success", "results": results})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500
''',
                'new_backend': '''
@optimized_bp.route('/search', methods=['POST'])
@handle_api_errors  # 自动错误处理
@monitor_performance  # 性能监控
@rate_limit(30, 60)  # 限速
@require_fields(['keyword'])  # 必填字段检查
def search_books():
    data = request.get_json()
    keyword = data['keyword']
    
    # 输入验证
    if not validators.validate_string(keyword, min_length=1, max_length=100):
        return APIResponse.validation_error("搜索关键词长度需在1-100之间")
    
    results = searcher.search_universal(keyword)
    return APIResponse.success({'results': results})
''',
                'old_frontend': '''
async function searchBooks(keyword) {
    const loadingEl = document.getElementById('loading');
    loadingEl.style.display = 'block';
    
    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({keyword})
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            renderResults(data.results);
        } else {
            alert('搜索失败: ' + data.msg);
        }
    } catch (error) {
        alert('网络错误: ' + error.message);
    } finally {
        loadingEl.style.display = 'none';
    }
}
''',
                'new_frontend': '''
async function searchBooks(keyword) {
    // 使用uiClient自动处理加载状态和错误提示
    const result = await uiClient.searchBooks(keyword);
    
    if (result.success) {
        renderResults(result.data.results);
    }
    // 错误处理已自动完成（显示toast）
}
'''
            }
        }
        
        return examples.get(api_name, {})


# ========================================
# CLI工具
# ========================================

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python migration_helper.py scan <project_dir>    - 扫描项目生成迁移建议")
        print("  python migration_helper.py analyze <file>        - 分析单个文件")
        print("  python migration_helper.py example <api_name>    - 查看迁移示例")
        sys.exit(1)
    
    command = sys.argv[1]
    analyzer = APIMigrationAnalyzer()
    guide = MigrationGuide()
    
    if command == 'scan':
        if len(sys.argv) < 3:
            print("请指定项目目录")
            sys.exit(1)
        
        project_dir = sys.argv[2]
        results = analyzer.scan_project(project_dir)
        
        print(f"\n发现 {len(results)} 个文件需要优化:\n")
        
        for result in results:
            print(f"\n📁 {result['file']}")
            for suggestion in result['suggestions']:
                print(f"  💡 [{suggestion['type']}] {suggestion['suggestion']}")
        
        # 生成迁移清单
        files = [r['file'] for r in results]
        checklist = guide.generate_checklist(files)
        
        checklist_file = 'MIGRATION_CHECKLIST.md'
        with open(checklist_file, 'w', encoding='utf-8') as f:
            f.write(checklist)
        
        print(f"\n✅ 迁移清单已保存到: {checklist_file}")
    
    elif command == 'analyze':
        if len(sys.argv) < 3:
            print("请指定文件路径")
            sys.exit(1)
        
        file_path = sys.argv[2]
        
        if file_path.endswith('.py'):
            result = analyzer.analyze_python_file(file_path)
        elif file_path.endswith('.js'):
            result = analyzer.analyze_javascript_file(file_path)
        else:
            print("不支持的文件类型")
            sys.exit(1)
        
        print(f"\n分析结果: {file_path}\n")
        for suggestion in result['suggestions']:
            print(f"💡 [{suggestion['type']}] {suggestion['suggestion']}")
    
    elif command == 'example':
        if len(sys.argv) < 3:
            print("请指定API名称 (例如: search)")
            sys.exit(1)
        
        api_name = sys.argv[2]
        examples = guide.generate_migration_example(api_name)
        
        if not examples:
            print(f"未找到 {api_name} 的迁移示例")
            sys.exit(1)
        
        print(f"\n=== {api_name} API 迁移示例 ===\n")
        
        print("📌 旧版后端代码:")
        print(examples['old_backend'])
        
        print("\n✨ 新版后端代码:")
        print(examples['new_backend'])
        
        print("\n📌 旧版前端代码:")
        print(examples['old_frontend'])
        
        print("\n✨ 新版前端代码:")
        print(examples['new_frontend'])
    
    else:
        print(f"未知命令: {command}")
        sys.exit(1)
