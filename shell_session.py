"""
Shell 会话管理：跟踪当前目录，执行命令，返回输出
"""
import os
import locale
import subprocess

# Windows 中文系统 shell 输出编码（GBK/CP936），非中文系统退回 UTF-8
_SHELL_ENCODING = locale.getpreferredencoding(False) or "utf-8"

# 每个 QQ 会话独立的 shell 状态
_sessions: dict[str, "ShellSession"] = {}


def get_shell(session_id: str) -> "ShellSession":
    if session_id not in _sessions:
        _sessions[session_id] = ShellSession()
    return _sessions[session_id]


class ShellSession:
    def __init__(self):
        self.cwd = os.getcwd()

    def execute(self, command: str) -> str:
        """执行 shell 命令，返回输出字符串"""
        command = command.strip()
        if not command:
            return ""

        # cd 需要特殊处理（子进程的 cd 不影响父进程）
        if command == "cd" or command.startswith("cd ") or command.startswith("cd\t"):
            return self._cd(command)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.cwd,
                capture_output=True,
                timeout=30,
            )
            stdout = result.stdout.decode(_SHELL_ENCODING, errors="replace")
            stderr = result.stderr.decode(_SHELL_ENCODING, errors="replace")
            output = (stdout + stderr).strip()
            return output if output else "(命令执行完毕，无输出)"
        except subprocess.TimeoutExpired:
            return "⚠️ 命令超时（30秒）"
        except Exception as e:
            return f"⚠️ 执行失败：{e}"

    def _cd(self, command: str) -> str:
        parts = command.split(None, 1)
        target = os.path.expanduser("~") if len(parts) == 1 else parts[1].strip()
        target = os.path.expandvars(os.path.expanduser(target))

        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)
        target = os.path.normpath(target)

        if os.path.isdir(target):
            self.cwd = target
            return f"✅ 已切换到：{self.cwd}"
        else:
            return f"❌ 目录不存在：{target}"
