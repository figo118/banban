with open('strategies/strategies_full.py', 'r', encoding='utf-8') as f:
    content = f.read()
    # 先读取文件开头部分，查看类定义
    with open('file_content.txt', 'w', encoding='utf-8') as out:
        # 查找类定义的结束位置
        class_end = content.find('def calculate_signals')
        if class_end == -1:
            class_end = 4000
        out.write(content[:class_end])
    print('File content written to file_content.txt')