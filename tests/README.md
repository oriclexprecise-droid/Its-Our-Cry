# 冒烟测试 (Smoke Tests)

Automated checks for core frontend flows, to catch button-click regressions.

## Run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_smoke.ps1
```

## Covered

1. New project: SRT / WebGaL card selection, AI mode selection auto-syncs card type.
2. SRT workbench: client-generation prompt appears.
3. WebGaL: client-generation prompt appears after parsing the script.
4. API 情绪分析与日语翻译必须与客户端共用同一套提示词构造器。

## Notes

- Starts a temporary server on port 5134 and closes it afterwards.
- Temporary projects are restored from `work\recent_results.json`, so real data is untouched.
- Runs `py_compile` and `node --check` first.
