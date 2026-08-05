"""GPT-SoVITS TTS 子进程推理脚本（由打包版 app 调用）。

用法:
  runtime\python.exe tts_worker.py --request <request.json>
  runtime\python.exe tts_worker.py --batch <batch.json>

单条 request.json 字段:
  gptsovits_path / model_path / gpt_model_path / text / ref_audio_path / output_path / params

批量 batch.json 字段:
  gptsovits_path / cancel_file / jobs: [ {id, model_path, gpt_model_path, text, ref_audio_path, output_path, params} ]

结果统一写入 <output_path 同名>.result.json。
"""

import argparse
import json
import os
import sys
import traceback
from collections import OrderedDict
from pathlib import Path


def _write_result(output_path, result):
    result_path = Path(output_path).with_suffix(".result.json")
    try:
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _prepare_stdio():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _build_tts_request(job, params):
    return {
        "text": job["text"],
        "text_lang": params.get("text_lang", "zh"),
        "ref_audio_path": job["ref_audio_path"],
        "prompt_text": params.get("prompt_text", ""),
        "prompt_lang": params.get("prompt_lang", "zh"),
        "text_split_method": params.get("text_split_method", "cut5"),
        "batch_size": params.get("batch_size", 1),
        "speed_factor": params.get("speed_factor", 1.0),
        "fragment_interval": params.get("fragment_interval", 0.3),
        "temperature": params.get("temperature", 1.0),
        "top_k": params.get("top_k", 15),
        "top_p": params.get("top_p", 1.0),
        "seed": params.get("seed", -1),
        "streaming_mode": False,
        "media_type": "wav",
    }


def _load_pipeline(gs):
    sys.path.insert(0, str(gs))
    sys.path.insert(0, str(gs / "GPT_SoVITS"))
    os.chdir(str(gs))

    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

    config = TTS_Config(str(gs / "GPT_SoVITS" / "configs" / "tts_infer.yaml"))
    return TTS(config)


def _switch_weights(pipeline, model_path, gpt_model_path):
    pipeline.init_vits_weights(str(Path(model_path).resolve()))
    gpt_model = str(gpt_model_path or "").strip()
    if gpt_model:
        pipeline.init_t2s_weights(str(Path(gpt_model).resolve()))


def _run_job(pipeline, job):
    params = job.get("params", {}) or {}
    tts_req = _build_tts_request(job, params)

    print("############ 推理 ############")
    print("text: " + str(tts_req["text"]))
    generator = pipeline.run(tts_req)
    sr, audio = next(generator)

    import numpy as np  # noqa: F401
    import soundfile as sf

    out = Path(job["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), audio, sr)
    duration = len(audio) / sr
    _write_result(job["output_path"], {"duration": duration, "output": str(out)})
    print("WORKER_OK duration=" + str(round(duration, 4)))
    return duration


def _process_job(pipeline, job):
    result_path = Path(job["output_path"]).with_suffix(".result.json")
    if result_path.exists():
        try:
            result_path.unlink()
        except Exception:
            pass
    try:
        _run_job(pipeline, job)
    except Exception as e:
        _write_result(job["output_path"], {"error": str(e), "traceback": traceback.format_exc()})
        print("WORKER_ERROR: " + str(e))
        print(traceback.format_exc())


def run_single(req):
    result_path = Path(req["output_path"]).with_suffix(".result.json")
    if result_path.exists():
        try:
            result_path.unlink()
        except Exception:
            pass
    try:
        pipeline = _load_pipeline(Path(req["gptsovits_path"]))
        _switch_weights(pipeline, req["model_path"], req.get("gpt_model_path"))
        _run_job(pipeline, req)
    except Exception as e:
        _write_result(req["output_path"], {"error": str(e), "traceback": traceback.format_exc()})
        print("WORKER_ERROR: " + str(e))
        print(traceback.format_exc())
        sys.exit(1)


def run_batch(batch):
    cancel_file = str(batch.get("cancel_file") or "").strip()
    try:
        pipeline = _load_pipeline(Path(batch["gptsovits_path"]))
    except Exception as e:
        for job in batch.get("jobs", []):
            _write_result(job["output_path"], {"error": "模型加载失败: " + str(e), "traceback": traceback.format_exc()})
        print("WORKER_ERROR: " + str(e))
        print(traceback.format_exc())
        sys.exit(1)

    def cancelled():
        return bool(cancel_file) and os.path.exists(cancel_file)

    groups = OrderedDict()
    for job in batch.get("jobs", []):
        key = (str(job.get("model_path") or ""), str(job.get("gpt_model_path") or ""))
        groups.setdefault(key, []).append(job)

    for (model_path, gpt_model_path), jobs in groups.items():
        if cancelled():
            break
        try:
            _switch_weights(pipeline, model_path, gpt_model_path)
        except Exception as e:
            for job in jobs:
                _write_result(job["output_path"], {"error": "角色模型加载失败: " + str(e), "traceback": traceback.format_exc()})
            print("WORKER_ERROR: " + str(e))
            print(traceback.format_exc())
            continue
        for job in jobs:
            if cancelled():
                break
            _process_job(pipeline, job)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request")
    parser.add_argument("--batch")
    args = parser.parse_args()
    _prepare_stdio()
    if args.batch:
        batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        run_batch(batch)
        return
    req = json.loads(Path(args.request).read_text(encoding="utf-8"))
    run_single(req)


if __name__ == "__main__":
    main()
