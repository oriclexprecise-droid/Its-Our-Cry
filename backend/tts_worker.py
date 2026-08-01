"""GPT-SoVITS TTS 子进程推理脚本（由打包版 app 调用）。

用法:
  runtime\python.exe tts_worker.py --request <request.json>

request.json 字段:
  gptsovits_path / model_path / gpt_model_path / text / ref_audio_path / output_path / params

结果写入 <output_path 同名>.result.json。
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    req = json.loads(Path(args.request).read_text(encoding="utf-8"))
    result_path = Path(req["output_path"]).with_suffix(".result.json")

    try:
        gs = Path(req["gptsovits_path"]).resolve()
        sys.path.insert(0, str(gs))
        sys.path.insert(0, str(gs / "GPT_SoVITS"))
        os.chdir(str(gs))

        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

        config = TTS_Config(str(gs / "GPT_SoVITS" / "configs" / "tts_infer.yaml"))
        pipeline = TTS(config)

        pipeline.init_vits_weights(str(Path(req["model_path"]).resolve()))
        gpt_model = str(req.get("gpt_model_path") or "").strip()
        if gpt_model:
            pipeline.init_t2s_weights(str(Path(gpt_model).resolve()))

        params = req.get("params", {}) or {}
        tts_req = {
            "text": req["text"],
            "text_lang": params.get("text_lang", "zh"),
            "ref_audio_path": req["ref_audio_path"],
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

        print("############ 推理 ############")
        print("text: " + str(tts_req["text"]))
        generator = pipeline.run(tts_req)
        sr, audio = next(generator)

        import numpy as np  # noqa: F401
        import soundfile as sf

        out = Path(req["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), audio, sr)
        duration = len(audio) / sr

        result = {"duration": duration, "output": str(out)}
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        print("WORKER_OK duration=" + str(round(duration, 4)))
    except Exception as e:
        try:
            result_path.write_text(
                json.dumps({"error": str(e), "traceback": traceback.format_exc()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        print("WORKER_ERROR: " + str(e))
        print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
