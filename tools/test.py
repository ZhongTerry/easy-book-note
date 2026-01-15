import argparse
import logging
from tqdm import tqdm
import colorama
import json
from typing import Optional, List, Dict
import requests
import aiohttp
import asyncio
from ebooklib import epub

# 使用与HTML中相同的API基础路径
BASE_URL = 'https://qkfqapi.vv9v.cn'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger('fanqie_crawler')
console_handler = logging.StreamHandler()
logger.addHandler(console_handler)

class CacheManager:
    """缓存管理器，模拟HTML中的缓存逻辑"""
    
    def __init__(self):
        self.cache = {}
    
    def get(self, cache_type: str, book_id: str) -> Optional[dict]:
        """获取缓存数据"""
        key = f"{cache_type}_{book_id}"
        return self.cache.get(key)
    
    def set(self, cache_type: str, book_id: str, data: dict):
        """设置缓存数据"""
        key = f"{cache_type}_{book_id}"
        self.cache[key] = data

class FanqieNovelDownloader:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.cache_manager = CacheManager()
        self.session = requests.Session()
        # 设置与浏览器相同的请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Referer': 'https://qkfqapi.vv9v.cn/',  # 关键：添加Referer
            'Origin': base_url,  # 添加Origin头
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
        })

    def call_api(self, api: str, params: dict) -> Optional[dict]:
        """
        调用API，模拟HTML中的API调用逻辑
        """
        try:
            url = f"{self.base_url}/api/{api}"
            logger.debug(f"调用API: {url}, 参数: {params}")
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            # 检查API返回状态码
            if result.get('code') != 200:
                logger.error(f"API返回错误: {result.get('message', '未知错误')}")
                return None
                
            # 返回data字段，与HTML中的逻辑一致
            return result.get('data')
            
        except requests.exceptions.RequestException as e:
            logger.error(f'调用API失败 {url}: {e}')
            return None
        except Exception as e:
            logger.error(f'处理API响应失败: {e}')
            return None

    async def download_one_chapter(self, item_id: str, pbar: tqdm, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> dict:
        """
        下载单个章节，完全模拟HTML中的/content API调用
        """
        async with semaphore:
            try:
                # 模拟HTML中的请求参数
                params = {
                    'item_id': item_id,
                    'tab': '小说'  # 与HTML中保持一致
                }
                
                async with session.get(
                    f'{self.base_url}/api/content', 
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status != 200:
                        logger.warning(f"章节 {item_id} 请求失败: HTTP {response.status}")
                        pbar.update(1)
                        return {
                            'success': False,
                            'item_id': item_id,
                        }
                    
                    content_data = await response.json()
                    
                    # 检查API响应格式
                    if content_data.get('code') != 200:
                        logger.warning(f"章节 {item_id} API错误: {content_data.get('message')}")
                        pbar.update(1)
                        return {
                            'success': False,
                            'item_id': item_id,
                        }
                    
                    # 提取章节内容，与HTML中的逻辑一致
                    chapter_data = content_data.get('data', {})
                    content = chapter_data.get('content', '')
                    
                    pbar.update(1)
                    return {
                        'success': True,
                        'item_id': item_id,
                        'content': content,
                        'raw_data': chapter_data  # 保留原始数据
                    }
                    
            except asyncio.TimeoutError:
                logger.warning(f"章节 {item_id} 下载超时")
                pbar.update(1)
                return {
                    'success': False,
                    'item_id': item_id,
                }
            except Exception as e:
                logger.error(f"下载章节 {item_id} 失败: {e}")
                pbar.update(1)
                return {
                    'success': False,
                    'item_id': item_id,
                }

    def get_book_detail(self, book_id: str) -> Optional[dict]:
        """
        获取书籍详情，模拟HTML中的/detail API
        """
        # 先检查缓存
        cached_data = self.cache_manager.get('detail', book_id)
        if cached_data:
            logger.info("从缓存获取书籍详情")
            return cached_data
            
        # 调用API获取数据
        book_data = self.call_api('detail', {'book_id': book_id})
        if book_data:
            # 缓存数据
            self.cache_manager.set('detail', book_id, book_data)
        
        return book_data

    def get_chapter_list(self, book_id: str) -> Optional[List[Dict]]:
        """
        获取章节列表，模拟HTML中的/book API
        返回扁平化的章节列表
        """
        # 先检查缓存
        cached_data = self.cache_manager.get('chapters', book_id)
        if cached_data:
            logger.info("从缓存获取章节列表")
            return cached_data
            
        # 调用API获取章节数据
        chapter_data = self.call_api('book', {'book_id': book_id})
        if not chapter_data:
            return None
            
        try:
            # 模拟HTML中的数据结构处理
            # HTML中使用: chapterList = data.data.data.chapterListWithVolume.flat()
            data_container = chapter_data.get('data', {})
            chapter_list_with_volume = data_container.get('chapterListWithVolume', [])
            
            # 扁平化处理（二维数组转一维）
            flat_chapter_list = []
            for volume in chapter_list_with_volume:
                if isinstance(volume, list):
                    flat_chapter_list.extend(volume)
                else:
                    flat_chapter_list.append(volume)
            
            # 格式化章节信息
            formatted_chapters = []
            for idx, chapter in enumerate(flat_chapter_list):
                formatted_chapter = {
                    'index': idx + 1,
                    'item_id': chapter.get('itemId') or chapter.get('item_id', ''),
                    'title': chapter.get('title', f'第{idx+1}章'),
                    'chapter_id': chapter.get('chapterId') or chapter.get('chapter_id', ''),
                    'raw_data': chapter
                }
                formatted_chapters.append(formatted_chapter)
            
            # 缓存数据
            self.cache_manager.set('chapters', book_id, formatted_chapters)
            
            logger.info(f"成功获取 {len(formatted_chapters)} 个章节")
            return formatted_chapters
            
        except Exception as e:
            logger.error(f"处理章节列表失败: {e}")
            return None

    async def download_chapters(self, book_id: str, start_ch: int, end_ch: int, disable_tqdm: bool = False) -> Optional[List[Dict]]:
        """
        下载指定范围的章节内容
        """
        try:
            # 获取章节列表
            chapter_list = self.get_chapter_list(book_id)
            if not chapter_list:
                logger.error("无法获取章节列表")
                return None
            
            # 验证章节范围
            total_chapters = len(chapter_list)
            if start_ch < 1 or start_ch > total_chapters:
                logger.error(f"起始章节 {start_ch} 超出范围 (1-{total_chapters})")
                return None
                
            if end_ch == 0:  # 0表示下载到最后一章
                end_ch = total_chapters
            elif end_ch < start_ch or end_ch > total_chapters:
                logger.error(f"结束章节 {end_ch} 超出范围 ({start_ch}-{total_chapters})")
                return None
            
            # 切片获取指定章节范围
            target_chapters = chapter_list[start_ch - 1:end_ch]
            logger.info(f"准备下载第 {start_ch}-{end_ch} 章，共 {len(target_chapters)} 章")
            
            # 建立章节ID到索引的映射
            chapter_index_map = {}
            for idx, chapter in enumerate(target_chapters):
                chapter_index_map[chapter['item_id']] = idx
            
            # 连接池与并发限制（模拟HTML中的配置）
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=10)
            semaphore = asyncio.Semaphore(5)  # 适当的并发限制
            
            # 第一次下载尝试
            results = []
            with tqdm(total=len(target_chapters), desc='下载章节', disable=disable_tqdm) as pbar:
                async with aiohttp.ClientSession(
                    connector=connector, 
                    timeout=aiohttp.ClientTimeout(total=60),
                    headers=self.session.headers
                ) as session:
                    tasks = [
                        self.download_one_chapter(chapter['item_id'], pbar, session, semaphore)
                        for chapter in target_chapters
                    ]
                    results = await asyncio.gather(*tasks)
            
            # 处理下载结果
            failed_chapters = []
            for result in results:
                if result and result['success']:
                    # 将内容添加到对应的章节
                    chapter_idx = chapter_index_map[result['item_id']]
                    target_chapters[chapter_idx]['content'] = result['content']
                else:
                    failed_chapters.append(result['item_id'])
            
            # 重试失败的章节
            if failed_chapters:
                logger.info(f"开始重试 {len(failed_chapters)} 个失败的章节")
                with tqdm(total=len(failed_chapters), desc='重试失败章节', disable=disable_tqdm) as pbar:
                    async with aiohttp.ClientSession(
                        connector=connector, 
                        timeout=aiohttp.ClientTimeout(total=60),
                        headers=self.session.headers
                    ) as session:
                        retry_tasks = [
                            self.download_one_chapter(chapter_id, pbar, session, semaphore)
                            for chapter_id in failed_chapters
                        ]
                        retry_results = await asyncio.gather(*retry_tasks)
                        
                        # 处理重试结果
                        for result in retry_results:
                            if result and result['success']:
                                chapter_idx = chapter_index_map[result['item_id']]
                                target_chapters[chapter_idx]['content'] = result['content']
                            else:
                                logger.error(f"章节 {result['item_id']} 重试后仍然失败")
            
            # 统计成功下载的章节
            success_count = sum(1 for chapter in target_chapters if 'content' in chapter)
            logger.info(f"章节下载完成: 成功 {success_count}/{len(target_chapters)}")
            
            return target_chapters
            
        except Exception as e:
            logger.critical(f"下载章节过程失败: {e}")
            return None

    def download_book(self, book_id: str, start_ch: int, end_ch: int) -> Optional[dict]:
        """
        下载整本书或指定章节范围
        """
        # 获取书籍详情
        book_detail = self.get_book_detail(book_id)
        if not book_detail:
            logger.error("无法获取书籍详情")
            return None
        
        # 提取书籍信息（模拟HTML中的数据结构）
        book_info_data = book_detail.get('data', {})
        serial_count = int(book_info_data.get('serial_count', 0))
        book_name = book_info_data.get('book_name', '未知书名')
        author = book_info_data.get('author', '未知作者')
        
        # 验证章节范围
        if end_ch == 0:  # 0表示下载到最后一章
            end_ch = serial_count
        
        if (end_ch != 0 and end_ch < start_ch) or start_ch > serial_count or end_ch > serial_count:
            logger.error(f"章节范围错误: 开始{start_ch} 结束{end_ch} 总章节{serial_count}")
            return None
        
        logger.info(f"开始下载: {book_name} (共{serial_count}章) - 章节{start_ch}-{end_ch}")
        
        # 下载章节内容
        chapters = asyncio.run(self.download_chapters(book_id, start_ch, end_ch))
        if not chapters:
            return None
        
        # 构建返回数据
        return {
            'info': {
                'book_id': book_id,
                'book_name': book_name,
                'author': author,
                'abstract': book_info_data.get('abstract', ''),
                'cover_url': book_info_data.get('thumb_url', ''),
                'serial_count': serial_count
            },
            'chapters': chapters
        }

    def create_epub(self, metadata: dict, chapters: List[Dict]) -> Optional[epub.EpubBook]:
        """
        创建EPUB电子书
        """
        try:
            book = epub.EpubBook()
            book.set_identifier(f"fanqie_{metadata['book_id']}")
            book.set_language('zh-CN')
            
            book.set_title(metadata['book_name'])
            book.add_author(metadata['author'])
            book.add_metadata('DC', 'description', metadata.get('abstract', ''))
            
            # 添加封面
            if metadata.get('cover_url'):
                try:
                    cover_response = self.session.get(metadata['cover_url'], timeout=10)
                    if cover_response.status_code == 200:
                        cover_image = epub.EpubImage(
                            uid='cover',
                            file_name='cover.jpg',
                            media_type='image/jpeg',
                            content=cover_response.content
                        )
                        book.add_item(cover_image)
                        book.set_cover('cover.jpg', cover_response.content)
                except Exception as e:
                    logger.warning(f"添加封面失败: {e}")
            
            spine = ['nav']
            toc = []
            
            # 添加章节
            for idx, chapter in enumerate(chapters):
                if 'content' not in chapter:
                    logger.warning(f"跳过第{idx+1}章 '{chapter.get('title', '未知标题')}' - 无内容")
                    continue
                    
                chapter_file = f'chapter_{idx + 1}.xhtml'
                title = chapter.get('title', f'第{idx + 1}章')
                content = chapter['content']
                
                # 格式化内容（模拟HTML中的段落处理）
                paragraphs = content.split('\n') if content else []
                html_content = ''.join(f'<p>{paragraph.strip()}</p>' for paragraph in paragraphs if paragraph.strip())
                
                # 创建章节
                epub_chapter = epub.EpubHtml(
                    title=title,
                    file_name=chapter_file,
                    lang='zh-CN'
                )
                epub_chapter.content = f'''
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>{title}</title>
                        <style>
                            body {{ font-family: serif; line-height: 1.6; margin: 2em; }}
                            h1 {{ text-align: center; border-bottom: 1px solid #ccc; padding-bottom: 0.5em; }}
                            p {{ text-indent: 2em; margin-bottom: 1em; text-align: justify; }}
                        </style>
                    </head>
                    <body>
                        <h1>{title}</h1>
                        <div>{html_content}</div>
                    </body>
                    </html>
                '''
                
                book.add_item(epub_chapter)
                spine.append(epub_chapter)
                toc.append(epub_chapter)
            
            # 设置书籍结构
            book.toc = toc
            book.spine = spine
            
            # 添加导航文件
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            
            logger.info(f"EPUB创建成功: 包含 {len(toc)} 个章节")
            return book
            
        except Exception as e:
            logger.error(f"创建EPUB失败: {e}")
            return None

    def download_and_save(self, book_id: str, start_ch: int = 1, end_ch: int = 0, output_file: str = None) -> bool:
        """
        完整的下载和保存流程
        """
        try:
            # 下载书籍
            book_data = self.download_book(book_id, start_ch, end_ch)
            if not book_data:
                return False
            
            # 创建EPUB
            epub_book = self.create_epub(book_data['info'], book_data['chapters'])
            if not epub_book:
                return False
            
            # 确定输出文件名
            if not output_file:
                book_name = book_data['info']['book_name']
                output_file = f"{book_name}.epub"
            
            # 保存EPUB文件
            epub.write_epub(output_file, epub_book, {})
            logger.info(f"书籍已保存到: {output_file}")
            
            # 打印统计信息
            total_chapters = len(book_data['chapters'])
            success_chapters = sum(1 for ch in book_data['chapters'] if 'content' in ch)
            logger.info(f"下载统计: {success_chapters}/{total_chapters} 章成功")
            
            return True
            
        except Exception as e:
            logger.error(f"下载保存过程失败: {e}")
            return False

def main():
    parser = argparse.ArgumentParser(description='番茄小说下载器 - 模拟阅读器API')
    parser.add_argument('book_id', help='书籍ID')
    parser.add_argument('-s', '--start', type=int, default=1, help='开始章节 (默认: 1)')
    parser.add_argument('-e', '--end', type=int, default=0, help='结束章节 (0表示到最后一章, 默认: 0)')
    parser.add_argument('-o', '--output', help='输出文件名')
    parser.add_argument('--base-url', default=BASE_URL, help='API基础URL')
    
    args = parser.parse_args()
    
    # 验证书籍ID
    if not args.book_id:
        logger.critical('必须提供书籍ID')
        return 1
    
    # 创建下载器实例
    downloader = FanqieNovelDownloader(args.base_url)
    
    # 执行下载
    success = downloader.download_and_save(
        book_id=args.book_id,
        start_ch=args.start,
        end_ch=args.end,
        output_file=args.output
    )
    
    if success:
        print(colorama.Fore.GREEN + '下载成功!' + colorama.Style.RESET_ALL)
        return 0
    else:
        print(colorama.Fore.RED + '下载失败!' + colorama.Style.RESET_ALL)
        return 1

if __name__ == '__main__':
    colorama.init()
    exit(main())