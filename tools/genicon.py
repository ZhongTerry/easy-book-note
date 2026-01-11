import os
from PIL import Image

def generate_all_icons(source_file='icon.png', output_dir='static/icons'):
    if not os.path.exists(source_file):
        print(f"找不到源文件: {source_file}")
        return

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 加载源文件
    img = Image.open(source_file)
    
    # --- 1. PWA & Android 常用尺寸 ---
    pwa_sizes = [72, 96, 128, 144, 152, 192, 384, 512]
    for size in pwa_sizes:
        img.resize((size, size), Image.Resampling.LANCZOS).save(
            f"{output_dir}/icon-{size}.png"
        )
    print("✅ PWA 图标生成完毕")

    # --- 2. iOS Apple Touch Icon ---
    img.resize((180, 180), Image.Resampling.LANCZOS).save(f"{output_dir}/apple-touch-icon.png")
    print("✅ iOS 图标生成完毕")

    # --- 3. Web Favicon (多尺寸合一) ---
    favicon_sizes = [(16, 16), (32, 32), (48, 48)]
    img.save(f"{output_dir}/favicon.ico", sizes=favicon_sizes)
    print("✅ Web Favicon 生成完毕")

    # --- 4. Windows 系统图标 (.ico) ---
    # Windows 软件主图标建议包含从 16 到 256 的所有级别
    win_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(f"{output_dir}/app-icon.ico", format='ICO', sizes=win_sizes)
    print("✅ Windows 系统图标 (app-icon.ico) 生成完毕")

if __name__ == "__main__":
    # 执行前确保你已经安装了 Pillow: pip install Pillow
    generate_all_icons()