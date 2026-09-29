"""
安装 AI-REOS 的 git hook（把版本化副本复制到 .git/hooks/）。

用途
----
`.git/hooks/` 不进版本控制，换机器或重新 clone 后需要重新安装。本脚本把
`aiops/checks/hooks/` 下的 hook 复制到当前仓库的 `.git/hooks/` 并赋予可执行权限。

设计说明
--------
- 不使用命令行参数解析；要安装哪些 hook 由模块级常量集中定义。
- 采用**复制**而非符号链接：Windows 上创建符号链接通常需要额外权限。
- 已存在同名 hook 且内容不同时，先备份为 `<name>.bak` 再覆盖，避免静默破坏他人配置。
- 对外暴露 main(repo_root: Path) -> int，便于测试传入临时目录。
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import List, Tuple

# 模块级配置：仓库根由脚本位置向上三级推出
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_SOURCE_DIR = Path(__file__).resolve().parent / "hooks"

# 要安装的 hook 文件名（版本化副本 -> .git/hooks/ 下的名字）
HOOK_NAMES: Tuple[str, ...] = ("pre-commit",)


def _report(prefix: str, message: str) -> None:
    """按统一前缀打印一行中文信息。"""
    print(f"[{prefix}] {message}")


def _make_executable(path: Path) -> None:
    """尽量赋予可执行权限（Windows 上是无害的空操作）。"""
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def install_hook(repo_root: Path, hook_name: str) -> int:
    """
    安装单个 hook。

    Returns
    -------
    exit_code : int
        0 表示成功或已是最新，非 0 表示失败。
    """
    source = HOOKS_SOURCE_DIR / hook_name
    if not source.is_file():
        _report("FAIL", f"版本化 hook 不存在: {source}")
        return 1

    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        _report("FAIL", f"未找到 git hooks 目录: {hooks_dir}")
        return 1

    target = hooks_dir / hook_name
    source_text = source.read_text(encoding="utf-8")
    if target.is_file():
        if target.read_text(encoding="utf-8", errors="replace") == source_text:
            _make_executable(target)
            _report("OK", f"{hook_name} 已是最新，无需改动")
            return 0
        backup = target.with_name(f"{hook_name}.bak")
        shutil.copy2(target, backup)
        _report("WARN", f"已存在不同的 {hook_name}，已备份为 {backup.name}")

    shutil.copy2(source, target)
    _make_executable(target)
    _report("OK", f"已安装 {hook_name} -> {target}")
    return 0


def main(repo_root: Path) -> int:
    """
    安装全部登记在 HOOK_NAMES 中的 hook。

    Parameters
    ----------
    repo_root : Path
        仓库根目录（测试中可传入临时目录）。

    Returns
    -------
    exit_code : int
        0 表示全部成功，非 0 表示存在失败项。
    """
    repo_root = Path(repo_root)
    print(f"== install_hooks: {repo_root} ==")
    exit_code = 0
    for hook_name in HOOK_NAMES:
        exit_code |= install_hook(repo_root, hook_name)
    if exit_code == 0:
        _report("OK", "汇总: hook 安装完成")
    else:
        _report("FAIL", "汇总: hook 安装存在失败项")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(REPO_ROOT))
