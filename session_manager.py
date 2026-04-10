"""
多会话管理器
─────────────
每个 QQ 用户（user_key）拥有：
  - 多个命名的 ChatSession（最多 MAX_SESSIONS 个）
  - 当前激活的 session 指针
  - 独立的 shell 实例

ChatSession 包含：
  - 名称、历史、模型、系统提示词、创建时间
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── 可用模型表 ────────────────────────────────────────────────────────────────
MODELS: dict[str, str] = {
    # 别名       → 完整模型 ID
    "opus":      "claude-opus-4-6",
    "gpt":      "gpt-4-1",
    "sonnet":    "claude-sonnet-4-6",
    "haiku":     "claude-haiku-4-5",
    # 也接受完整名
    "claude-opus-4-6":    "claude-opus-4-6",
    "claude-sonnet-4-6":  "claude-sonnet-4-6",
    "claude-haiku-4-5":   "claude-haiku-4-5",
    "gpt-4-1":    "gpt-4-1",
}
DEFAULT_MODEL   = "gpt-4-1"
DEFAULT_SYSTEM  = "你是一个有帮助的AI助手。回答要清晰、准确、简洁。支持中英文对话。"
MAX_SESSIONS    = 10     # 每用户最多会话数
MAX_TURNS       = 20     # 默认每会话最多保留轮数


# ── ChatSession ───────────────────────────────────────────────────────────────
@dataclass
class ChatSession:
    name:       str
    model:      str      = DEFAULT_MODEL
    system:     str      = DEFAULT_SYSTEM
    history:    list     = field(default_factory=list)
    created_at: float    = field(default_factory=time.time)
    updated_at: float    = field(default_factory=time.time)

    # ── 历史操作 ──────────────────────────────────────────────────────────────
    def add_user(self, content: str, max_turns: int = MAX_TURNS):
        self.history.append({"role": "user", "content": content})
        self.updated_at = time.time()
        self._trim(max_turns)

    def add_assistant(self, content: str):
        self.history.append({"role": "assistant", "content": content})
        self.updated_at = time.time()

    def clear(self):
        self.history.clear()
        self.updated_at = time.time()

    def trim_to(self, n_turns: int):
        """只保留最近 n_turns 轮（user+assistant 各算半轮）"""
        self._trim(n_turns)

    def _trim(self, max_turns: int):
        limit = max_turns * 2
        if len(self.history) > limit:
            msgs = self.history[-limit:]
            while msgs and msgs[0]["role"] != "user":
                msgs = msgs[1:]
            self.history = msgs

    # ── 统计 ──────────────────────────────────────────────────────────────────
    @property
    def turn_count(self) -> int:
        return sum(1 for m in self.history if m["role"] == "user")

    @property
    def model_short(self) -> str:
        for alias, full in MODELS.items():
            if full == self.model and len(alias) <= 6:
                return alias
        return self.model.split("-")[-1]  # fallback

    def info(self) -> str:
        age = datetime.fromtimestamp(self.created_at).strftime("%m-%d %H:%M")
        return (
            f"会话：{self.name}\n"
            f"模型：{self.model}\n"
            f"对话轮数：{self.turn_count}\n"
            f"消息条数：{len(self.history)}\n"
            f"系统提示：{'(默认)' if self.system == DEFAULT_SYSTEM else self.system[:60]}\n"
            f"创建时间：{age}"
        )


# ── UserContext ───────────────────────────────────────────────────────────────
class UserContext:
    """
    单个 QQ 用户的全部状态：
      - 多个命名会话
      - 当前激活会话
      - shell 模式标志
    """
    def __init__(self, max_turns: int = MAX_TURNS):
        self._max_turns = max_turns
        self._sessions:  dict[str, ChatSession] = {}
        self._active:    str = "default"
        self.shell_mode: bool = False
        self._new_session("default")

    # ── 激活会话 ──────────────────────────────────────────────────────────────
    @property
    def session(self) -> ChatSession:
        return self._sessions[self._active]

    @property
    def active_name(self) -> str:
        return self._active

    # ── 会话管理 ──────────────────────────────────────────────────────────────
    def new_session(self, name: str | None = None) -> tuple[ChatSession, str]:
        """新建会话，返回 (session, 消息)"""
        if len(self._sessions) >= MAX_SESSIONS:
            return self.session, f"已达上限 {MAX_SESSIONS} 个会话，请先删除旧会话"
        name = name or self._auto_name()
        if name in self._sessions:
            return self.session, f"会话 '{name}' 已存在"
        sess = self._new_session(name)
        self._active = name
        return sess, f"已新建并切换到会话：{name}"

    def switch_session(self, name: str) -> tuple[ChatSession | None, str]:
        if name not in self._sessions:
            names = ", ".join(self._sessions)
            return None, f"会话 '{name}' 不存在\n可用：{names}"
        self._active = name
        return self._sessions[name], f"已切换到：{name}（{self.session.model_short}，{self.session.turn_count} 轮）"

    def delete_session(self, name: str) -> str:
        if name not in self._sessions:
            return f"会话 '{name}' 不存在"
        if len(self._sessions) == 1:
            return "无法删除唯一的会话"
        del self._sessions[name]
        if self._active == name:
            self._active = next(iter(self._sessions))
            return f"已删除 '{name}'，自动切换到：{self._active}"
        return f"已删除会话：{name}"

    def rename_session(self, new_name: str) -> str:
        if new_name in self._sessions:
            return f"名称 '{new_name}' 已被占用"
        old = self._active
        sess = self._sessions.pop(old)
        sess.name = new_name
        self._sessions[new_name] = sess
        self._active = new_name
        return f"已将 '{old}' 重命名为 '{new_name}'"

    def list_sessions(self) -> str:
        lines = []
        for name, s in self._sessions.items():
            marker = "▶" if name == self._active else " "
            lines.append(
                f"{marker} {name:<16} {s.model_short:<8} {s.turn_count} 轮"
            )
        return "会话列表（▶=当前）：\n" + "\n".join(lines)

    # ── 快捷代理到 active session ──────────────────────────────────────────────
    def add_user(self, content: str):
        self.session.add_user(content, self._max_turns)

    def add_assistant(self, content: str):
        self.session.add_assistant(content)

    def get_history(self) -> list:
        return list(self.session.history)

    def clear(self):
        self.session.clear()

    # ── 内部 ──────────────────────────────────────────────────────────────────
    def _new_session(self, name: str) -> ChatSession:
        sess = ChatSession(name=name)
        self._sessions[name] = sess
        return sess

    def _auto_name(self) -> str:
        i = len(self._sessions) + 1
        while f"session-{i}" in self._sessions:
            i += 1
        return f"session-{i}"


# ── 全局 SessionManager ───────────────────────────────────────────────────────
class SessionManager:
    """
    全局入口：user_key → UserContext
    user_key 通常是 QQ 消息的 session_id（c2c_xxx / group_xxx_yyy）
    """
    def __init__(self, max_turns: int = MAX_TURNS):
        self._max_turns = max_turns
        self._users: dict[str, UserContext] = {}
        self._last_active: dict[str, float] = {}

    def get(self, user_key: str) -> UserContext:
        if user_key not in self._users:
            self._users[user_key] = UserContext(self._max_turns)
        self._last_active[user_key] = time.time()
        return self._users[user_key]

    def total_users(self) -> int:
        return len(self._users)

    def cleanup_inactive(self, max_age: int = 3600) -> int:
        now = time.time()
        stale = [k for k, t in self._last_active.items() if now - t > max_age]
        for k in stale:
            self._users.pop(k, None)
            self._last_active.pop(k, None)
        return len(stale)
