# 整段组合器：Apple Silicon 适配版（待实机验证）

这个仓库包含整段组合器源码及 Mac 适配工作。支持的目标是 Apple Silicon macOS；**尚未在真实 Mac 上完成端到端测试**，不应当作为已验证的生产版本。

用户授权公开的顶部文案和口播／字幕文案保存在 `starter_content/`，任何人都可以查看。首次启动会在文件不存在时初始化本地文案库；已有文件不会被覆盖。原始素材、音乐、音效、音色、模型和历史批次不包含在仓库中。请阅读 [Mac 首次使用](MAC首次使用.md)，然后把素材自行拷贝到 Mac。首次准备需要网络、磁盘空间、Homebrew 和模型下载；部分下载及软件安装可能需要系统交互。

首次准备：`bash setup_mac.sh`。日常启动：`bash start_mac.sh`。启动前请先核对 [Mac 首次使用](MAC首次使用.md) 中的限制和验收步骤。

首次安装或启动会依据 [剪辑目录清单](starter_content/workspace_directories.json) 创建空目录，保留 Windows `D:\DY video` 中与剪辑相关的素材、音乐、配音、音效及输出目录层级。实际素材、音频及音色配置需要自行复制；仅创建目录不会导入音效绑定或设置。

本项目调用 [IndexTTS 2.5](https://github.com/index-tts/index-tts) 和相关模型，请遵守各自许可证。本仓库不包含模型权重及第三方运行时。
