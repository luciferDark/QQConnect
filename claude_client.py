"""
Claude Code CLI 封装：通过 subprocess 调用本地 claude 命令
使用 Claude.ai 订阅，无需 API Key
"""
import subprocess
import shutil
from session_manager import SessionManager


class ClaudeClient:
    def __init__(self, api_key: str = "", model: str = ""):
        # api_key / model 保留参数签名兼容性，CLI 模式下忽略
        claude_path = shutil.which("claude")
        if not claude_path:
            raise RuntimeError(
                "找不到 claude 命令，请确认 Claude Code 已安装。\n"
                "安装方法：npm install -g @anthropic-ai/claude-code"
            )
        self._cmd = claude_path

    def chat(self, session_manager: SessionManager, session_id: str, user_text: str) -> str:
        """
        发送消息并返回回复文本。
        使用当前会话的 model/system/history，实现多轮对话。
        """
        ctx = session_manager.get(session_id)

        # 1. 存入用户消息
        ctx.add_user(user_text)

        # 2. 取当前会话的 model / system / history
        sess    = ctx.session
        system  = sess.system
        history = ctx.get_history()

        # 3. 构造带历史的 prompt
        prompt = self._build_prompt(system, history)

        # 4. 调用 claude CLI（-p 是 print 模式，非交互式）
        try:
            result = subprocess.run(
                [self._cmd, "-p", prompt],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            # 撤销刚加的消息
            if sess.history and sess.history[-1]["role"] == "user":
                sess.history.pop()
            raise RuntimeError("Claude 响应超时（120秒），请重试")
        except FileNotFoundError:
            raise RuntimeError("claude 命令不可用，请检查安装")

        if result.returncode != 0:
            err = (result.stderr or "未知错误").strip()
            raise RuntimeError(f"Claude CLI 错误：{err[:300]}")

        reply = result.stdout.strip()
        if not reply:
            reply = "（Claude 未返回内容，请重试）"

        # 5. 存入助手回复
        ctx.add_assistant(reply)
        return reply

    def _build_prompt(self, system: str, history: list) -> str:
        """
        将系统提示 + 对话历史格式化为单个 prompt 字符串。
        """
        if len(history) == 1:
            return f"{system}\n\n{history[0]['content']}"

        lines = [system, "", "以下是之前的对话记录：", ""]
        for msg in history[:-1]:
            prefix = "用户" if msg["role"] == "user" else "助手"
            lines.append(f"{prefix}：{msg['content']}")
            lines.append("")

        lines.append(f"用户的最新问题：{history[-1]['content']}")
        return "\n".join(lines)
