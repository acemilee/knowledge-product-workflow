# Codex 适配说明

CC Switch 安装并同步此包后，在 Codex 当前工作目录中使用：

```text
$kp-create 游戏策划
```

Codex 负责读取 `kp-create` 根 Skill 并使用当前会话模型执行宿主驱动流程；`kp` CLI 仍只负责工件、质量门和 PDF 渲染，不会自行调用 LLM API。

若当前 Codex 客户端未将用户可调用 Skill 暴露为 `$` 入口，直接在对话中点名 `kp-create` Skill，并在目标目录启动。不得通过粘贴旧版长提示词绕过 Skill 的固定约束。

`/kp-create` 是 Claude Code Command 入口；Codex 是否支持同名斜杠入口取决于客户端，不能假定。
