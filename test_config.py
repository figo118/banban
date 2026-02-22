#!/usr/bin/env python3
"""
配置加载测试脚本
用于验证 config.py 是否正确从环境变量读取配置
"""

import sys
sys.path.append('.')

from config import API_KEY, API_SECRET

print("配置加载测试结果:")
print(f"API_KEY 长度: {len(API_KEY)}")
print(f"API_SECRET 长度: {len(API_SECRET)}")
print(f"API_KEY 前10位: {API_KEY[:10]}..." if API_KEY else "API_KEY 为空")
print(f"API_SECRET 前10位: {API_SECRET[:10]}..." if API_SECRET else "API_SECRET 为空")

if API_KEY and API_SECRET:
    print("✅ 配置加载成功！")
else:
    print("⚠️  配置加载失败，请检查 .env 文件是否正确配置")
