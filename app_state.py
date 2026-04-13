"""
全局应用状态——在 bot 和 admin server 之间共享 SessionManager 实例。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from session_manager import SessionManager

_sessions: "SessionManager | None" = None


def set_sessions(sm: "SessionManager") -> None:
    global _sessions
    _sessions = sm


def get_sessions() -> "SessionManager | None":
    return _sessions
