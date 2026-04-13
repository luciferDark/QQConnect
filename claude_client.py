"""
Claude Code CLI 封装：通过 subprocess 调用本地 claude 命令
使用 Claude.ai 订阅，无需 API Key
支持注入全局 Skill 内容到 prompt
"""
import locale
import subprocess
import shutil
from session_manager import SessionManager
from skill_loader import load_skill
from data_store import get_store

def _decode(b: bytes) -> str:
    """优先 UTF-8，失败则退回系统编码（Windows GBK 等）。"""
    if not b:
        return ""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode(locale.getpreferredencoding(False), errors="replace")


class ClaudeClient:
    def __init__(self, api_key: str = "", model: str = ""):
        claude_path = shutil.which("claude")
        if not claude_path:
            raise RuntimeError(
                "找不到 claude 命令，请确认 Claude Code 已安装。\n"
                "安装方法：npm install -g @anthropic-ai/claude-code"
            )
        self._cmd = claude_path

    def chat(
        self,
        session_manager: SessionManager,
        session_id: str,
        user_text: str,
        skill_name: str = "",
    ) -> str:
        """
        发送消息并返回回复文本。
        skill_name: 若非空，则将对应 skill 内容注入 prompt 头部。
        """
        ctx = session_manager.get(session_id)

        # 1. 存入用户消息
        ctx.add_user(user_text)

        sess    = ctx.session
        system  = sess.system
        history = ctx.get_history()

        # 2. 如果有指定 skill，加载并注入
        skill_ctx = ""
        if skill_name:
            skill_ctx = load_skill(skill_name)

        # 3. 构造 prompt
        prompt = self._build_prompt(system, history, skill_ctx)

        # 4. 调用 claude CLI
        try:
            result = subprocess.run(
                [self._cmd, "--model", sess.model, "-p", prompt],
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            if sess.history and sess.history[-1]["role"] == "user":
                sess.history.pop()
            raise RuntimeError("Claude 响应超时（120秒），请重试")
        except FileNotFoundError:
            raise RuntimeError("claude 命令不可用，请检查安装")

        if result.returncode != 0:
            err = _decode(result.stderr).strip() or "未知错误"
            raise RuntimeError(f"Claude CLI 错误：{err[:300]}")

        reply = _decode(result.stdout).strip()
        if not reply:
            reply = "（Claude 未返回内容，请重试）"

        # 5. 存入助手回复
        ctx.add_assistant(reply)

        # 6. Token 估算（CLI 无返回，按字符数估算：中文 ~2 chars/tok，英文 ~4 chars/tok）
        in_tok  = sum(len(m["content"]) for m in history) // 3
        out_tok = len(reply) // 3
        get_store().record_tokens(session_id, ctx.session.name, "claude", in_tok, out_tok)

        # 7. 持久化
        session_manager.save(session_id)
        return reply

    def _build_prompt(self, system: str, history: list, skill_ctx: str = "") -> str:
        parts = [system]

        if skill_ctx:
            parts += ["", "## 当前激活的 Skill 指令（请严格遵照执行）", "", skill_ctx]

        if len(history) == 1:
            parts += ["", history[0]["content"]]
            return "\n".join(parts)

        parts += ["", "以下是之前的对话记录：", ""]
        for msg in history[:-1]:
            prefix = "用户" if msg["role"] == "user" else "助手"
            parts.append(f"{prefix}：{msg['content']}")
            parts.append("")

        parts.append(f"用户的最新问题：{history[-1]['content']}")
        return "\n".join(parts)
