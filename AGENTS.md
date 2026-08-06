# MyGO TTS 配音工作台

剧本 -> DeepSeek 情绪分析 -> GPT-SoVITS 多角色 TTS -> 合并音频 + SRT 字幕。

## 必须遵守：git 版本管理
- 每次开始修改代码前，先检查 `git status`。如果工作区有未提交改动，先提交一个“修改前”版本点。
- 每完成一轮功能修改，立即 `git add -A` 并 `git commit`，提交信息写清楚改了什么。
- 用户说“回退”时，优先使用 git 提交历史回退，不要用破坏性命令丢弃没有明确要求丢弃的内容。
- 任何删除、覆盖、重命名前，先确认路径在项目内，并确保当前状态已有 git 提交或备份。
- 改动 AI 提示词后，先运行 `tests\check_prompt_consistency.py`（用 GPT-SoVITS runtime python），确认 API 与客户端提示词一致后再提交。
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

## 技能使用约定（用户要求长期遵守）
- 效率优先：技能按任务大小匹配，小改动直接做，不强制走完整流程；只有新功能或大改动才用 brainstorming / writing-plans 确认需求与计划。
- 遇到 bug：默认按 systematic-debugging 先定位根因再修复，不靠乱试。
- 实现功能或修复：默认按 test-driven-development 先写测试再实现；简单改动只补必要测试，不为走流程而加测试。
- 需要并行任务或隔离开发时：使用 subagent-driven-development / using-git-worktrees。
- 完成收尾：按 verification-before-completion 做与本次改动相关的必要验证，通过后再报告完成，不扩大验证范围。
- 代码风格：按 karpathy-guidelines 保持简洁、不过度设计。
- 网页搜索/抓取：使用 firecrawl（需先安装并登录）。
- 用户没有点名技能时，助手按上述规则自动匹配合适技能，不需要用户记忆技能名。

## 下版本未打包清单（用户要求长期遵守）
- 每完成一轮功能修改，立即把“本次改动 + 涉及文件 + 提交号 + 是否已打包”更新到本节。
- 打包新版本前，先读本节，确认所有未打包改动是否纳入。
- 打包完成后，把已纳入本版的条目标记为“已打包”或清空，避免重复打包。
- 当前待打包：
  1. 日志导出包：设置 → 日志 → “导出日志包”，下载 zip（含 launcher.log、logs/server.*.log、feedback/events.jsonl、system.txt）；提交 b57ddef
  2. 前端错误与撤销/重做失败自动上报（frontend_error / undo_failed / redo_failed 事件）；提交 edae0dc
  3. 自定义角色功能：设置 → 模型配置 → 添加角色/模型（选择 .pth/.ckpt 并复制入库）、删除自定义角色；提交 c892fd1
  4. 非原生角色包导入导出迁移：导入导出面板新增非原生角色包板块，默认全选导出多角色包（含模型、参考音频、参考文本、激活词、情绪）；导入支持多角色包与旧单角色包，撞名整包拒绝并提示；模型配置每行移除导入/导出按钮，仅保留添加、删除与激活词；本次提交 f7c51ab
  5. 模型配置「添加角色/模型」按钮与下方提示文字重叠修复，并更新提示文案；本次提交 HASH_PENDING
  6. 状态：以上源码已改、已提交，桌面安装包尚未重建（release 仍为旧版）
