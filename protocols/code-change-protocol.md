# 代码改动协议

本协议规定改代码的**强制约束与验收标准**。流水线步骤见 `aiops/skills/code-change/SKILL.md`。

## 适用场景

- 任何对源码、配置、脚本的功能性改动。

## 必须产出什么

- 最小 diff（附带本次改动的 files_changed）。
- 测试运行记录：命令 + PASS | FAIL。
- 若结论有变化：新增 Evidence、更新 Decision。

## 禁止事项

- 禁止顺手重构、扩大 scope、批量格式化无关文件。
- 发现无关问题只登记到 Task Queue，不就地修改。
- 禁止擅自安装依赖或升级版本。
- 禁止跳过回归直接声称通过。
- 若改动涉及科学结论：区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN；相关性不得写成因果；未收敛结果不得当作连续介质极限。

## 验收检查

- [ ] diff 仅包含本次 scope 内的文件。
- [ ] 局部测试与回归均有实际输出。
- [ ] 静态检查通过（或已记录无法运行的客观原因）。
- [ ] 结论变化已同步到 Evidence / Decision。
