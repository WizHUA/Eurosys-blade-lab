import sys
import os
import argparse

# 将 src 添加到 path，以便可以导入 src 下的模块
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, 'src')
sys.path.append(src_dir)

try:
    from experiment import ExperimentManager
except ImportError:
    # 如果直接运行，可能需要调整 path
    sys.path.append(os.path.join(current_dir, '..', 'src'))
    from experiment import ExperimentManager

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run HPC Experiment Data Collection')
    parser.add_argument('--experiment', type=str, required=True, help='Name of the experiment to run (defined in config/experiments.yaml)')
    args = parser.parse_args()

    # 确保在 dataset_builder 目录下运行，以便相对路径 config/ 能够正确解析
    os.chdir(current_dir)

    # ==================== 安全检查 (Safety Checks) ====================
    print("\n" + "="*60)
    print(" 🛡️  Safety Checks")
    print("="*60)
    
    # 1. 检查会话保护 (Session Protection)
    if not os.environ.get("TMUX") and not os.environ.get("STY"):
        print("\n[WARNING] ⚠️  It seems you are NOT running in a tmux/screen session.")
        print("Fault injection (especially network loss) may cause SSH disconnection.")
        print("It is HIGHLY RECOMMENDED to use `tmux` or `screen` to prevent data loss!")
        print("Waiting 5 seconds... Press Ctrl+C to abort if needed.")
        import time
        time.sleep(5)
    else:
        print("✅ Running inside a protected session (tmux/screen).")

    print("="*60 + "\n")
    # ==================================================================

    print(f"Initializing Experiment Manager for experiment: {args.experiment}")
    manager = ExperimentManager()
    try:
        manager.run(args.experiment)
    except Exception as e:
        print(f"Error running experiment: {e}")
        sys.exit(1)
