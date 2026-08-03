# MyGO TTS 配音工作台

剧本 -> DeepSeek 情绪分析 -> GPT-SoVITS 多角色 TTS -> 合并音频 + SRT 字幕。

## 必须遵守：git 版本管理
- 每次开始修改代码前，先检查 `git status`。如果工作区有未提交改动，先提交一个“修改前”版本点。
- 每完成一轮功能修改，立即 `git add -A` 并 `git commit`，提交信息写清楚改了什么。
- 用户说“回退”时，优先使用 git 提交历史回退，不要用破坏性命令丢弃没有明确要求丢弃的内容。
- 任何删除、覆盖、重命名前，先确认路径在项目内，并确保当前状态已有 git 提交或备份。
- 不要把 `output/`、`outputs/`、`work/`、`__pycache__/`、日志文件提交进 git（已在 .gitignore 中）。

## 运行方式
- 服务入口：`main.py`，端口 `5123`，地址 `http://127.0.0.1:5123`。
- 使用 GPT-SoVITS 自带 Python 启动：`E:\GPT-SoVITS-v2pro-20250604-nvidia50\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe main.py`。
- 手动启动也可以双击 `start.bat`。
- 助手只负责清理 5123 上所有残留的 main.py 实例；服务由用户自己启动，不要自动拉起服务。

## 关键状态
- 当前基线提交：`d3dc8f6 restore from 2026-07-26 backup`，这是已验证可用的版本。
- 角色：千早爱音、要乐奈、高松灯、椎名立希、长崎素世。
- 情绪：生气、告别、哭泣、感动、决心、悲伤、认真、害羞、微笑、惊讶、思考。
- 参考音频目录：`reference_audio/<角色>/<情绪>/`，只有千早爱音和长崎素世的“微笑”目前有音频，其余目录待用户填充。

## 打包边界（必须遵守）
- 安装包只包含我们自己的内容：程序代码、前端页面、config、角色权重（GPT_weights_v2ProPlus / SoVITS_weights_v2ProPlus）、参考音频、图片背景等。
- GPT-SoVITS 完整运行时、PyTorch/torchaudio、ffmpeg 等大型依赖一律不打包，全部由“部署”板块引导用户下载/安装。
- 用户安装后得到的是独立可启动的 app（自带轻量运行环境或独立 exe），不应依赖用户机器上已有的 GPT-SoVITS。
- TTS 推理时通过部署板块里用户确认的 GPT-SoVITS 路径调用其 runtime 的 python.exe。

## 与用户协作约定（用户要求长期遵守）
- 改大功能前，先提醒并让用户确认保存一个 git 版本点，确认后再动手。
- 每完成一轮修改，主动给用户小结：改了什么、验证了什么、安装包（如适用）在哪里。
