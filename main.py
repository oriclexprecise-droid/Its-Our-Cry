"""MyGO TTS 配音工作台 —— 启动入口。"""

import os
import sys
import webbrowser
import threading
from pathlib import Path

# 确保项目目录在 sys.path 中
PROJECT_DIR = Path(__file__).parent.resolve()
os.chdir(str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR))

from backend.server import create_app


def main():
    from single_instance import acquire_single_instance_mutex
    if not acquire_single_instance_mutex():
        print("程序已经在运行，请勿重复启动。")
        return

    print("=" * 50)
    print("  MyGO TTS 配音工作台")
    print("=" * 50)

    app = create_app("config.yaml")

    host = "127.0.0.1"
    port = 5123
    url = f"http://{host}:{port}"

    # 自动打开浏览器
    def open_browser():
        webbrowser.open(url)

    threading.Timer(1.0, open_browser).start()

    print(f"\n  正在启动服务: {url}")
    print(f"  按 Ctrl+C 停止服务\n")

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
