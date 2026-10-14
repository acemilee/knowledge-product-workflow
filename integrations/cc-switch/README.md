# KP CC Switch Skill 包

此目录是 Knowledge Product Workflow 的唯一可部署 Skill 源，当前版本为宿主驱动模式：Claude Code 或 Codex 承担模型调用，`kp` CLI 承担工件、路由、质量门和发布控制。

14 个 Skill 的逐项职责、输入输出、协作边界和独立审稿前提见[Skill 架构与职责](../../docs/skills.md)。

## 包内容

- `skills/kp-create/`：唯一默认入口，负责中文状态、审批和专业 Skill 路由。
- `skills/kp-*/`：13 个专业 Skill，包含出版设计、发展性编辑、视觉核验与独立审稿。每个目录携带本地编排契约，可单独复制安装。
- `references/orchestration-contract.md`：编排契约唯一维护源；运行 `python scripts/sync_skill_resources.py` 同步包内镜像和 manifest 哈希。安装器保留共享布局，并校验镜像完整性。
- `claude-code/commands/`：Claude Code 命令适配模板。
- `codex/README.md`：Codex 调用说明。
- `scripts/`：校验、定向安装和可恢复卸载脚本。

## 安装前校验

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\verify-cc-switch-package.ps1 -PackageRoot .
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-cc-switch.ps1 -PackageRoot . -WhatIf
```

确认目标只包含 `kp-*` 和唯一的 KP 编排契约后，移除 `-WhatIf` 执行安装。安装器仅操作 CC Switch Skill 根中的同名 KP 目录及 `references/orchestration-contract.md`；已有版本会移动到相邻的 `skill-backups/`，不会删除其他 Skill。
