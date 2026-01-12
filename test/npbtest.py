import os
import sys
import time
import subprocess

os.chdir("/opt/exp")
os.environ["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
os.environ["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"

npbfile = "/opt/exp/npb/NPB3.4.2/NPB3.4-MPI/bin"

# 程序,Class,实际运行时间(秒),运行时间(分:秒),计算类型,适用场景,tag
tarfile = "/opt/exp/note/npb_result.csv"

npb_note_info = {
    "bt": ("块三维FFT", "高通信"),
    "cg": ("共轭梯度", "中通信"),
    "ep": ("CPU密集", "无通信"),
    "ft": ("FFT", "高通信"),
    "is": ("整数排序", "低通信"),
    "lu": ("LU分解", "高通信"),
    "mg": ("多重网格", "中通信"),
    "sp": ("稀疏矩阵", "低通信")
}

def main():
    result = []
    # 遍历npbfile目录下的所有可执行文件
    for f in os.listdir(npbfile):
        filepath = os.path.join(npbfile, f)
        if os.path.isfile(filepath) and os.access(filepath, os.X_OK):
            print("*"*20,"\nTest:", f)
            npbname = f.split(".")[0]
            level = f.split(".")[1]
            desc = npb_note_info.get(npbname, "default")
            start = time.time()
            start = time.time()

            ret = subprocess.run(
                ["mpirun", "-np", "4", filepath],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=500
            )
            end = time.time()
            duration = end - start
            time0 = f"{duration:.2f}"
            time1 = f"{int(duration // 60)}:{int(duration % 60):02d}"
            # 程序,Class,实际运行时间(秒),运行时间(分:秒),计算类型,适用场景,tag
            result.append([f, level, time0, time1, desc[0], desc[1], "test"])
            print("  Time:", time0, "seconds")
    # 写入结果
    with open(tarfile, "w") as f:
        f.write("程序,Class,实际运行时间(秒),运行时间(分:秒),计算类型,适用场景,tag\n")
        for r in result:
            f.write(",".join(r) + "\n")

    

if __name__ == "__main__":
    main()