"""
DataStore — 会话持久化 + Token 用量追踪
────────────────────────────────────────
• sessions.json  每个用户的会话历史（含 history）
• tokens.json    按日期 / backend / session 的 token 用量
"""
from __future__ import annotations
import json
import os
import time
from datetime import datetime
from threading import Lock

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
TOKENS_FILE   = os.path.join(DATA_DIR, "tokens.json")


# ─────────────────────────────────────────────────────────────────────────────
#  DataStore
# ─────────────────────────────────────────────────────────────────────────────

class DataStore:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._lock = Lock()
        self._tokens: dict = self._load_json(TOKENS_FILE, {
            "daily": {},
            "total": {},
            "sessions": {},
        })

    # ── Session 持久化 ────────────────────────────────────────────────────────

    def save_user(self, user_key: str, user_ctx) -> None:
        """把单个 UserContext 序列化写入 sessions.json"""
        with self._lock:
            all_data = self._load_json(SESSIONS_FILE, {})
            all_data[user_key] = self._serialize_user(user_ctx)
            self._write_json(SESSIONS_FILE, all_data)

    def load_all_sessions(self) -> dict:
        """读取 sessions.json，返回原始 dict（供 SessionManager 恢复）"""
        return self._load_json(SESSIONS_FILE, {})

    def delete_user_session(self, user_key: str, session_name: str) -> bool:
        with self._lock:
            all_data = self._load_json(SESSIONS_FILE, {})
            if user_key not in all_data:
                return False
            sessions = all_data[user_key].get("sessions", {})
            if session_name not in sessions:
                return False
            del sessions[session_name]
            if not sessions:
                del all_data[user_key]
            else:
                all_data[user_key]["sessions"] = sessions
            self._write_json(SESSIONS_FILE, all_data)
            return True

    def get_all_users(self) -> dict:
        return self._load_json(SESSIONS_FILE, {})

    # ── Token 追踪 ────────────────────────────────────────────────────────────

    def record_tokens(
        self,
        user_key: str,
        session_name: str,
        backend: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        with self._lock:
            today = datetime.now().strftime("%Y-%m-%d")
            d = self._tokens

            # daily
            d["daily"].setdefault(today, {})
            d["daily"][today].setdefault(backend, {"input": 0, "output": 0})
            d["daily"][today][backend]["input"]  += input_tokens
            d["daily"][today][backend]["output"] += output_tokens

            # total
            d["total"].setdefault(backend, {"input": 0, "output": 0})
            d["total"][backend]["input"]  += input_tokens
            d["total"][backend]["output"] += output_tokens

            # per session
            key = f"{user_key}:{session_name}"
            d["sessions"].setdefault(key, {"input": 0, "output": 0, "backend": backend})
            d["sessions"][key]["input"]  += input_tokens
            d["sessions"][key]["output"] += output_tokens

            self._write_json(TOKENS_FILE, d)

    def get_token_stats(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._tokens))  # deep copy

    # ── 序列化 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _serialize_user(user_ctx) -> dict:
        sessions = {}
        for name, sess in user_ctx._sessions.items():
            sessions[name] = {
                "name":        sess.name,
                "backend":     sess.backend,
                "model":       sess.model,
                "codex_model": sess.codex_model,
                "system":      sess.system,
                "history":     list(sess.history),
                "created_at":  sess.created_at,
                "updated_at":  sess.updated_at,
            }
        return {
            "active": user_ctx._active,
            "shell_mode": user_ctx.shell_mode,
            "sessions": sessions,
        }

    # ── 文件 IO ───────────────────────────────────────────────────────────────

    @staticmethod
    def _load_json(path: str, default) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: str, data: dict) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


# ── 单例 ──────────────────────────────────────────────────────────────────────

_instance: DataStore | None = None

def get_store() -> DataStore:
    global _instance
    if _instance is None:
        _instance = DataStore()
    return _instance
