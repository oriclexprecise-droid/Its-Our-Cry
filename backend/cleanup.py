"""Clean-up helpers for removing redundant bundled files after deployment."""
import shutil
from pathlib import Path


def _dir_size(path):
    total = 0
    try:
        for p in Path(path).rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except Exception:
        pass
    return total


def _file_size(path):
    try:
        return path.stat().st_size
    except Exception:
        return 0


def _size_of(targets):
    total = 0
    for target in targets:
        if target.is_dir():
            total += _dir_size(target)
        elif target.is_file():
            total += _file_size(target)
    return total


def _group_targets(project_root, key):
    root = Path(project_root)
    if key == "model_weights":
        return [root / "GPT_weights_v2ProPlus", root / "SoVITS_weights_v2ProPlus"]
    if key == "logs_cache":
        return [
            root / "launcher.log",
            root / "server.err.log",
            root / "server.out.log",
            root / "__pycache__",
            root / "feedback",
        ]
    if key == "build_cache":
        return [root / "build", root / "dist"]
    return []


def scan_cleanable(project_root, gs_path=""):
    root = Path(project_root).resolve()
    gs = Path(gs_path).resolve() if str(gs_path).strip() else None
    gs_exists = bool(gs and gs.exists())

    model_entries = []
    missing_models = []
    seen = set()
    for sub, ext in (("GPT_weights_v2ProPlus", ".ckpt"), ("SoVITS_weights_v2ProPlus", ".pth")):
        bundled_dir = root / sub
        gs_dir = gs / sub if gs else None
        if not bundled_dir.is_dir():
            continue
        for f in sorted(bundled_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() != ext:
                continue
            key = (sub, f.name)
            if key in seen:
                continue
            seen.add(key)
            installed = bool(gs_dir and (gs_dir / f.name).exists())
            entry = {
                "kind": "GPT" if sub.startswith("GPT") else "SoVITS",
                "name": f.name,
                "rel": sub + "/" + f.name,
                "bundled": True,
                "installed": installed,
                "size": _file_size(f),
            }
            model_entries.append(entry)
            if not installed:
                missing_models.append({
                    "kind": entry["kind"],
                    "name": f.name,
                    "rel": entry["rel"],
                })

    groups = []
    group_specs = [
        ("model_weights", "角色模型权重", "GPT_weights_v2ProPlus + SoVITS_weights_v2ProPlus",
         bool(model_entries), not missing_models,
         "SoVITS 中缺少部分模型，请先在「部署」里复制缺失模型，再清理。" if missing_models
         else "已确认模型在 SoVITS 中齐全，可以清理程序包内的模型副本。"),
        ("logs_cache", "日志与反馈记录", "launcher.log / feedback / __pycache__ 等", True, True,
         "会删除本地日志、反馈记录和 Python 缓存，不影响已导出结果。"),
        ("build_cache", "构建缓存（开发版）", "build / dist 目录", True, True,
         "仅开发环境存在，清理后如需再打包安装包必须重新构建。"),
    ]
    total_bytes = 0
    for key, label, detail, exists, safe, warning in group_specs:
        if not exists:
            continue
        targets = _group_targets(root, key)
        size = _size_of(targets)
        total_bytes += size
        groups.append({
            "key": key,
            "label": label,
            "detail": detail,
            "size": size,
            "safe": safe,
            "warning": warning,
            "targets": [str(t.relative_to(root)) for t in targets],
        })

    return {
        "gptsovits_path": str(gs or ""),
        "gs_exists": gs_exists,
        "model_entries": model_entries,
        "missing_models": missing_models,
        "groups": groups,
        "total_bytes": total_bytes,
    }


def clean_items(project_root, gs_path, keys, confirm_missing=False):
    root = Path(project_root).resolve()
    scan = scan_cleanable(root, gs_path)
    group_map = {g["key"]: g for g in scan["groups"]}
    removed = []
    freed = 0
    errors = []
    for key in keys:
        group = group_map.get(key)
        if not group:
            errors.append("未知清理项: " + str(key))
            continue
        if key == "model_weights" and scan["missing_models"] and not confirm_missing:
            errors.append("模型尚未在 GPT-SoVITS 中补齐，已取消清理角色模型。")
            continue
        for rel in group.get("targets", []):
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append("拒绝清理项目目录之外的文件: " + str(target))
                continue
            if not target.exists():
                continue
            if target.is_dir():
                freed += _dir_size(target)
                shutil.rmtree(target, ignore_errors=True)
            else:
                freed += _file_size(target)
                try:
                    target.unlink(missing_ok=True)
                except Exception as e:
                    errors.append(str(target) + ": " + str(e))
            if not target.exists():
                removed.append(rel)
    return {
        "removed": removed,
        "freed_bytes": freed,
        "errors": errors,
        "scan": scan_cleanable(root, gs_path),
    }