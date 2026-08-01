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


def _ffmpeg_info():
    exe = shutil.which("ffmpeg")
    if not exe:
        return {"installed": False, "version": "", "path": ""}
    out = _run([exe, "-version"], timeout=5)
    first = out.splitlines()[0] if out else exe
    return {"installed": True, "version": first, "path": exe}


def scan_environment(config, project_root, gptsovits_path=None):
    gs_path = Path(gptsovits_path or config["gptsovits_path"])
    gs_exists = gs_path.exists()

    checks = {}
    for sub in ["GPT_SoVITS", "tools", "pretrained_models", "runtime", "configs"]:
        checks[sub] = (gs_path / sub).exists() if gs_exists else False
    runtime_python = (gs_path / "runtime" / "python.exe").exists() if gs_exists else False

    packages = []
    for display, meta in CORE_PACKAGES + OPTIONAL_PACKAGES:
        version = _pkg_version(display, meta)
        packages.append({"name": display, "version": version, "installed": bool(version)})

    ffmpeg = _ffmpeg_info()
    gpus = _gpus()
    cuda_version = _cuda_version()
    core_names = {name for name, _ in CORE_PACKAGES}

    issues = []
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
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "arch": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "pip": _run([sys.executable, "-m", "pip", "--version"]) or "pip not found",
        },
        "cpu": platform.processor() or platform.machine(),
        "cores": os.cpu_count(),
        "memory_total_gb": _memory_total_gb(),
        "disk": _disk_gb(project_root),
        "gptsovits_disk": _disk_gb(gs_path) if gs_exists else None,
        "gpu": gpus,
        "cuda_version": cuda_version,
        "packages": packages,
        "ffmpeg": ffmpeg,
        "gptsovits": {
            "path": str(gs_path),
            "exists": gs_exists,
            "runtime_python": runtime_python,
            "checks": checks,
        },
        "issues": issues,
        "ready": not issues,
    }