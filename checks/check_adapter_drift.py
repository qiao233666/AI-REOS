"""
Adapter 漂移检查（对应 T-006）。

用途
----
`.trae/` 是本地适配层（已被 gitignore），它对 `aiops/` 真源存在少量必要的重复——
主要是 6 个技能薄包装 front matter 中的 `description`（供 Trae 路由使用）。
本脚本检查这份重复是否与真源一致，避免"改了 aiops 里的 description，包装里还是旧的"
这种静默漂移。

检查内容
--------
1. 对 `aiops/skills/` 下的每个技能，`.trae/skills/<name>/SKILL.md` 必须存在、
   `name` 与目录一致、`description` 与真源逐字一致、正文包含指向 `aiops/` 的指针。
2. `.trae/skills/` 下不得存在 `aiops/skills/` 中没有对应真源的孤立包装。
3. 根 `AGENTS.md` 必须保留 generated 区块标记
   （BEGIN/END GENERATED、BEGIN LOCAL OVERRIDES/END LOCAL OVERRIDES），
   且 generated 区块中必须出现真源路径。

`AGENTS.md` 缺失时判失败；`.trae/` 缺失时只 WARN 跳过（换机器或新 clone 后尚未重建适配层）。
对外暴露 main(root: Path) -> int，其中 root 为 aiops 目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 模块级配置：所有规则与路径集中定义，不使用命令行参数
REPO_ROOT = Path(__file__).resolve().parents[2]
AIOPS_ROOT = REPO_ROOT / "aiops"

ADAPTER_DIRNAME = ".trae"
WRAPPER_SKILLS_SUBDIR = Path("skills")
TRUTH_SKILLS_SUBDIR = Path("skills")
SKILL_FILENAME = "SKILL.md"
AGENTS_FILENAME = "AGENTS.md"

# 判定"该包装属于本真源管辖"的标记：正文出现该片段即视为 aiops 薄包装
TRUTH_POINTER_MARKER = "aiops/skills/"

# AGENTS.md 的生成区块标记
GENERATED_BEGIN = "<!-- BEGIN GENERATED"
GENERATED_END = "<!-- END GENERATED -->"
OVERRIDES_BEGIN = "<!-- BEGIN LOCAL OVERRIDES -->"
OVERRIDES_END = "<!-- END LOCAL OVERRIDES -->"

# ZCode 适配层（可进版本库的 workspace 配置）
ZCODE_CONFIG_REL = Path(".zcode/config.json")
SESSION_HOOK_SCRIPT = Path("aiops/checks/hooks/session_start_context.py")
GUARD_HOOK_SCRIPT = Path("aiops/checks/hooks/guard_destructive.py")
# LOCAL OVERRIDES 中必须出现的状态指针要点（防"加固被悄悄删掉"）
AGENTS_STATE_POINTER_MARKER = "aiops/STATE.md"

# generated 区块中必须出现的真源路径
REQUIRED_TRUTH_REFERENCES: Tuple[str, ...] = (
    "aiops/CHARTER.md",
    "aiops/STATE.md",
    "aiops/EVIDENCE.yaml",
)


def _report(prefix: str, message: str) -> None:
    """按统一前缀打印一行中文校验信息。"""
    print(f"[{prefix}] {message}")


def _parse_front_matter(path: Path) -> Optional[Dict[str, str]]:
    """
    解析 Markdown 的 YAML front matter（只取 name / description 这类简单键值）。

    Returns
    -------
    fields : Optional[Dict[str, str]]
        成功时为键值映射；无 front matter 或读取失败时返回 None。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    fields: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return None


def _check_wrapper(
    truth_skill: Path, adapter_dir: Path
) -> Tuple[int, Optional[str]]:
    """
    检查单个技能薄包装与真源是否一致。

    Returns
    -------
    exit_code : int
        0 表示通过。
    skill_name : Optional[str]
        成功解析出的技能名。
    """
    name = truth_skill.parent.name
    truth_file = truth_skill
    wrapper_file = adapter_dir / WRAPPER_SKILLS_SUBDIR / name / SKILL_FILENAME

    if not wrapper_file.is_file():
        _report("FAIL", f"缺少薄包装: {wrapper_file}（真源为 {truth_file}）")
        return 1, name

    truth_fields = _parse_front_matter(truth_file)
    wrapper_fields = _parse_front_matter(wrapper_file)
    if truth_fields is None:
        _report("FAIL", f"真源缺少 front matter: {truth_file}")
        return 1, name
    if wrapper_fields is None:
        _report("FAIL", f"薄包装缺少 front matter: {wrapper_file}")
        return 1, name

    exit_code = 0
    if wrapper_fields.get("name") != name:
        _report(
            "FAIL",
            f"{name}: 薄包装 name 与目录不一致（{wrapper_fields.get('name')!r} != {name!r}）",
        )
        exit_code = 1

    truth_description = truth_fields.get("description", "")
    wrapper_description = wrapper_fields.get("description", "")
    if wrapper_description != truth_description:
        _report(
            "FAIL",
            f"{name}: description 与真源漂移。包装={wrapper_description!r} 真源={truth_description!r}",
        )
        exit_code = 1

    pointer = f"aiops/skills/{name}/SKILL.md"
    if pointer not in wrapper_file.read_text(encoding="utf-8"):
        _report("FAIL", f"{name}: 薄包装正文缺少指向真源的指针 {pointer}")
        exit_code = 1

    if exit_code == 0:
        _report("OK", f"{name}: 薄包装与真源一致")
    return exit_code, name


def _check_agents(repo_root: Path) -> int:
    """检查 AGENTS.md 的 generated 区块标记与真源引用。"""
    path = repo_root / AGENTS_FILENAME
    if not path.is_file():
        _report("FAIL", f"AGENTS.md 不存在: {path}")
        return 1
    text = path.read_text(encoding="utf-8")

    exit_code = 0
    for marker in (GENERATED_BEGIN, GENERATED_END, OVERRIDES_BEGIN, OVERRIDES_END):
        if marker not in text:
            _report("FAIL", f"AGENTS.md 缺少标记: {marker}")
            exit_code = 1

    block = _extract_generated_block(text)
    if block is None:
        _report("FAIL", "AGENTS.md 的 generated 区块无法定位（检查 BEGIN/END GENERATED 成对）")
        return 1

    for reference in REQUIRED_TRUTH_REFERENCES:
        if reference not in block:
            _report("FAIL", f"AGENTS.md generated 区块缺少真源引用: {reference}")
            exit_code = 1

    if exit_code == 0:
        _report("OK", f"AGENTS.md generated 区块标记与真源引用齐备（{len(REQUIRED_TRUTH_REFERENCES)} 项）")
    return exit_code


def _extract_generated_block(text: str) -> Optional[str]:
    """提取 AGENTS.md 中 BEGIN GENERATED 与 END GENERATED 之间的内容。"""
    start = text.find(GENERATED_BEGIN)
    end = text.find(GENERATED_END)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end]


def _check_zcode_adapter(repo_root: Path) -> int:
    """
    检查 ZCode 侧适配（对应 D-014 加固）：
    1. `.zcode/config.json` 存在且 hooks.enabled 为 true，
       SessionStart/PreToolUse(Bash) 分别挂上注入脚本与守卫脚本；
    2. 两个 hook 脚本真源文件存在；
    3. AGENTS.md 的 LOCAL OVERRIDES 保留状态指针（读 aiops/STATE.md）。

    Notes
    -----
    hook 命令里的解释器路径是本机路径，跨机器可能不同，故只校验脚本路径
    出现在命令/参数里，不校验解释器路径本身。
    D-017：hook 脚本参数必须是绝对路径（或以 ${ZCODE_PROJECT_DIR} 开头的写法被禁止）——
    变量在会话中途 cd 后被解析成 shell cwd 下的相对位置，曾造成"找不到脚本 →
    exit 2 → 拦掉包括 cd 在内的一切命令"的死锁。
    """
    exit_code = 0
    config_path = repo_root / ZCODE_CONFIG_REL
    if not config_path.is_file():
        _report("FAIL", f"缺少 ZCode 适配配置: {config_path}")
        return 1
    try:
        import json

        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _report("FAIL", f"ZCode 配置无法解析: {config_path}（{exc}）")
        return 1

    hooks = config.get("hooks") or {}
    if not hooks.get("enabled"):
        _report("FAIL", "ZCode 配置中 hooks.enabled 未开启，确定性注入与守卫不会运行")
        exit_code = 1

    raw = json.dumps(config, ensure_ascii=False)
    if "${ZCODE_PROJECT_DIR}" in raw or "${CLAUDE_PROJECT_DIR}" in raw:
        _report(
            "FAIL",
            "ZCode hooks 配置使用了 ${ZCODE_PROJECT_DIR}/${CLAUDE_PROJECT_DIR} 变量依赖"
            "（D-017 禁止：cd 后会解析到错误位置导致 hook 死锁），请改绝对路径",
        )
        exit_code = 1
    for label, script in (
        ("SessionStart 注入", SESSION_HOOK_SCRIPT.as_posix()),
        ("PreToolUse 守卫", GUARD_HOOK_SCRIPT.as_posix()),
    ):
        if script not in raw:
            _report("FAIL", f"ZCode hooks 未挂载 {label} 脚本: {script}")
            exit_code = 1
        elif not (repo_root / script).is_file():
            _report("FAIL", f"{label} 脚本真源不存在: {repo_root / script}")
            exit_code = 1
        else:
            _report("OK", f"ZCode {label}已挂载且真源存在")

    for event in ("SessionStart", "PreToolUse"):
        if event not in (hooks.get("events") or {}):
            _report("FAIL", f"ZCode hooks 缺少事件: {event}")
            exit_code = 1

    overrides_text = ""
    agents_path = repo_root / AGENTS_FILENAME
    if agents_path.is_file():
        text = agents_path.read_text(encoding="utf-8")
        ob = text.find(OVERRIDES_BEGIN)
        oe = text.find(OVERRIDES_END)
        if ob != -1 and oe != -1 and oe > ob:
            overrides_text = text[ob:oe]
    if AGENTS_STATE_POINTER_MARKER not in overrides_text:
        _report(
            "FAIL",
            f"AGENTS.md LOCAL OVERRIDES 缺少状态指针（{AGENTS_STATE_POINTER_MARKER}），"
            "非 ZCode/Trae 工具将失去读取状态的确定性提示",
        )
        exit_code = 1
    else:
        _report("OK", "AGENTS.md LOCAL OVERRIDES 状态指针在位")
    return exit_code


def main(root: Path) -> int:
    """
    执行 adapter 漂移检查。

    Parameters
    ----------
    root : Path
        aiops 目录路径；仓库根由 root.parent 推出。

    Returns
    -------
    exit_code : int
        0 表示通过，非 0 表示存在失败项。
    """
    root = Path(root)
    repo_root = root.parent
    adapter_dir = repo_root / ADAPTER_DIRNAME
    truth_skills_dir = root / TRUTH_SKILLS_SUBDIR
    print(f"== check_adapter_drift: {adapter_dir} ==")

    exit_code = _check_agents(repo_root)

    if not truth_skills_dir.is_dir():
        _report("FAIL", f"真源技能目录不存在: {truth_skills_dir}")
        return 1

    truth_skills = sorted(truth_skills_dir.glob(f"*/{SKILL_FILENAME}"))
    if not truth_skills:
        _report("WARN", "真源技能目录为空，跳过包装检查")
        return exit_code

    if not adapter_dir.is_dir():
        _report("WARN", f"未发现本地适配层 {adapter_dir}，跳过包装检查（换机器后需重建）")
        _report("WARN", "汇总: adapter 漂移检查跳过包装部分")
        return exit_code

    truth_names = {path.parent.name for path in truth_skills}
    for truth_skill in truth_skills:
        code, _ = _check_wrapper(truth_skill, adapter_dir)
        exit_code |= code

    # 只检查"声称指向 aiops 真源"的包装是否都有对应真源；
    # .trae/skills/ 下还有大量与本真源无关的技能，不应被当成孤立包装。
    exit_code |= _check_orphan_wrappers(adapter_dir, truth_names)

    if exit_code == 0:
        _report("OK", "汇总: adapter 漂移检查全部通过")
    else:
        _report("FAIL", "汇总: adapter 漂移检查存在失败项")
    return exit_code


def _check_orphan_wrappers(adapter_dir: Path, truth_names: set) -> int:
    """
    检查声称指向 aiops 真源的薄包装是否都有对应真源技能。

    Notes
    -----
    仅当包装正文出现 `aiops/skills/` 指针时，才认为它属于本真源管辖范围；
    其余 `.trae/skills/` 下的技能与本真源无关，不参与检查。
    """
    wrapper_root = adapter_dir / WRAPPER_SKILLS_SUBDIR
    if not wrapper_root.is_dir():
        return 0
    exit_code = 0
    for wrapper in sorted(wrapper_root.glob(f"*/{SKILL_FILENAME}")):
        try:
            body = wrapper.read_text(encoding="utf-8")
        except OSError:
            continue
        if TRUTH_POINTER_MARKER not in body:
            continue
        if wrapper.parent.name not in truth_names:
            _report("FAIL", f"存在无真源对应的 aiops 薄包装: {wrapper}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(AIOPS_ROOT))
