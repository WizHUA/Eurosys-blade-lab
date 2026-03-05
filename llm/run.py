import sys
import os

# 将 src 添加到 path，以便可以导入 src 下的模块
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, 'src')
sys.path.append(src_dir)

try:
    from run import main
except ImportError as e:
    print(f"Error importing src.run: {e}")
    sys.exit(1)

if __name__ == "__main__":
    # 切换到 llm 目录，确保相对路径正确
    os.chdir(current_dir)
    main()
