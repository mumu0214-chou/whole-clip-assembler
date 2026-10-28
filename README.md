# 整段组合器：Apple Silicon 适配版（待实机验证）

这个仓库包含整段组合器源码及 Mac 适配工作。支持的目标是 Apple Silicon macOS；**尚未在真实 Mac 上完成端到端测试**，不应当作为已验证的生产版本。

原始素材、音乐、音效、音色、文案、模型和历史批次均不包含在仓库中。请阅读 [Mac 首次使用](MAC首次使用.md)，然后把文件自行拷贝到 Mac。首次准备需要网络、磁盘空间、Homebrew 和模型下载；部分下载及软件安装可能需要系统交互。

首次准备：`bash setup_mac.sh`。日常启动：`bash start_mac.sh`。启动前请先核对 [Mac 首次使用](MAC首次使用.md) 中的限制和验收步骤。

本项目调用 [IndexTTS 2.5](https://github.com/index-tts/index-tts) 和相关模型，请遵守各自许可证。本仓库不包含模型权重及第三方运行时。
