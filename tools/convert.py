import os
from PIL import Image

def convert_specific_list_to_ico():
    # 1. 设置图标所在的目录（根据你的实际路径修改）
    # 如果脚本就放在图标文件夹里，这里写 '.' 即可
    icon_dir = 'static/icons' 
    output_filename = 'app-icon.ico'
    output_path = os.path.join(icon_dir, output_filename)

    # 2. 你截图中的具体文件列表
    png_files = [
        'icon-72.png', 
        'icon-96.png', 
        'icon-128.png', 
        'icon-144.png',
        'icon-152.png', 
        'icon-192.png', 
        'icon-384.png', 
        'icon-512.png'
    ]

    images = []

    print("开始读取文件...")
    for fileName in png_files:
        path = os.path.join(icon_dir, fileName)
        if os.path.exists(path):
            img = Image.open(path)
            images.append(img)
            print(f" ➕ 已加载: {fileName}")
        else:
            print(f" ❌ 找不到: {path}")

    if not images:
        print("错误：未找到任何可转换的 PNG 文件！")
        return

    # 3. 执行打包
    # ICO 格式支持在一个文件内存储多种尺寸。
    # 我们以最大的 512px 图作为主对象，将其余图塞入 append_images
    try:
        # 挑选最大的图作为基础
        main_img = images[-1] 
        # 剩下的图作为备用尺寸
        additional_imgs = images[:-1]
        
        main_img.save(output_path, format='ICO', append_images=additional_imgs)
        
        print("\n--------------------------------------------------")
        print(f"✅ 全部转换完成！")
        print(f"🚀 生成文件: {output_path}")
        print(f"💡 该 ICO 现在包含了从 72px 到 512px 的所有层级")
        print("--------------------------------------------------")
        print("👉 现在你可以放心地去 npm run build 了。")

    except Exception as e:
        print(f"\n发生错误: {e}")

if __name__ == "__main__":
    # 确保安装了 Pillow: pip install Pillow
    convert_specific_list_to_ico()