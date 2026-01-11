import os
from PIL import Image, ImageDraw, ImageFont

def generate_font_icon(size=512):
    # 1. 设计参数
    bg_color = (242, 242, 242)    # 极浅灰背景
    text_color = (26, 26, 26)     # 深炭灰字母
    
    # 创建画布
    img = Image.new('RGB', (size, size), bg_color)
    draw = ImageDraw.Draw(img)

    # 2. 寻找系统中的高质量无衬线 Bold 字体
    # 按优先级排列：Inter > Segoe UI (Win) > Helvetica/San Francisco (Mac) > Arial
    font_names = [
        "Inter-Bold.otf", "Inter-Bold.ttf", 
        "Segoe UI Bold.ttf", "seguibld.ttf", 
        "HelveticaNeue-Bold.otf", "Helvetica-Bold.ttf",
        "Arial Bold.ttf", "arialbd.ttf"
    ]
    
    font = None
    font_size = int(size * 0.65) # 字母占据高度的 65%
    
    for name in font_names:
        try:
            # 尝试从系统标准路径加载
            if os.name == 'nt': # Windows
                font_path = os.path.join("C:\\Windows\\Fonts", name)
            else: # Mac/Linux
                font_path = name 
            font = ImageFont.truetype(font_path, font_size)
            print(f"成功加载字体: {name}")
            break
        except:
            continue
    
    if not font:
        font = ImageFont.load_default()
        print("未找到专业字体，使用系统默认。建议安装 Inter 或使用 Windows 自带 Segoe UI。")

    # 3. 文本测量与对齐
    text = "R"
    # 获取文本边框
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top

    # 4. 光学中心修正
    # R 这个字母因为右侧有斜腿，数学上的中心会导致视觉上看起来偏左。
    # 我们将其向右微调 2% 的宽度，向上微调 2% 的高度。
    x = (size - text_width) / 2 - left + (size * 0.02)
    y = (size - text_height) / 2 - top - (size * 0.01)

    # 5. 绘制
    draw.text((x, y), text, fill=text_color, font=font)

    # 6. 保存导出
    # 导出 PNG 备份
    img.save('icon.png')
    
    # 导出 ICO (Windows 核心格式)
    # 包含从 16px 到 256px 的所有常用尺寸，确保在任何地方都不模糊
    img.save('icon.ico', format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("-------------------------------------------------")
    print("✅ 已生成 icon.ico (含多尺寸支持)")
    print("✅ 已生成 icon.png (高精度预览)")
    print("-------------------------------------------------")

if __name__ == "__main__":
    # 需要安装 Pillow: pip install Pillow
    generate_font_icon()