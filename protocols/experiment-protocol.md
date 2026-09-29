# 实验协议

本协议规定昂贵实验的**强制约束与验收标准**。执行步骤见 `aiops/skills/experiment-run/SKILL.md`。

## 适用场景

- RCWA / COMSOL / GPU 训练等耗时批次、参数扫描、优化循环。

## 必须记录什么（provenance）

1. 身份与版本：`git_commit`、`dataset_hash`、`config_hash`、`environment_lock`。
2. 模拟条件：求解器、谐波截断、波长/频率约定、偏振、边界条件、材料模型。
3. 执行条件：随机种子、设备、精度、输出目录。
4. **不适用项须显式写 N/A 并给理由**，不得留空、也不得用其他领域的值硬填。

## 必须产出什么

- 一份 Experiment Spec 文件（状态 planned → running → complete/failed/aborted）。
- registry 条目，至少含 id / status / purpose / experiment_file / owner / created_at。
- 若项目已使用 DVC/MLflow 等跟踪系统，只存外部 run_id/URI 与关键 hash，不重造跟踪系统。

## 禁止事项

- 禁止在未记录 provenance 的情况下产出"结果"。
- 禁止用未收敛结果充当连续介质极限或最终结论。
- 禁止把相关性描述成因果。
- 禁止无授权覆盖或删除既有结果目录与大体积产物。
- 科学结论输出必须区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN。

## 验收检查

- [ ] Spec 字段齐全且通过 schema 自检。
- [ ] provenance 十项（求解器/截断/波长约定/偏振/边界/材料/种子/设备/精度/输出目录）均已记录。
- [ ] 验证含容差与参考对比。
- [ ] registry 已登记且 id 唯一。
