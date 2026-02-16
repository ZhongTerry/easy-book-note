#!/usr/bin/env python3
"""
番茄小说微服务启动脚本（带Token鉴权，重构优化版）
建议与 main.py 放在同一目录下
"""
import os
import sys
import subprocess
from pathlib import Path

def load_env(env_file="config.env"):
    """加载环境变量"""
    env_path = Path(__file__).parent.parent.parent / env_file
    if not env_path.exists():
        print(f"⚠️ 未找到 {env_file}，将使用默认配置")
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                value = value.strip().strip('"').strip("'")
                os.environ[key] = value

def check_token():
    token = os.environ.get("FANQIE_API_TOKEN", "")
    if not token or token == "your-secret-token-here-change-me":
        print("=" * 60)
        print("⚠️ 警告：未配置安全的 FANQIE_API_TOKEN")
        print("=" * 60)
        print("当前使用默认Token，存在安全风险！")
        print("\n请在 config.env 中修改:")
        print("FANQIE_API_TOKEN=\"your-secret-token-here-change-me\"")
        print("\n建议使用强随机字符串，例如：")
        print(f"FANQIE_API_TOKEN=\"{os.urandom(16).hex()}\"")
        print("=" * 60)
        response = input("\n是否继续启动？(y/n): ")
        if response.lower() != 'y':
            print("已取消启动")
            sys.exit(0)
    return token

def main():
    load_env()
    token = check_token()
    print("\n🚀 启动番茄小说微服务...")
    print(f"🔑 Token: {token[:10]}... (已加密显示)")
    print(f"🌐 监听: http://0.0.0.0:9000")
    print("-" * 60)
    # 切换到 main.py 所在目录
    service_dir = Path(__file__).parent
    os.chdir(service_dir)
    # 启动 main.py
    try:
        subprocess.run([sys.executable, "main.py"])
    except KeyboardInterrupt:
        print("\n\n✅ 微服务已停止")

if __name__ == "__main__":
    main()
