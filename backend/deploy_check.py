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
        "url": "https://us.aws.cdn.hf.co/xet-bridge-us/65a6a250b3c1a539e0260480/cdb751ef40f106a59c3bcc6bc5fd2078580448ef71da1b01a1bec22ee6e88dcc?X-Xet-Cas-Uid=public&user_id=public&response-content-disposition=attachment%3B+filename*%3DUTF-8%27%27GPT-SoVITS-v2pro-20250604.7z%3B+filename%3D%22GPT-SoVITS-v2pro-20250604.7z%22%3B&response-content-type=application%2Fx-7z-compressed&Expires=1785590021&Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly91cy5hd3MuY2RuLmhmLmNvL3hldC1icmlkZ2UtdXMvNjVhNmEyNTBiM2MxYTUzOWUwMjYwNDgwL2NkYjc1MWVmNDBmMTA2YTU5YzNiY2M2YmM1ZmQyMDc4NTgwNDQ4ZWY3MWRhMWIwMWExYmVjMjJlZTZlODhkY2NcXD9YLVhldC1DYXMtVWlkPXB1YmxpYyZ1c2VyX2lkPXB1YmxpYyZyZXNwb25zZS1jb250ZW50LWRpc3Bvc2l0aW9uPWF0dGFjaG1lbnQlM0IrZmlsZW5hbWUlMkElM0RVVEYtOCUyNyUyN0dQVC1Tb1ZJVFMtdjJwcm8tMjAyNTA2MDQuN3olM0IrZmlsZW5hbWUlM0QlMjJHUFQtU29WSVRTLXYycHJvLTIwMjUwNjA0Ljd6JTIyJTNCJnJlc3BvbnNlLWNvbnRlbnQtdHlwZT1hcHBsaWNhdGlvbiUyRngtN3otY29tcHJlc3NlZCIsIkNvbmRpdGlvbiI6eyJEYXRlTGVzc1RoYW4iOnsiRXBvY2hUaW1lIjoxNzg1NTkwMDIxfX19XX0_&Signature=MEYCIQCcZIzeS7fKaPiWuRevi1WSXQ2F9mkX%7EhxzZBK64LZ8jwIhAJUzD85aoBwNFf4CgaILqwRK5tH68TCexWeNgoM319FK&Key-Pair-Id=01KXEF4KZ1B6FV465MAWR4M21F",
    },
    {
        "id": "nvidia50",
        "label": "RTX 50 系列",
        "url": "https://cdn-lfs-cn-1.modelscope.cn/prod/lfs-objects/97/b4/edcd451c42357db7e26e6c1c877ca5d85144fe97beaff6d7005d35bee008?filename=GPT-SoVITS-v2pro-20250604-nvidia50.7z&namespace=FlowerCry&repository=gpt-sovits-7z-pacakges&revision=master&tag=model&auth_key=1785586470-fa2b43da278a47519982abffa31910b0-0-11244f2ef4f8d7b8a9d8aeac9280db06",
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


def build_install_plan(packages, gpus, cuda_version):
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
    index = _torch_index(gpus, cuda_version) if torch_pkgs else None

    commands = []
    if torch_pkgs:
        cmd = [sys.executable, "-m", "pip", "install"] + torch_pkgs
        if index:
            cmd += ["--index-url", index]
        commands.append(cmd)
    if other_pkgs:
        commands.append([sys.executable, "-m", "pip", "install"] + other_pkgs)

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
    install_plan = build_install_plan(packages, gpus, cuda_version)

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
        "install_plan": install_plan,
        "download_options": GPT_SOVITS_DOWNLOADS,
        "recommended_download": recommend_download(gpus),
        "issues": issues,
        "ready": not issues,
    }