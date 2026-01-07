import os
import sqlite3
import json
import re

# === 配置 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
TARGET_DB = os.path.join(USER_DATA_DIR, "data.sqlite")

def init_db():
    """初始化新的统一数据库"""
    print(f"🚀 初始化目标数据库: {TARGET_DB}")
    conn = sqlite3.connect(TARGET_DB)
    c = conn.cursor()
    
    # 1. 核心 KV 表 (合并所有用户的 .sqlite)
    c.execute('''CREATE TABLE IF NOT EXISTS user_books (
                    username TEXT NOT NULL,
                    book_key TEXT NOT NULL,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (username, book_key)
                )''')
    
    # 2. 通用 JSON 数据表 (合并 stats, tags, booklists, updates)
    # data_type: 'stats', 'tags', 'booklists', 'updates'
    c.execute('''CREATE TABLE IF NOT EXISTS user_modules (
                    username TEXT NOT NULL,
                    module_type TEXT NOT NULL,
                    json_content TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (username, module_type)
                )''')

    # 3. 系统配置表 (合并 roles.json)
    c.execute('''CREATE TABLE IF NOT EXISTS sys_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )''')
    
    conn.commit()
    return conn

def migrate():
    if not os.path.exists(USER_DATA_DIR):
        print("❌ 未找到 user_data 目录！")
        return

    conn = init_db()
    cursor = conn.cursor()
    
    print("\n📦 开始扫描并迁移文件...")
    files = os.listdir(USER_DATA_DIR)
    
    for f in files:
        f_path = os.path.join(USER_DATA_DIR, f)
        if f == "data.sqlite" or f.endswith(".bak"): continue # 跳过目标库和备份
        
        # === 1. 迁移 roles.json ===
        if f == "roles.json":
            try:
                with open(f_path, 'r', encoding='utf-8') as file:
                    data = json.dumps(json.load(file), ensure_ascii=False)
                    cursor.execute("REPLACE INTO sys_config (key, value) VALUES (?, ?)", ('roles', data))
                print(f"✅ [System] roles.json 已迁移")
            except Exception as e:
                print(f"❌ [Error] roles.json 读取失败: {e}")
            continue

        # === 2. 迁移用户 KV 数据库 (xxx.sqlite) ===
        if f.endswith(".sqlite"):
            username = f.replace(".sqlite", "")
            try:
                # 连接旧数据库
                old_conn = sqlite3.connect(f_path)
                old_cursor = old_conn.cursor()
                # 检查是否有 kv_store 表
                try:
                    old_cursor.execute("SELECT key, value FROM kv_store")
                    rows = old_cursor.fetchall()
                    count = 0
                    for key, val in rows:
                        cursor.execute("REPLACE INTO user_books (username, book_key, value) VALUES (?, ?, ?)", 
                                     (username, key, val))
                        count += 1
                    print(f"✅ [KV] 用户 {username}: 迁移了 {count} 条记录")
                except sqlite3.OperationalError:
                    print(f"⚠️ [Skip] {f} 不是标准的 KV 数据库，跳过。")
                finally:
                    old_conn.close()
            except Exception as e:
                print(f"❌ [Error] 迁移 {f} 失败: {e}")
            continue

        # === 3. 迁移用户 JSON 数据 (xxx_stats.json 等) ===
        # ... (上面是迁移 KV 的代码) ...

        # === 3. 迁移用户 JSON 数据 (增强鲁棒性版) ===
        match = re.match(r'(.+)_(stats|tags|booklists|updates)\.json$', f)
        if match:
            username = match.group(1)
            module_type = match.group(2)
            
            # 定义不同模块的默认值
            default_data = {}
            if module_type == 'stats':
                default_data = {"daily_stats": {}}
            
            try:
                with open(f_path, 'r', encoding='utf-8') as file:
                    content = file.read().strip()
                    
                    if not content:
                        # 文件为空，使用默认值
                        json_data = default_data
                        print(f"⚠️ [Warn] {f} 为空，已重置为默认值。")
                    else:
                        try:
                            json_data = json.loads(content)
                        except json.JSONDecodeError:
                            # JSON 格式错误，使用默认值
                            json_data = default_data
                            print(f"⚠️ [Warn] {f} 格式损坏，已重置为默认值。")

                # 存入数据库
                json_str = json.dumps(json_data, ensure_ascii=False)
                cursor.execute("REPLACE INTO user_modules (username, module_type, json_content) VALUES (?, ?, ?)", 
                             (username, module_type, json_str))
                
                print(f"✅ [JSON] 用户 {username}: 迁移模块 {module_type}")
                
            except Exception as e:
                print(f"❌ [Error] 迁移 {f} 发生未知错误: {e}")
            continue

    conn.commit()
    # 开启 WAL 模式提高并发性能
    cursor.execute("PRAGMA journal_mode=WAL;")
    conn.close()
    
    print("\n🎉 迁移完成！生成文件: user_data/data.sqlite")
    print("💡 请确认数据无误后，将 managers.py 替换为 SQL 版本。")

if __name__ == "__main__":
    migrate()