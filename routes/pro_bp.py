from flask import Blueprint, request, jsonify
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from shared import pro_required, is_safe_url, debug, info, warn, error
from managers import offline_manager

# 假设你把爬虫逻辑移到了 spider_core.py，并实例化了 crawler_instance
# 如果没移，你需要从 dbserver import crawler (但这会导致循环引用)，所以强烈建议移出爬虫类
from spider_core import crawler_instance as crawler 

pro_bp = Blueprint('pro', __name__)

# routes/pro_bp.py

@pro_bp.route('/api/pro/download_book', methods=['POST'])
@pro_required
def api_pro_download_book():
    book_key = request.json.get('key')
    input_url = request.json.get('url') # 这是你当前看的某一章
    
    if not book_key or not input_url:
        return jsonify({"status": "error", "msg": "Missing params"})

    if not is_safe_url(input_url):
        return jsonify({"status": "error", "msg": "Illegal URL"}), 403

    def download_task(u_key, start_url):
        info("Pro", f"启动离线任务: {u_key}")
        
        toc = None
        real_toc_url = None

        # 1. 智能判断：如果 URL 以 .html 结尾，大概率是章节，不是目录
        # 或者先尝试解析，如果章节数太少，也认为不对
        is_chapter_url = ".html" in start_url
        
        if not is_chapter_url:
            # 看起来像目录，先试着抓一下
            toc = crawler.get_toc(start_url)
        
        # 2. 校验逻辑：如果没抓到，或者抓到的章节少于 20 章 (防止误判“最新章节列表”)
        if not toc or len(toc['chapters']) < 20:
            info("Pro", f"URL 似乎不是全本目录 (仅 {len(toc['chapters']) if toc else 0} 章)，尝试寻找真实目录...")
            
            # 访问当前页面，寻找“目录”按钮的链接
            page_data = crawler.run(start_url)
            if page_data and page_data.get('toc_url'):
                real_toc_url = page_data['toc_url']
                info("Pro", f"🎯 定位到真实目录: {real_toc_url}")
                # 再次尝试抓取目录
                toc = crawler.get_toc(real_toc_url)
            else:
                error("Pro", "❌ 无法定位目录页，任务终止。")
                return

        if not toc or not toc['chapters']: 
            error("Pro", "❌ 目录解析失败或为空")
            return
        
        info("Pro", f"✅ 目录获取成功，共 {len(toc['chapters'])} 章，开始并发下载...")

        # 3. 并发下载全书
        full_data = {}
        # 建议根据服务器配置调整 max_workers，10-15 是比较激进但高效的值
        with ThreadPoolExecutor(max_workers=12) as exe:
            future_to_url = {exe.submit(crawler.run, c['url']): c['url'] for c in toc['chapters']}
            
            # 进度计数
            total = len(toc['chapters'])
            done = 0
            
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    data = future.result()
                    if data: 
                        full_data[url] = data
                except: pass
                
                done += 1
                if done % 50 == 0:
                    info("Pro", f"下载进度: {done}/{total}")
        
        # 4. 保存
        offline_manager.save_book(u_key, full_data)
        info("System", f"[Pro] 🎉 离线下载完成: {u_key} (最终缓存 {len(full_data)} 章)")

    threading.Thread(target=download_task, args=(book_key, input_url)).start()
    return jsonify({"status": "success", "msg": "🚀 全本离线任务已启动，正在后台高速下载..."})

# ==========================================
# 下载管理功能（Pro 专属）
# ==========================================
@pro_bp.route('/api/pro/list_downloads', methods=['GET'])
@pro_required
def list_downloads():
    """列出 downloads 文件夹中的所有文件"""
    import os
    from shared import DL_DIR
    
    try:
        files = []
        if os.path.exists(DL_DIR):
            for filename in os.listdir(DL_DIR):
                filepath = os.path.join(DL_DIR, filename)
                if os.path.isfile(filepath):
                    file_stat = os.stat(filepath)
                    files.append({
                        'filename': filename,
                        'size': file_stat.st_size,
                        'modified': file_stat.st_mtime
                    })
        
        # 按修改时间倒序排列
        files.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify({"success": True, "files": files})
    
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

@pro_bp.route('/api/pro/download_file', methods=['GET'])
@pro_required
def download_file():
    from flask import send_from_directory
    from shared import DL_DIR
    
    filename = request.args.get('filename')
    if not filename:
        return "Missing filename", 400
    
    # 安全检查：防止路径遍历攻击
    import os
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(DL_DIR, safe_filename)
    
    if not os.path.exists(filepath):
        return "File not found", 404
    
    return send_from_directory(DL_DIR, safe_filename, as_attachment=True, conditional=False, max_age=0)

@pro_bp.route('/api/pro/delete_file', methods=['POST'])
@pro_required
def delete_file():
    """删除 downloads 文件夹中的指定文件"""
    import os
    from shared import DL_DIR
    
    data = request.get_json()
    filename = data.get('filename')
    
    if not filename:
        return jsonify({"success": False, "msg": "缺少文件名"})
    
    # 安全检查
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(DL_DIR, safe_filename)
    
    if not os.path.exists(filepath):
        return jsonify({"success": False, "msg": "文件不存在"})
    
    try:
        os.remove(filepath)
        return jsonify({"success": True, "msg": "删除成功"})
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})
