"""集中管理仓库内可写数据与策略文件的默认位置。"""

from __future__ import annotations

from pathlib import Path


def repository_root() -> Path:
    """返回项目根目录，允许从任意当前工作目录启动命令。"""
    return next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").is_file()
    )


def config_dir() -> Path:
    """返回策略配置目录。"""
    return repository_root() / "config"


def state_dir() -> Path:
    """返回本地状态目录。"""
    return repository_root() / "state"


def database_path(configured: str | None = None) -> Path:
    """返回数据库路径；提供环境变量值时使用该显式路径。"""
    return Path(configured) if configured else state_dir() / "db" / "magpie-router.db"


def snapshot_dir() -> Path:
    """返回批量应用回滚快照目录。"""
    return state_dir() / "snapshots"


def evidence_dir() -> Path:
    """返回只读观测与验证证据目录。"""
    return state_dir() / "evidence"


def work_dir() -> Path:
    """返回计划和工作记录目录。"""
    return state_dir() / "work"
