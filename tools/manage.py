# manage.py
import sys
from managers import role_manager

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python manage.py set_role <用户名> <admin|pro|user>")
        print("示例: python manage.py set_role ztrztr admin")
    else:
        command = sys.argv[1]
        if command == "set_role":
            username = sys.argv[2]
            role = sys.argv[3]
            role_manager.set_role(username, role)
            print(f"✅ 成功! 用户 [{username}] 已被设置为 [{role}]")