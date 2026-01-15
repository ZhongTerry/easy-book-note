# adapters/fanqie_utils/internals.py
import os
import json
from .device_register import register

# 缓存文件路径
SESSION_FILE = 'fanqie_session.json'

def get_header():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, 'r') as f:
                data = json.load(f)
                return data['header']
        except: pass
    
    # 如果没有缓存，注册新设备
    try:
        header_str = register()
        with open(SESSION_FILE, 'w') as f:
            json.dump({'header': header_str}, f)
        return header_str
    except Exception as e:
        print(f"设备注册失败: {e}")
        return None

header = get_header()