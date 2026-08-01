"""It's Our Cry!!!!! 自包含启动器。

开发模式:  python launcher.py
打包模式:  双击 ItsOurCry.exe（PyInstaller --noconsole）
"""

import os
import socket
import subprocess
import sys
import threading
import urllib.request
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


def _write_log(root, text):
    try:
        (root / "launcher.log").write_text(text, encoding="utf-8")
    except Exception:
        pass


def _find_edge():
    roots = [
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if candidate.exists():
            return str(candidate)
    return None


def _open_desktop_window(url):
    try:
        import webview

        webview.create_window(
            "It's Our Cry!!!!!",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
        )
        webview.start()
        return
    except Exception as e:
        _write_log(app_root(), "webview failed, fallback edge app mode:\n" + str(e))

    edge = _find_edge()
    if edge:
        try:
            subprocess.Popen([edge, "--app=" + url])
            return
        except Exception as e:
            _write_log(app_root(), "edge app mode failed:\n" + str(e))

    import webbrowser

    webbrowser.open(url)


def main():
    # 无控制台模式下 sys.stdout/stderr 是 None，Flask/click 打印 banner 会崩
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    root = app_root()
    _write_log(root, "launcher start: " + str(root))
    os.chdir(str(root))
    sys.path.insert(0, str(root))

    try:
        from backend.server import create_app
        _write_log(root, "backend imported")
    except Exception as e:
        import traceback
        _write_log(root, "import failed:\n" + traceback.format_exc())
        raise

    try:
        config_path = root / "config.yaml"
        if not config_path.exists():
            config_path = root / "config" / "config.yaml"
        app = create_app(str(config_path))
        _write_log(root, "app created")

        port = 5123
        if _port_in_use(port):
            if _is_our_server(port):
                _write_log(root, "server already running on " + str(port))
                if os.environ.get("MYGO_NO_BROWSER") == "1":
                    print(f"server url: http://127.0.0.1:{port}/")
                    return
                _open_desktop_window(f"http://127.0.0.1:{port}/")
                return
            for candidate in range(5124, 5224):
                if not _port_in_use(candidate):
                    port = candidate
                    break
        url = f"http://127.0.0.1:{port}/"
        _write_log(root, "server url: " + url)

        if os.environ.get("MYGO_NO_BROWSER") == "1":
            print("=" * 50)
            print("  It's Our Cry!!!!! 配音工作台")
            print("  " + url)
            print("  按 Ctrl+C 停止服务")
            print("=" * 50)

            app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
            return

        server_thread = threading.Thread(
            target=app.run,
            kwargs={
                "host": "127.0.0.1",
                "port": port,
                "debug": False,
                "use_reloader": False,
                "threaded": True,
            },
            daemon=True,
            name="itsourcry-flask",
        )
        server_thread.start()
        _open_desktop_window(url)
    except Exception as e:
        import traceback
        _write_log(root, "run failed:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()