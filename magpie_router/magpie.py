"""magpie CLI 的定位与安全调用。"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MagpieError(RuntimeError):
    """magpie 不可用、命令失败或返回无法识别的内容。"""


@dataclass(frozen=True)
class CommandResult:
    """保留一次外部命令的输出和退出状态。"""

    stdout: str
    stderr: str
    returncode: int


def find_magpie() -> str:
    """返回显式配置、PATH 或标准安装位置中的 magpie CLI。"""
    configured = os.environ.get("MAGPIE_CLI")
    if configured:
        if not Path(configured).is_file():
            raise MagpieError(f"MAGPIE_CLI 不是可执行文件：{configured}")
        return configured

    discovered = shutil.which("magpie.com") or shutil.which("magpie")
    if discovered:
        return discovered

    candidates = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        if local:
            candidates.append(Path(local) / "magpie" / "magpie.exe")
        if program_files:
            candidates.append(Path(program_files) / "magpie" / "magpie.com")
    else:
        candidates.append(Path("/Applications/magpie.app/Contents/MacOS/magpie"))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise MagpieError("找不到 magpie CLI，请启动应用或设置 MAGPIE_CLI。")


def run_magpie(arguments: list[str], timeout: int = 60) -> CommandResult:
    """以参数数组执行 magpie，不经过 shell，也不接受来自计划的命令文本。"""
    command = [find_magpie(), *arguments]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MagpieError(f"无法执行 magpie {' '.join(arguments)}：{error}") from error
    return CommandResult(completed.stdout, completed.stderr, completed.returncode)


def checked_magpie(arguments: list[str], timeout: int = 60) -> str:
    """执行只读或已批准命令，失败时保留输出供诊断。"""
    result = run_magpie(arguments, timeout)
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise MagpieError(f"magpie {' '.join(arguments)} 失败：\n{detail.strip()}")
    return result.stdout
