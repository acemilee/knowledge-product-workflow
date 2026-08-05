# KP 模块目录说明

`module/` 保留给旧模块目录和目录索引兼容使用，不是可部署 Skill 源。

唯一可部署、可测试和可升级的内容是项目内的规范 `kp-*` Skill 包及其包清单。不要直接修改或安装本目录中的角色说明；恢复运行时先执行 `kp status --json --workspace <workspace>`。
