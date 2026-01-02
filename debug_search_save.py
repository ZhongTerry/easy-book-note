import time
from curl_cffi import requests as cffi_requests

# === 配置 ===
KEYWORD = "手术直播间"
# 这里完全复刻 SearchHelper 的逻辑
QUERY = f"{KEYWORD} 笔趣阁 目录"
URL = f"https://www.bing.com/search?q={QUERY}"

def save_html():
    print(f"[Debug] 正在请求: {URL}")
    print(f"[Debug] 伪装身份: Chrome 110")

    try:
        # 发送请求
        response = cffi_requests.get(
            URL, 
            impersonate="chrome110", 
            timeout=10
        )
        
        print(f"[Debug] 请求状态码: {response.status_code}")
        
        # 保存到文件
        filename = "bing_debug.html"
        with open(filename, "wb") as f:
            f.write(response.content)
            
        print("-" * 30)
        print(f"[成功] 网页源代码已保存到当前目录下的: {filename}")
        print(f"文件大小: {len(response.content)} 字节")
        print("-" * 30)
        
        # 简单做个预判
        html_str = response.content.decode('utf-8', errors='replace')
        if "b_algo" in html_str:
            print("提示: 在源码中发现了 'b_algo' (标准搜索结果类名)，说明页面结构应该正常。")
        elif "h2" in html_str:
            print("提示: 发现了 'h2' 标签，但没发现 'b_algo'，可能结构变了。")
        else:
            print("警告: 源码中既没找到 'b_algo' 也没找到 'h2'，可能被拦截了！")
            
        print(f"\n请双击打开 {filename} 查看内容，或者用记事本打开，把里面的关键代码发给我。")

    except Exception as e:
        print(f"[失败] 发生错误: {e}")

if __name__ == "__main__":
    save_html()