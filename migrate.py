import os
import sqlite3
import glob

# 配置原路径
USER_DATA_DIR = "./user_data"

def migrate():
    # 查找所有旧的 .db 文件 (txt格式)
    old_files = glob.glob(os.path.join(USER_DATA_DIR, "*.db"))
    
    for old_path in old_files:
        username = os.path.basename(old_path).replace(".db", "")
        new_sqlite_path = os.path.join(USER_DATA_DIR, f"{username}.sqlite")
        
        print(f"正在迁移用户: {username} ...")
        
        # 1. 解析旧数据
        kv_data = {}
        try:
            with open(old_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        kv_data[parts[0]] = parts[1]
        except Exception as e:
            print(f"读取旧文件 {old_path} 失败: {e}")
            continue

        # 2. 写入新 SQLite
        try:
            conn = sqlite3.connect(new_sqlite_path)
            conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
            
            with conn:
                for k, v in kv_data.items():
                    conn.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)", (k, v))
            
            conn.close()
            print(f"迁移成功: {new_sqlite_path}")
            
            # 3. (可选) 备份并删除旧文件
            os.rename(old_path, old_path + ".bak")
            
        except Exception as e:
            print(f"写入 SQLite 失败: {e}")

if __name__ == "__main__":
    migrate()