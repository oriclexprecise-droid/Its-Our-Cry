"""It's Our Cry!!!!! 自包含启动器。

开发模式:  python launcher.py
打包模式:  双击 ItsOurCry.exe（PyInstaller --noconsole）
"""

import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_our_server(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as r:
            if r.status == 200:
                body = r.read(300).decode("utf-8", "replace")
                return "characters" in body
    except Exception:
        pass
    return False


def main():
    root = app_root()
    os.chdir(str(root))
    sys.path.insert(0, str(root))

    from backend.server import create_app

    config_path = root / "config.yaml"
    if not config_path.exists():
        config_path = root / "config" / "config.yaml"
    app = create_app(str(config_path))

    port = 5123
    if _port_in_use(port):
        if _is_our_server(port):
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        for candidate in range(5124, 5224):
            if not _port_in_use(candidate):
                port = candidate
                break
    url = f"http://127.0.0.1:{port}/"

    if os.environ.get("MYGO_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print("=" * 50)
    print("  It's Our Cry!!!!! 配音工作台")
    print("  " + url)
    print("  按 Ctrl+C 停止服务")
    print("=" * 50)

    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
