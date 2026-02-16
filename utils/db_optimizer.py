"""
数据库优化工具
提供索引创建、查询分析、性能监控等功能
"""
import sqlite3
from typing import List, Dict, Any
import time
from functools import wraps
from shared import USER_DATA_DIR
import os

class DatabaseOptimizer:
    """数据库优化工具"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(USER_DATA_DIR, 'data.sqlite')
        self.db_path = db_path
    
    def create_indexes(self) -> Dict[str, bool]:
        """
        创建推荐的索引以提升查询性能
        返回每个索引的创建状态
        """
        indexes = {
            # 书库查询优化
            'idx_library_username': '''
                CREATE INDEX IF NOT EXISTS idx_library_username 
                ON library(username)
            ''',
            'idx_library_book_key': '''
                CREATE INDEX IF NOT EXISTS idx_library_book_key 
                ON library(book_key)
            ''',
            'idx_library_username_book_key': '''
                CREATE INDEX IF NOT EXISTS idx_library_username_book_key 
                ON library(username, book_key)
            ''',
            
            # 阅读进度查询优化
            'idx_library_last_read_time': '''
                CREATE INDEX IF NOT EXISTS idx_library_last_read_time 
                ON library(username, last_read_time DESC)
            ''',
            'idx_library_update_time': '''
                CREATE INDEX IF NOT EXISTS idx_library_update_time 
                ON library(username, update_time DESC)
            ''',
            
            # TOC缓存查询优化
            'idx_toc_book_key': '''
                CREATE INDEX IF NOT EXISTS idx_toc_book_key 
                ON toc_cache(book_key)
            ''',
            'idx_toc_expire_time': '''
                CREATE INDEX IF NOT EXISTS idx_toc_expire_time 
                ON toc_cache(expire_time)
            ''',
            
            # 用户管理优化
            'idx_users_username': '''
                CREATE INDEX IF NOT EXISTS idx_users_username 
                ON users(username)
            ''',
            
            # 全文搜索优化（如果有search_history表）
            'idx_search_history_username': '''
                CREATE INDEX IF NOT EXISTS idx_search_history_username 
                ON search_history(username, search_time DESC)
            ''',
        }
        
        results = {}
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            for name, sql in indexes.items():
                try:
                    cursor.execute(sql)
                    results[name] = True
                    print(f"✅ 索引创建成功: {name}")
                except sqlite3.Error as e:
                    results[name] = False
                    print(f"❌ 索引创建失败: {name} - {e}")
            
            conn.commit()
        finally:
            conn.close()
        
        return results
    
    def analyze_table(self, table_name: str) -> Dict[str, Any]:
        """
        分析表的统计信息
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 执行ANALYZE命令
            cursor.execute(f"ANALYZE {table_name}")
            
            # 获取表信息
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cursor.fetchone()[0]
            
            # 获取索引信息
            cursor.execute(f"PRAGMA index_list({table_name})")
            indexes = cursor.fetchall()
            
            # 获取表结构
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            
            return {
                'table': table_name,
                'row_count': row_count,
                'indexes': [{'name': idx[1], 'unique': idx[2]} for idx in indexes],
                'columns': [{'name': col[1], 'type': col[2]} for col in columns]
            }
        finally:
            conn.close()
    
    def explain_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """
        分析查询计划
        帮助识别性能问题
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            explain_query = f"EXPLAIN QUERY PLAN {query}"
            if params:
                cursor.execute(explain_query, params)
            else:
                cursor.execute(explain_query)
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'selectid': row[0],
                    'order': row[1],
                    'from': row[2],
                    'detail': row[3]
                })
            
            return results
        finally:
            conn.close()
    
    def vacuum(self):
        """
        执行VACUUM以优化数据库文件大小和性能
        """
        conn = sqlite3.connect(self.db_path)
        try:
            print("🔧 开始执行 VACUUM...")
            start = time.time()
            conn.execute("VACUUM")
            elapsed = time.time() - start
            print(f"✅ VACUUM 完成，耗时: {elapsed:.2f}秒")
        finally:
            conn.close()
    
    def get_slow_queries_log(self) -> List[Dict[str, Any]]:
        """
        获取慢查询日志（需要先启用慢查询记录）
        """
        # 这个功能需要在应用层实现慢查询记录
        # 这里提供一个示例结构
        return []
    
    def optimize_full_text_search(self, table_name: str, column_name: str):
        """
        为指定列创建全文搜索索引（FTS5）
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            fts_table = f"{table_name}_fts"
            
            # 创建FTS5虚拟表
            create_fts = f'''
                CREATE VIRTUAL TABLE IF NOT EXISTS {fts_table}
                USING fts5({column_name}, content='{table_name}', content_rowid='id')
            '''
            cursor.execute(create_fts)
            
            # 触发器：同步插入
            create_insert_trigger = f'''
                CREATE TRIGGER IF NOT EXISTS {table_name}_fts_insert 
                AFTER INSERT ON {table_name} BEGIN
                    INSERT INTO {fts_table}(rowid, {column_name}) 
                    VALUES (new.id, new.{column_name});
                END
            '''
            cursor.execute(create_insert_trigger)
            
            # 触发器：同步更新
            create_update_trigger = f'''
                CREATE TRIGGER IF NOT EXISTS {table_name}_fts_update 
                AFTER UPDATE ON {table_name} BEGIN
                    UPDATE {fts_table} 
                    SET {column_name} = new.{column_name} 
                    WHERE rowid = new.id;
                END
            '''
            cursor.execute(create_update_trigger)
            
            # 触发器：同步删除
            create_delete_trigger = f'''
                CREATE TRIGGER IF NOT EXISTS {table_name}_fts_delete 
                AFTER DELETE ON {table_name} BEGIN
                    DELETE FROM {fts_table} WHERE rowid = old.id;
                END
            '''
            cursor.execute(create_delete_trigger)
            
            # 初始化FTS数据
            populate_fts = f'''
                INSERT INTO {fts_table}(rowid, {column_name})
                SELECT id, {column_name} FROM {table_name}
            '''
            cursor.execute(populate_fts)
            
            conn.commit()
            print(f"✅ 全文搜索索引创建成功: {fts_table}")
            
        except sqlite3.Error as e:
            print(f"❌ 全文搜索索引创建失败: {e}")
        finally:
            conn.close()


class QueryProfiler:
    """
    查询性能分析器
    用装饰器方式自动记录慢查询
    """
    
    slow_queries = []
    threshold_ms = 1000  # 慢查询阈值（毫秒）
    
    @classmethod
    def profile(cls, query_name: str = None):
        """
        装饰器：记录查询性能
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                start = time.time()
                result = func(*args, **kwargs)
                elapsed_ms = (time.time() - start) * 1000
                
                name = query_name or func.__name__
                
                # 记录慢查询
                if elapsed_ms > cls.threshold_ms:
                    cls.slow_queries.append({
                        'name': name,
                        'elapsed_ms': elapsed_ms,
                        'timestamp': time.time(),
                        'function': func.__name__
                    })
                    print(f"⚠️ 慢查询: {name} 耗时 {elapsed_ms:.2f}ms")
                
                return result
            return wrapper
        return decorator
    
    @classmethod
    def get_slow_queries(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的慢查询"""
        return sorted(
            cls.slow_queries,
            key=lambda x: x['elapsed_ms'],
            reverse=True
        )[:limit]
    
    @classmethod
    def clear_log(cls):
        """清除慢查询日志"""
        cls.slow_queries = []


# ========================================
# 使用示例
# ========================================

def example_optimize_database():
    """优化数据库示例"""
    optimizer = DatabaseOptimizer()
    
    # 1. 创建索引
    print("=== 创建索引 ===")
    results = optimizer.create_indexes()
    success_count = sum(1 for v in results.values() if v)
    print(f"成功创建 {success_count}/{len(results)} 个索引")
    
    # 2. 分析表
    print("\n=== 分析表结构 ===")
    library_info = optimizer.analyze_table('library')
    print(f"表: {library_info['table']}")
    print(f"行数: {library_info['row_count']}")
    print(f"索引数: {len(library_info['indexes'])}")
    
    # 3. 查询计划分析
    print("\n=== 查询计划分析 ===")
    query = "SELECT * FROM library WHERE username = ? ORDER BY last_read_time DESC"
    plan = optimizer.explain_query(query, ('test_user',))
    for step in plan:
        print(f"  {step['detail']}")
    
    # 4. VACUUM优化
    print("\n=== VACUUM优化 ===")
    optimizer.vacuum()


def example_query_profiler():
    """查询性能分析示例"""
    
    @QueryProfiler.profile("获取用户书库")
    def get_user_library(username: str):
        # 模拟数据库查询
        time.sleep(1.2)  # 模拟慢查询
        return []
    
    # 执行查询
    get_user_library("test_user")
    
    # 查看慢查询
    slow_queries = QueryProfiler.get_slow_queries()
    for query in slow_queries:
        print(f"慢查询: {query['name']} - {query['elapsed_ms']:.2f}ms")


# ========================================
# CLI工具
# ========================================

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python db_optimizer.py create_indexes  - 创建所有推荐索引")
        print("  python db_optimizer.py analyze <table>  - 分析表")
        print("  python db_optimizer.py vacuum          - 优化数据库")
        print("  python db_optimizer.py explain <query> - 分析查询计划")
        sys.exit(1)
    
    command = sys.argv[1]
    optimizer = DatabaseOptimizer()
    
    if command == 'create_indexes':
        results = optimizer.create_indexes()
        success = sum(1 for v in results.values() if v)
        print(f"\n总计: 成功 {success}/{len(results)} 个索引")
    
    elif command == 'analyze':
        if len(sys.argv) < 3:
            print("请指定表名")
            sys.exit(1)
        table = sys.argv[2]
        info = optimizer.analyze_table(table)
        print(f"\n表: {info['table']}")
        print(f"行数: {info['row_count']}")
        print(f"索引: {len(info['indexes'])} 个")
        for idx in info['indexes']:
            print(f"  - {idx['name']} {'(UNIQUE)' if idx['unique'] else ''}")
    
    elif command == 'vacuum':
        optimizer.vacuum()
    
    elif command == 'explain':
        if len(sys.argv) < 3:
            print("请提供SQL查询")
            sys.exit(1)
        query = sys.argv[2]
        plan = optimizer.explain_query(query)
        print("\n查询计划:")
        for step in plan:
            print(f"  {step['detail']}")
    
    else:
        print(f"未知命令: {command}")
        sys.exit(1)
