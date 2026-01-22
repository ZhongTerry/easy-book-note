#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试章节 ID 识别功能"""

import sys
import re

def parse_chapter_id(text):
    """从 spider_core.py 复制的函数"""
    if not text: 
        return -1
    text = text.strip()
    
    # 1. 优先匹配纯数字 (例如: "49. 章节名" 或 "第49章")
    match_num = re.search(r'(?:第)?\s*(\d+)\s*[章节回幕\.]', text)
    if match_num: 
        return int(match_num.group(1))
        
    # 2. 匹配中文数字 (例如: "第十一章")
    match_cn = re.search(r'(?:第)?\s*([零一二两三四五六七八九十百千万]+)\s*[章节回幕]', text)
    if match_cn: 
        return _smart_convert_int(match_cn.group(1))
        
    # 3. 实在不行，匹配开头的数字 (例如 "123 章节名")
    match_start = re.search(r'^(\d+)', text)
    if match_start: 
        return int(match_start.group(1))
        
    return -1

def _smart_convert_int(s):
    """将中文数字转换为阿拉伯数字"""
    try: 
        return int(s)
    except: 
        pass

    cn_nums = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, 
               '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    cn_units = {'十': 10, '百': 100, '千': 1000, '万': 10000}

    if s.startswith('十'):
        s = '一' + s

    result = 0
    temp_val = 0
    
    for char in s:
        if char in cn_nums:
            temp_val = cn_nums[char]
        elif char in cn_units:
            unit = cn_units[char]
            if unit >= 10000:
                result = (result + temp_val) * unit
                temp_val = 0
            else:
                result += temp_val * unit
                temp_val = 0
    
    return result + temp_val

# 测试用例
test_cases = [
    "第6章 无名口诀",
    "第1章 开始",
    "第49章",
    "123 章节名",
    "第十一章",
    "第一百零五章",
    "49. 章节名",
    "Chapter 6",  # 应该识别失败
]

print("=" * 50)
print("章节 ID 识别测试")
print("=" * 50)

for title in test_cases:
    result = parse_chapter_id(title)
    status = "✅" if result > 0 else "❌"
    print(f"{status} \"{title}\" -> {result}")

print("=" * 50)
