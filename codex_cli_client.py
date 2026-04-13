"""
Codex CLI 模式
──────────────
通过 `openai api chat.completions.create -g role content ...` 调用，
与 claude_client.py 的 subprocess 模式对称。
无 Function Calling，返回纯文本。
"""
import json
import locale
import os
import shutil
import subprocess
from session_manager import SessionManager
from data_store import get_store

def _decode(b: bytes) -> str:
    if not b:
        return ""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode(locale.getpreferredencoding(False), errors="replace")


class CodexCliClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        cli = shutil.which("openai")
        if not cli:
            raise RuntimeError(
                "找不到 openai 命令，请安装：pip install openai"
            )
        if not base_url:
            raise RuntimeError("CODEX_BASE_URL 未配置")
        if not api_key:
            raise RuntimeError("CODEX_API_KEY 未配置")
        if not model:
            raise RuntimeError("CODEX_MODEL 未配置")

        self._cmd   = cli
        self._model = model
        # 子进程环境：注入 base_url 和 api_key，并强制 UTF-8 输出
        self._env = {
            **os.environ,
            "OPENAI_BASE_URL":    base_url,
            "OPENAI_API_KEY":     api_key,
            "PYTHONIOENCODING":   "utf-8",   # 强制 openai CLI 以 UTF-8 写 stdout
            "PYTHONUTF8":         "1",        # Python 3.7+ UTF-8 模式
        }

    def chat(self, session_manager: SessionManager, session_id: str, user_text: str) -> str:
        ctx  = session_manager.get(session_id)
        sess = ctx.session

        # 1. 存入用户消息
        ctx.add_user(user_text)
        history = ctx.get_history()

        # 2. 构造命令，使用会话级模型，回退到客户端默认
        model = sess.codex_model or self._model
        cmd = [self._cmd, "api", "chat.completions.create", "-m", model]

        if sess.system:
            cmd += ["-g", "system", sess.system]

        for msg in history:
            cmd += ["-g", msg["role"], msg["content"]]

        # 3. 执行
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=120,
                env=self._env,
            )
        except subprocess.TimeoutExpired:
            if sess.history and sess.history[-1]["role"] == "user":
                sess.history.pop()
            raise RuntimeError("openai CLI 响应超时（120秒）")
        except FileNotFoundError:
            raise RuntimeError("openai 命令不可用，请检查安装")

        if result.returncode != 0:
            err = (_decode(result.stderr) or _decode(result.stdout)).strip() or "未知错误"
            if sess.history and sess.history[-1]["role"] == "user":
                sess.history.pop()
            raise RuntimeError(f"openai CLI 错误：{err[:300]}")

        # 4. 解析 JSON 输出（优先 UTF-8，失败回退系统编码）
        raw_str = _decode(result.stdout)
        reply = self._parse_output(raw_str)
        if not reply:
            reply = "（CLI 未返回内容，请重试）"

        # 5. 存入助手回复
        ctx.add_assistant(reply)

        # 6. Token 统计（从 JSON usage 字段读取）
        try:
            raw_data = json.loads(raw_str)
            u = raw_data.get("usage", {})
            get_store().record_tokens(
                session_id, sess.name, "codex-cli",
                u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
            )
        except Exception:
            pass

        # 7. 持久化
        session_manager.save(session_id)
        return reply

    @staticmethod
    def _parse_output(raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return ""
        try:
            data = json.loads(raw)
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            # fallback：直接返回原始输出
            return raw
