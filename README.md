# It's Our Cry!!!!!

**给每个角色一个会呼吸的声音：剧本分析 → 情绪校对 → 多角色配音 → 合成字幕，一条龙完成。**

It's Our Cry!!!!! 是一款面向 MyGO!!!!! / Ave Mujica 同人二创的本地配音工作台。把剧本粘贴进来，剩下的交给 AI 与本地语音合成：自动分析情绪、自动匹配角色与参考音频、生成多角色配音，最后输出合并音频、分角色音轨与 SRT 字幕。

> 🛠️ 本项目全部由 AI 完成开发（DeepSeek / Codex），由 ORiCale 负责产品设计、测试与发布。

## 功能亮点

- 桌面应用，点开即用，安装包支持自选目录，不强制安装到 C 盘。
- AI 剧本分析：自动识别角色与旁白，标注 11 类情绪，AI 角色名纠错，支持中/日双语配音。
- 人机协同校对：单条台词可修改角色、台词、情绪与前间隔，不满意可单独重新生成。
- 多角色语音合成：内置 13 个角色模型，按“角色 + 情绪”匹配参考音频；缺少素材时自动降级为纯字幕，不中断流程。
- 成品导出：合并音频、分角色音轨、SRT 字幕严格对齐，旁白字幕时长自动估算；支持 WebGAL 工作流导出。
- 部署工具：环境扫描、显卡版本匹配、GPT-SoVITS 整合包下载/解压、依赖一键安装、空间清理。
- 预设共享：纠音词典、情绪参数模板、脚本情绪映射、参考音频库均可导出/导入。
- 隐私友好：API Key 使用 Windows 加密存储，可一键清除。

## 仓库说明

本仓库只包含程序源码、配置模板、前端页面与打包脚本，**不包含**角色模型权重与参考音频（约 4GB），这些素材由安装包/网盘分发，避免仓库体积过大。

- 安装包：`release/It-sOurCry-Setup-Inno.exe`
- 需要 GPT-SoVITS 运行时：由应用内“部署”板块引导下载安装
- AI 分析需要 DeepSeek 或其他 OpenAI 兼容 API Key：在“设置 → API 设置”中填写

## 快速开始（开发者）

```powershell
# 使用 GPT-SoVITS 自带 Python 启动
E:\GPT-SoVITS-v2pro-20250604-nvidia50\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe main.py
```

服务入口为 `main.py`，默认端口 `5123`，地址 `http://127.0.0.1:5123`。

## 技术栈

- 后端：Python / Flask
- 前端：原生 HTML / CSS / JavaScript
- 语音合成：GPT-SoVITS v2ProPlus
- AI 分析：DeepSeek（OpenAI 兼容协议，可自定义）

## 许可证

[MIT](LICENSE) © 2026 ORiCale

## 致谢

感谢一直赖宿室、qwertyuiop 等群友提供的帮助。期待更多人的加入，你的付出无论大小都会被记得。

- B 站：https://space.bilibili.com/3493294730381924
- QQ 群：https://qm.qq.com/q/8DXSwA7ZoQ
