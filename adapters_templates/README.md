# adapters_templates/ — 各 AI 工具适配层模板

真源永远在目标仓库的 `aiops/`。适配层只是"指路牌"：内容是**指针**（告诉 AI 去读
aiops 的哪个文件），不复制正文，因此不会漂移。按你实际使用的工具挑着装。

安装位置一律在**目标仓库根目录**。装完任一适配层后，用同工具开新会话问一句
"这个项目的治理真源在哪"来验证——它应能不靠搜索直接答出 `aiops/`。

---

## 1. ZCode（`.zcode/config.json`）— 确定性最高

复制 `zcode-config.example.json` 为 `<repo>/.zcode/config.json`，把其中的 `__AIOS_PYTHON__`（解释器绝对路径）与 `__AIOS_PROJECT_DIR__`（仓库绝对路径）替换掉——**必须用绝对路径**：D-017 实测 `${ZCODE_PROJECT_DIR}` 变量在会话中途 cd 后会解析到错误位置，曾造成 hook 死锁，`check_adapter_drift.py` 会直接拒绝变量写法。
效果：
- **SessionStart**：每次会话自动注入状态指针（读 STATE.md + 触发表）；
- **PreToolUse(Bash)**：对结果目录的破坏性删除命令程序化拒绝；
- 守卫脚本已随 kit 装在 `aiops/checks/hooks/`（会话注入 session_start_context.py、
  守卫 guard_destructive.py），无需额外安装。

活体验证：新会话运行 `echo ZCODE_HOOK_CANARY`，被拒绝即守卫生效。

## 2. Trae（`.trae/rules/` + `.trae/skills/`）

- 复制 `trae-rules-aiops.example.md` 为 `<repo>/.trae/rules/aiops.md`
  （Trae 会把 .trae/rules 全文注入每次会话）；
- 6 个技能薄包装：为 `aiops/skills/` 下每个技能（task-intake / code-change /
  scientific-analysis / experiment-run / evidence-update / red-team-review）在
  `.trae/skills/<同名>/SKILL.md` 建一份 20 行以内的指针文件（front matter 的
  name/description 从真源抄，正文一句"执行流程见 aiops/skills/<x>/SKILL.md"）。
  注意：装完后运行 `aiops/checks/check_adapter_drift.py` 应通过——它校验
  description 与真源逐字一致。

## 3. Codex（AGENTS.md 已覆盖）

Codex 读根 `AGENTS.md`（bootstrap 已写入 generated 区块），无需额外文件。
可选：把持久事实放入 `.codex/project-memory.md` 并在其中加一行指向 `aiops/`。

## 4. Cursor / Copilot / Gemini

- Cursor：`.cursor/rules/aiops-core.mdc`，正文 = AGENTS.md generated 区块的精简指针版；
- Copilot：`.github/copilot-instructions.md`，同上；
- Gemini：`GEMINI.md`，同上。
（三者都只放指针，规范全文见 Master Spec §23。）

---

## 通用守则

1. 适配层内容里**永远不要复制** protocols/skills 的正文，只写路径指针；
2. 本机解释器路径、主机名等环境信息写在本机文件（如 .zcode/config.json）里可以，
   写进会被多人 clone 的通用文件里不行；
3. 适配层与真源的一致性由 `aiops/checks/check_adapter_drift.py` 把关，改动后跑一次。
