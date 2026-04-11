"""
Codex / 本地模型客户端
─────────────────────
使用 OpenAI 兼容接口 + Function Calling 工具循环。
模型可以主动调用：
  - write_file      创建/覆盖文件
  - read_file       读取文件内容
  - run_shell       执行 shell 命令
  - list_directory  列出目录
循环执行直到模型不再调用工具，然后返回最终文本。
"""
import json
import os
from openai import OpenAI
from session_manager import SessionManager
from shell_session import get_shell

# ── 工具定义 ──────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file with the given content. "
                "Use this to create source files, config files, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "File path (relative to cwd or absolute)"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read and return the content of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Execute a shell command in the current working directory. "
                "Use this for mkdir, git init, npm install, pip install, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path. Omit or pass '.' to use current working directory.",
                    },
                },
                "required": [],
            },
        },
    },
]

MAX_TOOL_ITER = 20   # 防止无限循环


class CodexClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        if not base_url:
            raise RuntimeError("CODEX_BASE_URL 未配置，请在 .env 中填写本地模型地址")
        if not api_key:
            raise RuntimeError("CODEX_API_KEY 未配置")
        if not model:
            raise RuntimeError("CODEX_MODEL 未配置")
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model  = model

    # ── 公开接口 ──────────────────────────────────────────────────────────────
    def chat(self, session_manager: SessionManager, session_id: str, user_text: str) -> str:
        ctx   = session_manager.get(session_id)
        sess  = ctx.session
        shell = get_shell(session_id)

        # 1. 存入用户消息
        ctx.add_user(user_text)

        # 2. 构造初始 messages（system + 完整历史）
        messages: list[dict] = []
        if sess.system:
            messages.append({"role": "system", "content": sess.system})
        messages.extend(ctx.get_history())

        # 3. 工具调用循环
        tool_summary: list[str] = []
        try:
            for _ in range(MAX_TOOL_ITER):
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
                choice = response.choices[0]
                msg    = choice.message

                # 没有工具调用 → 取最终文本
                if not msg.tool_calls:
                    reply = (msg.content or "").strip()
                    break

                # 有工具调用 → 执行并收集结果
                # 把 assistant 消息（含 tool_calls）加入本轮 messages
                messages.append(msg)

                for tc in msg.tool_calls:
                    try:
                        args   = json.loads(tc.function.arguments)
                        result = self._dispatch(tc.function.name, args, shell)
                    except Exception as e:
                        result = f"ERROR: {e}"

                    short = result[:120].replace("\n", " ")
                    tool_summary.append(f"  [{tc.function.name}] {short}")

                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc.id,
                        "content":      result,
                    })
            else:
                reply = f"（已执行 {MAX_TOOL_ITER} 次工具调用，强制结束）"

        except Exception:
            # 回滚用户消息
            if sess.history and sess.history[-1]["role"] == "user":
                sess.history.pop()
            raise

        # 4. 拼接工具摘要 + 最终回复
        if tool_summary:
            header = "✅ 已执行以下操作：\n" + "\n".join(tool_summary)
            full_reply = header + ("\n\n" + reply if reply else "")
        else:
            full_reply = reply or "（模型未返回内容）"

        # 5. 存入助手回复（只存最终文本，不存中间工具消息）
        ctx.add_assistant(full_reply)
        return full_reply

    # ── 工具分发 ──────────────────────────────────────────────────────────────
    def _dispatch(self, name: str, args: dict, shell) -> str:
        if name == "write_file":
            return self._write_file(args["path"], args["content"], shell.cwd)
        if name == "read_file":
            return self._read_file(args["path"], shell.cwd)
        if name == "run_shell":
            return shell.execute(args["command"])
        if name == "list_directory":
            return self._list_dir(args.get("path", "."), shell.cwd)
        return f"未知工具：{name}"

    # ── 工具实现 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _resolve(path: str, cwd: str) -> str:
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        return os.path.normpath(path)

    def _write_file(self, path: str, content: str, cwd: str) -> str:
        full = self._resolve(path, cwd)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入：{full}（{len(content)} 字符）"

    def _read_file(self, path: str, cwd: str) -> str:
        full = self._resolve(path, cwd)
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                content = f.read()
            # 限制返回长度，避免撑爆 context
            if len(content) > 8000:
                content = content[:8000] + "\n... [截断，仅显示前 8000 字符]"
            return content
        except FileNotFoundError:
            return f"文件不存在：{full}"
        except Exception as e:
            return f"读取失败：{e}"

    def _list_dir(self, path: str, cwd: str) -> str:
        full = self._resolve(path, cwd)
        try:
            entries = os.listdir(full)
            lines   = []
            for e in sorted(entries):
                tag = "/" if os.path.isdir(os.path.join(full, e)) else ""
                lines.append(f"  {e}{tag}")
            return f"{full}\n" + ("\n".join(lines) if lines else "  (空目录)")
        except Exception as e:
            return f"列目录失败：{e}"
