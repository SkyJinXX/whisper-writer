import subprocess
import os

# 定位到项目目录
project_dir = r"C:\Users\SkyJi\Repositories\whisper-writer"
os.chdir(project_dir)

# 激活虚拟环境并运行脚本
subprocess.run(r"venv\Scripts\activate && python run.py", shell=True)
