# 安全说明

## API Key

- 应用内填写的 API Key 使用 Windows 加密存储（DPAPI），不会以明文写入项目文件。
- `config.yaml` 与 `packaging/release_config.yaml` 中的 `api_key` 默认留空，请勿把真实 Key 提交到仓库。
- 界面只显示掩码，设置中提供“清除 API Key”按钮。

## 敏感文件

以下文件已加入 `.gitignore`，不会被提交到仓库：

- `user_settings.json`
- `model_aliases.json`
- `feedback/`
- `exports/`
- `output/`

## 数据

- 剧本默认保存在本地项目记录中，导出由用户主动发起。
- AI 分析会把剧本文本发送到用户配置的 AI 服务（默认 DeepSeek），请使用自己的 API Key。
