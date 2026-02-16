"""
书籍服务层
处理书籍相关的业务逻辑
"""
from typing import Dict, List, Optional, Any
from utils import info, warn, error
import managers
import time


class BookService:
    """书籍业务逻辑服务"""
    
    def __init__(self):
        self.db = managers.db
        self.cache = managers.cache
        self.stats = managers.stats
        
    def get_user_library(self, username: str, filters: Optional[Dict] = None) -> Dict[str, Any]:
        """
        获取用户书架（带过滤和统计）
        
        Args:
            username: 用户名
            filters: 过滤条件（标签、状态等）
            
        Returns:
            dict: {
                'books': [],
                'stats': {},
                'tags': []
            }
        """
        try:
            # 获取书籍列表
            result = self.db.list_all(username)
            if result['status'] != 'success':
                return {'books': [], 'stats': {}, 'tags': []}
            
            books = result['data']
            
            # 应用过滤条件
            if filters:
                books = self._apply_filters(books, filters)
            
            # 增强书籍数据
            books = self._enrich_book_data(books, username)
            
            # 收集标签
            all_tags = set()
            for book in books:
                tags = book.get('tags', [])
                if isinstance(tags, list):
                    all_tags.update(tags)
            
            # 统计信息
            stats = {
                'total': len(books),
                'tags_count': len(all_tags),
                'has_updates': sum(1 for b in books if b.get('has_update', False))
            }
            
            return {
                'books': books,
                'stats': stats,
                'tags': sorted(all_tags)
            }
            
        except Exception as e:
            error("BookService", f"获取书架失败: {str(e)}")
            return {'books': [], 'stats': {}, 'tags': []}
    
    def _apply_filters(self, books: List[Dict], filters: Dict) -> List[Dict]:
        """应用过滤条件"""
        filtered = books
        
        # 按标签过滤
        if 'tag' in filters and filters['tag']:
            tag = filters['tag']
            filtered = [b for b in filtered if tag in b.get('tags', [])]
        
        # 按更新状态过滤
        if 'has_update' in filters:
            has_update = filters['has_update']
            filtered = [b for b in filtered if b.get('has_update') == has_update]
        
        # 按关键词搜索
        if 'keyword' in filters and filters['keyword']:
            keyword = filters['keyword'].lower()
            filtered = [
                b for b in filtered
                if keyword in b.get('key', '').lower() or
                   keyword in b.get('name', '').lower()
            ]
        
        return filtered
    
    def _enrich_book_data(self, books: List[Dict], username: str) -> List[Dict]:
        """
        增强书籍数据
        批量添加更新状态、阅读进度等信息
        """
        if not books:
            return books
        
        # 批量获取更新状态（避免 N+1 查询）
        update_info = {}
        try:
            all_updates = managers.update_manager.load(username)
            update_info = {k: v for k, v in all_updates.items()}
        except:
            pass
        
        # 增强每本书的数据
        for book in books:
            key = book.get('key')
            if not key:
                continue
            
            # 添加更新信息
            if key in update_info:
                book.update(update_info[key])
            
            # 计算未读章节数（如果有目录信息）
            if 'update_info' in book:
                ui = book['update_info']
                local_id = ui.get('last_local_id', 0)
                remote_id = ui.get('last_remote_id', 0)
                book['unread_count'] = max(0, remote_id - local_id)
        
        return books
    
    def save_book(self, username: str, book_key: str, book_data: Dict) -> Dict[str, Any]:
        """
        保存书籍信息
        
        Args:
            username: 用户名
            book_key: 书籍唯一标识
            book_data: 书籍数据
            
        Returns:
            dict: 操作结果
        """
        try:
            # 数据验证
            if not book_key:
                return {'status': 'error', 'msg': '书籍 key 不能为空'}
            
            if not isinstance(book_data, dict):
                return {'status': 'error', 'msg': '书籍数据格式错误'}
            
            # 确保必需字段
            book_data['key'] = book_key
            book_data['updated_at'] = int(time.time())
            
            # 保存到数据库
            self.db.save_raw_book(username, book_key, book_data)
            
            info("BookService", f"保存书籍成功: {username}:{book_key}")
            
            return {
                'status': 'success',
                'msg': '保存成功',
                'data': {'key': book_key}
            }
            
        except Exception as e:
            error("BookService", f"保存书籍失败: {str(e)}")
            return {'status': 'error', 'msg': '保存失败'}
    
    def delete_book(self, username: str, book_key: str) -> Dict[str, Any]:
        """
        删除书籍
        
        Args:
            username: 用户名
            book_key: 书籍唯一标识
            
        Returns:
            dict: 操作结果
        """
        try:
            # 删除书籍数据
            result = self.db.remove(book_key, username)
            
            if result['status'] == 'success':
                # 删除相关的更新订阅
                try:
                    managers.update_manager.remove_book(book_key, username)
                except:
                    pass
                
                info("BookService", f"删除书籍成功: {username}:{book_key}")
                return {'status': 'success', 'msg': '删除成功'}
            else:
                return result
                
        except Exception as e:
            error("BookService", f"删除书籍失败: {str(e)}")
            return {'status': 'error', 'msg': '删除失败'}
    
    def update_reading_progress(self, username: str, book_key: str, 
                                chapter_url: str, chapter_title: str,
                                chapter_id: int = -1) -> Dict[str, Any]:
        """
        更新阅读进度
        
        Args:
            username: 用户名
            book_key: 书籍唯一标识
            chapter_url: 章节 URL
            chapter_title: 章节标题
            chapter_id: 章节序号
            
        Returns:
            dict: 操作结果
        """
        try:
            # 获取现有书籍数据
            book_data = self.db.get_raw_book(username, book_key)
            if not book_data:
                book_data = {'key': book_key}
            
            # 更新进度信息
            book_data['last_url'] = chapter_url
            book_data['last_title'] = chapter_title
            book_data['last_read_at'] = int(time.time())
            
            if chapter_id > 0:
                book_data['last_chapter_id'] = chapter_id
            
            # 保存
            self.db.save_raw_book(username, book_key, book_data)
            
            # 记录历史
            try:
                managers.history_manager.add_record(
                    book_key, 
                    chapter_title, 
                    chapter_url, 
                    book_data.get('name', book_key)
                )
            except:
                pass
            
            info("BookService", f"更新进度: {username}:{book_key} -> {chapter_title}")
            
            return {'status': 'success', 'msg': '进度已保存'}
            
        except Exception as e:
            error("BookService", f"更新进度失败: {str(e)}")
            return {'status': 'error', 'msg': '保存失败'}
    
    def get_book_statistics(self, username: str, book_key: str) -> Dict[str, Any]:
        """
        获取书籍统计信息
        
        Args:
            username: 用户名
            book_key: 书籍唯一标识
            
        Returns:
            dict: 统计信息
        """
        try:
            stats = {
                'total_time': 0,  # 总阅读时间（秒）
                'total_chars': 0,  # 总阅读字数
                'total_chapters': 0,  # 总阅读章节数
                'last_read': None,  # 最后阅读时间
                'reading_speed': 0  # 阅读速度（字/分钟）
            }
            
            # 从统计管理器获取数据
            book_stats = self.stats.get_book_stats(username, book_key)
            if book_stats:
                stats.update(book_stats)
            
            # 计算阅读速度
            if stats['total_time'] > 0 and stats['total_chars'] > 0:
                minutes = stats['total_time'] / 60
                stats['reading_speed'] = int(stats['total_chars'] / minutes)
            
            return stats
            
        except Exception as e:
            error("BookService", f"获取统计失败: {str(e)}")
            return {}


# 全局实例
book_service = BookService()
