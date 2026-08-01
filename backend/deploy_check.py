"""Environment scan for the MyGO TTS deploy panel."""
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


CORE_PACKAGES = [
    ("torch", "torch"),
    ("torchaudio", "torchaudio"),
    ("transformers", "transformers"),
    ("numpy", "numpy"),
    ("scipy", "scipy"),
    ("librosa", "librosa"),
    ("soundfile", "soundfile"),
    ("PyYAML", "PyYAML"),
    ("flask", "flask"),
    ("requests", "requests"),
]

OPTIONAL_PACKAGES = [
    ("gradio", "gradio"),
    ("jieba", "jieba"),
    ("pypinyin", "pypinyin"),
    ("cn2an", "cn2an"),
    ("faiss-cpu", "faiss-cpu"),
    ("onnxruntime", "onnxruntime"),
]



GPT_SOVITS_DOWNLOADS = [
    {
        "id": "legacy",
        "label": "50 以下显卡",
        "url": "https://cdn-lfs-cn-1.modelscope.cn/prod/lfs-objects/bd/60/d0796553ff05d8568136e199c13e0dc22ebe2ed24273134e34ed6f215cd6?filename=GPT-SoVITS-v2pro-20250604.7z&namespace=FlowerCry&repository=gpt-sovits-7z-pacakges&revision=master&tag=model&auth_key=1785588571-05fb23d6735342c8ba95fc96704cd7f1-0-6cbc6316a77ea887a9a59f3df0a5a37f",
    },
    {
        "id": "nvidia50",
        "label": "RTX 50 系列",
        "url": "https://cdn-lfs-cn-1.modelscope.cn/prod/lfs-objects/97/b4/edcd451c42357db7e26e6c1c877ca5d85144fe97beaff6d7005d35bee008?filename=GPT-SoVITS-v2pro-20250604-nvidia50.7z&namespace=FlowerCry&repository=gpt-sovits-7z-pacakges&revision=master&tag=model&auth_key=1785588573-af7707cace9346aeb9b3052c20f885f8-0-e14f1d626dcafebb3760d90693f1a01e",
    },
]


def recommend_download(gpus):
    for gpu in gpus:
        name = gpu.get("name", "")
        if re.search(r"\b50\d{2}\b", name) or "RTX 50" in name:
            return dict(GPT_SOVITS_DOWNLOADS[1])
    return dict(GPT_SOVITS_DOWNLOADS[0])

def _run(cmd, timeout=10):
    kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, **kwargs)
        return (proc.stdout or proc.stderr or "").strip()
    except Exception:
        return ""


def _pkg_version(*names):
    for name in names:
        try:
            return importlib.metadata.version(name)
        except Exception:
            continue
    return None


def _runtime_python(gs_path):
    """打包版 app 里 sys.executable 是 exe，pip 必须用 GPT-SoVITS runtime。"""
    if gs_path:
        candidate = Path(gs_path) / "runtime" / "python.exe"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _runtime_pkg_versions(python_exe):
    """用目标 python 一次性列出已安装包版本。"""
    code = (
        "import importlib.metadata as m\n"
        "print('\\n'.join(d.metadata['Name'] + '==' + d.version for d in m.distributions()))"
    )
    out = _run([python_exe, "-c", code], timeout=30)
    result = {}
    for line in out.splitlines():
        if "==" in line:
            name, _, ver = line.partition("==")
            result[name.strip().lower()] = ver.strip()
    return result


def _gb(num):
    try:
        return round(float(num) / (1024 ** 3), 1)
    except Exception:
        return None


def _memory_total_gb():
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return round(stat.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        pass
    return None


def _gpus():
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    out = _run([
        smi,
        "--query-gpu=name,memory.total,memory.free,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append({
                "name": parts[0],
                "vram_gb": _gb(parts[1]),
                "free_gb": _gb(parts[2]),
                "driver": parts[3],
            })
    return gpus


def _cuda_version():
    smi = shutil.which("nvidia-smi")
    if smi:
        out = _run([smi])
        m = re.search(r"CUDA Version:\s*([\d.]+)", out)
        if m:
            return m.group(1)
    nvcc = shutil.which("nvcc")
    if nvcc:
        out = _run([nvcc, "--version"])
        m = re.search(r"release\s+([\d.]+)", out)
        if m:
            return m.group(1)
    return None


def _disk_gb(path):
    try:
        usage = shutil.disk_usage(path)
        return {
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "total_gb": round(usage.total / (1024 ** 3), 1),
        }
    except Exception:
        return None


def _ffmpeg_info(gs_path=None):
    exe = shutil.which("ffmpeg")
    if not exe and gs_path:
        bundled = Path(gs_path) / "runtime" / "ffmpeg.exe"
        if bundled.exists():
            exe = str(bundled)
    if not exe:
        return {"installed": False, "version": "", "path": ""}
    out = _run([exe, "-version"], timeout=5)
    first = out.splitlines()[0] if out else exe
    return {"installed": True, "version": first, "path": exe}



def _parse_cuda_number(cuda_version):
    if not cuda_version:
        return None
    parts = cuda_version.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return major + minor / 10.0
    except Exception:
        return None


def _torch_index(gpus, cuda_version):
    if not gpus:
        return "https://download.pytorch.org/whl/cpu"
    ver = _parse_cuda_number(cuda_version)
    if ver is None:
        return None
    if ver >= 12.6:
        return "https://download.pytorch.org/whl/cu126"
    if ver >= 12.4:
        return "https://download.pytorch.org/whl/cu124"
    if ver >= 12.1:
        return "https://download.pytorch.org/whl/cu121"
    if ver >= 11.8:
        return "https://download.pytorch.org/whl/cu118"
    return None


def build_install_plan(packages, gpus, cuda_version, python_exe=None):
    missing = [p["name"] for p in packages if not p["installed"]]
    if not missing:
        return {
            "packages": [],
            "torch_index": None,
            "commands": [],
            "note": "依赖已就绪，无需安装",
            "gpu_mode": None,
        }
    torch_pkgs = [name for name in ("torch", "torchaudio") if name in missing]
    other_pkgs = [name for name in missing if name not in ("torch", "torchaudio")]
    python_exe = python_exe or sys.executable
    index = _torch_index(gpus, cuda_version) if torch_pkgs else None

    commands = []
    if torch_pkgs:
        cmd = [python_exe, "-m", "pip", "install"] + torch_pkgs
        if index:
            cmd += ["--index-url", index]
        commands.append(cmd)
    if other_pkgs:
        commands.append([python_exe, "-m", "pip", "install"] + other_pkgs)

    if torch_pkgs:
        if gpus:
            mode = "gpu"
            if index:
                note = "检测到 NVIDIA 显卡，PyTorch 将匹配 CUDA " + (cuda_version or "未知") + " 安装"
            else:
                note = "检测到 NVIDIA 显卡，但未识别到 CUDA 版本，将使用默认 PyTorch 源"
        else:
            mode = "cpu"
            note = "未检测到 NVIDIA 显卡，将安装 CPU 版 PyTorch"
    else:
        mode = None
        note = "PyTorch 已安装，其余依赖将通过 pip 安装"

    return {
        "packages": missing,
        "torch_index": index,
        "commands": commands,
        "note": note,
        "gpu_mode": mode,
    }




def get_download_options():
    return {"options": GPT_SOVITS_DOWNLOADS, "recommended": recommend_download(_gpus())}

def scan_environment(config, project_root, gptsovits_path=None):
    gs_path = Path(gptsovits_path or config["gptsovits_path"])
    gs_exists = gs_path.exists()
    os_name = platform.system()
    os_release = platform.release()
    if sys.platform == "win32":
        try:
            if sys.getwindowsversion().build >= 22000:
                os_release = "11"
        except Exception:
            pass

    checks = {}
    for sub in ["GPT_SoVITS", "tools", "pretrained_models", "runtime", "configs"]:
        checks[sub] = (gs_path / sub).exists() if gs_exists else False
    runtime_python = (gs_path / "runtime" / "python.exe").exists() if gs_exists else False
    runtime_python_exe = _runtime_python(str(gs_path))

    packages = []
    pkg_map = None
    if gs_exists and runtime_python_exe != sys.executable:
        pkg_map = _runtime_pkg_versions(runtime_python_exe)
    for display, meta in CORE_PACKAGES + OPTIONAL_PACKAGES:
        if pkg_map is not None:
            version = pkg_map.get(meta.lower())
        else:
            version = _pkg_version(display, meta)
        packages.append({"name": display, "version": version, "installed": bool(version)})

    ffmpeg = _ffmpeg_info(gs_path)
    gpus = _gpus()
    cuda_version = _cuda_version()
    core_names = {name for name, _ in CORE_PACKAGES}
    install_plan = build_install_plan(packages, gpus, cuda_version, python_exe=runtime_python_exe)

    issues = []

    model_entries = []
    for char, char_cfg in config.get("characters", {}).items():
        rels = [
            ("SoVITS", char_cfg.get("model_rel") or char_cfg.get("model") or ""),
            ("GPT", char_cfg.get("gpt_model_rel") or char_cfg.get("gpt_model") or ""),
        ]
        for kind, rel in rels:
            rel = str(rel).replace("\\", "/")
            if not rel:
                continue
            source = project_root / rel
            dest = gs_path / rel
            bundled = source.exists()
            installed = gs_exists and dest.exists()
            model_entries.append({
                "character": char,
                "kind": kind,
                "rel": rel,
                "bundled": bundled,
                "installed": installed,
            })
            if not bundled:
                issues.append("角色模型未随程序提供: " + char + " " + kind)
            elif not installed:
                issues.append("角色模型未安装: " + char + " " + kind)
    if not gs_exists:
        issues.append("GPT-SoVITS directory not found: " + str(gs_path))
    else:
        if not checks["GPT_SoVITS"]:
            issues.append("GPT_SoVITS directory is missing")
        if not runtime_python:
            issues.append("runtime/python.exe is missing")

    for pkg in packages:
        if pkg["name"] in core_names and not pkg["installed"]:
            issues.append(pkg["name"] + " is not installed")

    if not ffmpeg["installed"]:
        issues.append("ffmpeg is not installed")

    return {
        "os": {
            "system": os_name,
            "release": os_release,
            "version": platform.version(),
            "arch": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": runtime_python_exe,
            "pip": _run([runtime_python_exe, "-m", "pip", "--version"]) or "pip not found",
        },
        "cpu": platform.processor() or platform.machine(),
        "cores": os.cpu_count(),
        "memory_total_gb": _memory_total_gb(),
        "disk": _disk_gb(project_root),
        "gptsovits_disk": _disk_gb(gs_path) if gs_exists else None,
        "gpu": gpus,
        "cuda_version": cuda_version,
        "packages": packages,
        "models": model_entries,
        "ffmpeg": ffmpeg,
        "gptsovits": {
            "path": str(gs_path),
            "exists": gs_exists,
            "runtime_python": runtime_python,
            "checks": checks,
        },
        "install_plan": install_plan,
        "download_options": GPT_SOVITS_DOWNLOADS,
        "recommended_download": recommend_download(gpus),
        "issues": issues,
        "ready": not issues,
    }
