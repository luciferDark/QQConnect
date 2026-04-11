"""
QQ Terminal Bridge TUI
─────────────────────
左侧：QQ 对话记录
右侧：Shell 命令输出
底部：当前会话 · 模型 · 轮数 · 目录 · 模式

消息路由规则：
  !cmd              → 直接执行 shell 命令（e.g. !ls, !mkdir foo）
  cd PATH           → 切换目录（任意模式均生效）

  /help             → 帮助列表
  /shell            → 当前会话切换为 shell 模式
  /chat             → 当前会话切换为 chat 模式（默认）
  /mode             → 查看当前模式
  /clear            → 清空当前会话历史
  /cwd              → 查看当前目录

  会话管理：
  /new [名称]       → 新建会话（可选名称）
  /sessions         → 列出所有会话
  /switch <名称>    → 切换到指定会话
  /rename <新名称>  → 重命名当前会话
  /del <名称>       → 删除指定会话
  /ctx              → 查看当前会话详情

  模型管理：
  /models           → 列出可用模型
  /model [别名]     → 查看/切换当前会话模型

  系统提示：
  /system [文字]    → 查看/设置当前会话系统提示
  /trim <n>         → 当前会话只保留最近 n 轮
"""
import os
import re
import asyncio

import botpy
from botpy.message import GroupMessage, Message, C2CMessage
from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Static
from textual.containers import Horizontal

from session_manager import SessionManager, MODELS, DEFAULT_SYSTEM
from claude_client import ClaudeClient
from codex_client import CodexClient
from shell_session import get_shell

load_dotenv()

QQ_APP_ID     = os.environ["QQ_APP_ID"]
QQ_APP_SECRET = os.environ["QQ_APP_SECRET"]
API_KEY       = os.getenv("ANTHROPIC_API_KEY", "")
MODEL         = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
MAX_TURNS     = int(os.getenv("MAX_HISTORY_TURNS", "20"))
MAX_MSG_LEN   = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))

CODEX_BASE_URL = os.getenv("CODEX_BASE_URL", "")
CODEX_API_KEY  = os.getenv("CODEX_API_KEY", "")
CODEX_MODEL    = os.getenv("CODEX_MODEL", "")

# ─────────────────────────────────────────────────────────────────────────────
#  TUI App
# ─────────────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    layout: vertical;
}

Horizontal {
    height: 1fr;
}

#chat-panel {
    width: 1fr;
    border: solid $success;
    padding: 0 1;
}

#shell-panel {
    width: 1fr;
    border: solid $accent;
    padding: 0 1;
}

#status {
    height: 1;
    background: $primary;
    color: $text;
    padding: 0 1;
}
"""


class QQTerminalApp(App):
    CSS = CSS
    TITLE = "QQ Terminal Bridge"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield RichLog(id="chat-panel",  highlight=True, markup=True, wrap=True)
            yield RichLog(id="shell-panel", highlight=True, markup=True, wrap=True)
        yield Static("启动中...", id="status")
        yield Footer()

    # ── 生命周期 ──────────────────────────────────────────────────────────────
    def on_mount(self) -> None:
        self._sessions   = SessionManager(max_turns=MAX_TURNS)
        self._claude     = ClaudeClient(api_key=API_KEY, model=MODEL)
        self._codex      = CodexClient(
            base_url=CODEX_BASE_URL,
            api_key=CODEX_API_KEY,
            model=CODEX_MODEL,
        ) if CODEX_BASE_URL else None

        self._chat_log  = self.query_one("#chat-panel",  RichLog)
        self._shell_log = self.query_one("#shell-panel", RichLog)
        self._status    = self.query_one("#status",      Static)

        self._chat_log.write( "[bold green]═══ QQ 对话 ═══[/]")
        self._shell_log.write("[bold cyan]═══ Shell 输出 ═══[/]")

        asyncio.create_task(self._start_bot())

    async def _start_bot(self) -> None:
        self._update_status()
        intents = botpy.Intents(
            public_guild_messages=True,
            public_messages=True,
        )
        bot = _BotClient(intents=intents, app=self)
        await bot.start(appid=QQ_APP_ID, secret=QQ_APP_SECRET)

    # ── 状态栏 ────────────────────────────────────────────────────────────────
    def _update_status(self, session_id: str = "", extra: str = "") -> None:
        if session_id:
            ctx  = self._sessions.get(session_id)
            sess = ctx.session
            shell = get_shell(session_id)
            cwd   = _short_path(shell.cwd)
            mode  = "Shell" if ctx.shell_mode else "Chat"
            self._status.update(
                f" [{sess.name}] {sess.model_short} · {sess.turn_count}轮"
                f" | 📁{cwd} | {mode} | 用户:{self._sessions.total_users()}{' ' + extra if extra else ''}"
            )
        else:
            self._status.update(
                f" 用户:{self._sessions.total_users()} | 连接中...{' ' + extra if extra else ''}"
            )

    # ── 消息处理核心 ──────────────────────────────────────────────────────────
    async def handle_qq_message(
        self, reply_func, session_id: str, raw_content: str
    ) -> None:
        content = _clean_at(raw_content or "").strip()
        ctx     = self._sessions.get(session_id)
        shell   = get_shell(session_id)

        self._chat_log.write(f"\n[bold yellow]▶ [{_short_sid(session_id)}][/] {content}")

        if not content:
            await reply_func("请输入内容 😊")
            return

        lower = content.lower()

        # ── /help ─────────────────────────────────────────────────────────────
        if lower == "/help":
            msg = (
                "🤖 QQ Terminal Bridge 命令列表\n\n"
                "【Shell】\n"
                "  !cmd          执行 shell 命令\n"
                "  cd PATH       切换目录\n"
                "  /shell        进入 shell 模式\n"
                "  /chat         进入对话模式\n"
                "  /mode         查看当前模式\n"
                "  /cwd          查看当前目录\n\n"
                "【会话】\n"
                "  /new [名称]   新建会话\n"
                "  /sessions     列出所有会话\n"
                "  /switch <名> 切换会话\n"
                "  /rename <名> 重命名当前会话\n"
                "  /del <名>    删除会话\n"
                "  /ctx          查看当前会话详情\n"
                "  /clear        清空当前会话历史\n\n"
                "【模型后端】\n"
                "  /codex        切换到 Codex（本地模型）\n"
                "  /claude       切换到 Claude CLI\n\n"
                "【模型】\n"
                "  /models       列出可用模型\n"
                "  /model [别名] 查看/切换模型\n\n"
                "【提示词】\n"
                "  /system [文] 查看/设置系统提示\n"
                "  /trim <n>    只保留最近 n 轮"
            )
            await reply_func(msg)
            return

        # ── /codex ────────────────────────────────────────────────────────────
        if lower == "/codex":
            if not self._codex:
                await reply_func("⚠️ Codex 未配置，请在 .env 中填写 CODEX_BASE_URL / CODEX_API_KEY / CODEX_MODEL")
                return
            ctx.session.backend = "codex"
            ctx.clear()
            self._update_status(session_id)
            await reply_func(f"🤖 已切换到 Codex 模式（{CODEX_MODEL}）\n历史已清空")
            return

        # ── /claude ───────────────────────────────────────────────────────────
        if lower == "/claude":
            ctx.session.backend = "claude"
            ctx.clear()
            self._update_status(session_id)
            await reply_func(f"🧠 已切换到 Claude 模式（{ctx.session.model}）\n历史已清空")
            return

        # ── /shell ────────────────────────────────────────────────────────────
        if lower == "/shell":
            ctx.shell_mode = True
            ctx.clear()
            self._update_status(session_id)
            await reply_func(f"🖥️ 已切换到 Shell 模式\n当前目录：{shell.cwd}")
            return

        # ── /chat ─────────────────────────────────────────────────────────────
        if lower == "/chat":
            ctx.shell_mode = False
            ctx.clear()
            self._update_status(session_id)
            await reply_func("💬 已切换到 Claude 对话模式")
            return

        # ── /mode ─────────────────────────────────────────────────────────────
        if lower == "/mode":
            mode = "Shell 模式" if ctx.shell_mode else "Claude 对话模式"
            await reply_func(f"当前模式：{mode}\n当前目录：{shell.cwd}")
            return

        # ── /clear ────────────────────────────────────────────────────────────
        if lower == "/clear":
            ctx.clear()
            self._update_status(session_id)
            await reply_func("✅ 对话历史已清空")
            return

        # ── /cwd ──────────────────────────────────────────────────────────────
        if lower == "/cwd":
            await reply_func(f"📁 当前目录：{shell.cwd}")
            return

        # ── /ctx ──────────────────────────────────────────────────────────────
        if lower == "/ctx":
            await reply_func(ctx.session.info())
            return

        # ── /sessions ─────────────────────────────────────────────────────────
        if lower == "/sessions":
            await reply_func(ctx.list_sessions())
            return

        # ── /models ───────────────────────────────────────────────────────────
        if lower == "/models":
            lines = ["可用模型："]
            seen = set()
            for alias, full in MODELS.items():
                if full not in seen and len(alias) <= 6:
                    marker = "▶" if full == ctx.session.model else " "
                    lines.append(f"  {marker} {alias:<8} → {full}")
                    seen.add(full)
            await reply_func("\n".join(lines))
            return

        # ── /new [名称] ───────────────────────────────────────────────────────
        if lower == "/new" or lower.startswith("/new "):
            name = content[4:].strip() or None
            _, msg = ctx.new_session(name)
            self._update_status(session_id)
            await reply_func(msg)
            return

        # ── /switch <名称> ────────────────────────────────────────────────────
        if lower.startswith("/switch "):
            name = content[8:].strip()
            if not name:
                await reply_func("用法：/switch <会话名称>")
                return
            _, msg = ctx.switch_session(name)
            self._update_status(session_id)
            await reply_func(msg)
            return

        # ── /rename <新名称> ──────────────────────────────────────────────────
        if lower.startswith("/rename "):
            name = content[8:].strip()
            if not name:
                await reply_func("用法：/rename <新名称>")
                return
            msg = ctx.rename_session(name)
            self._update_status(session_id)
            await reply_func(msg)
            return

        # ── /del <名称> ───────────────────────────────────────────────────────
        if lower.startswith("/del "):
            name = content[5:].strip()
            if not name:
                await reply_func("用法：/del <会话名称>")
                return
            msg = ctx.delete_session(name)
            self._update_status(session_id)
            await reply_func(msg)
            return

        # ── /model [别名] ─────────────────────────────────────────────────────
        if lower == "/model" or lower.startswith("/model "):
            arg = content[6:].strip()
            if not arg:
                await reply_func(f"当前模型：{ctx.session.model}")
                return
            key = arg.lower()
            if key not in MODELS:
                await reply_func(
                    f"未知模型：{arg}\n"
                    f"可用别名：{', '.join(k for k in MODELS if len(k) <= 6)}"
                )
                return
            ctx.session.model = MODELS[key]
            self._update_status(session_id)
            await reply_func(f"✅ 已将当前会话模型切换为：{ctx.session.model}")
            return

        # ── /system [文字] ────────────────────────────────────────────────────
        if lower == "/system" or lower.startswith("/system "):
            arg = content[7:].strip()
            if not arg:
                sys_text = ctx.session.system
                label = "(默认)" if sys_text == DEFAULT_SYSTEM else ""
                await reply_func(f"当前系统提示{label}：\n{sys_text}")
                return
            ctx.session.system = arg
            ctx.clear()   # 切换提示词后清空历史以保持一致
            self._update_status(session_id)
            await reply_func(f"✅ 系统提示已更新，历史已清空")
            return

        # ── /trim <n> ─────────────────────────────────────────────────────────
        if lower.startswith("/trim "):
            arg = content[6:].strip()
            try:
                n = int(arg)
                assert n >= 1
            except (ValueError, AssertionError):
                await reply_func("用法：/trim <正整数>  例：/trim 5")
                return
            ctx.session.trim_to(n)
            self._update_status(session_id)
            await reply_func(f"✅ 已裁剪，当前保留 {ctx.session.turn_count} 轮")
            return

        # ── cd 命令（任意模式均直接执行）─────────────────────────────────────
        if content == "cd" or content.startswith("cd ") or content.startswith("cd\t"):
            result = shell.execute(content)
            self._shell_log.write(f"[cyan]$ {content}[/]\n{result}")
            self._update_status(session_id)
            await reply_func(result)
            return

        # ── ! 前缀：强制 shell ─────────────────────────────────────────────
        if content.startswith("!"):
            cmd = content[1:].strip()
            result = await asyncio.to_thread(shell.execute, cmd)
            self._shell_log.write(
                f"[cyan]$ {cmd}[/]\n"
                f"{result}\n"
                f"[dim]📁 {shell.cwd}[/]"
            )
            self._update_status(session_id)
            reply_text = f"$ {cmd}\n{result}\n📁 {shell.cwd}"
            for part in _split(reply_text, MAX_MSG_LEN):
                await reply_func(part)
            return

        # ── Shell 模式：所有消息当命令 ────────────────────────────────────────
        if ctx.shell_mode:
            result = await asyncio.to_thread(shell.execute, content)
            self._shell_log.write(
                f"[cyan]$ {content}[/]\n"
                f"{result}\n"
                f"[dim]📁 {shell.cwd}[/]"
            )
            self._update_status(session_id)
            reply_text = f"$ {content}\n{result}\n📁 {shell.cwd}"
            for part in _split(reply_text, MAX_MSG_LEN):
                await reply_func(part)
            return

        # ── Chat 模式：按 backend 路由 ────────────────────────────────────────
        backend = ctx.session.backend
        try:
            if backend == "codex":
                if not self._codex:
                    reply = "⚠️ Codex 未配置，请先在 .env 填写配置后重启，或用 /claude 切换回 Claude"
                else:
                    reply = await asyncio.to_thread(
                        self._codex.chat, self._sessions, session_id, content
                    )
            else:
                reply = await asyncio.to_thread(
                    self._claude.chat, self._sessions, session_id, content
                )
        except Exception as e:
            reply = f"⚠️ {backend} 出错：{str(e)[:200]}"

        self._chat_log.write(f"[green]◀ Claude:[/] {reply[:120]}{'...' if len(reply)>120 else ''}")
        self._update_status(session_id)
        for part in _split(reply, MAX_MSG_LEN):
            await reply_func(part)

    # ── 机器人就绪回调 ────────────────────────────────────────────────────────
    def on_bot_ready(self, name: str) -> None:
        self._chat_log.write(f"[bold green]✅ 机器人就绪：{name}[/]")
        self._update_status(extra="运行中")


# ─────────────────────────────────────────────────────────────────────────────
#  botpy 客户端（内嵌在 TUI 里）
# ─────────────────────────────────────────────────────────────────────────────

class _BotClient(botpy.Client):
    def __init__(self, *args, app: QQTerminalApp, **kwargs):
        super().__init__(*args, **kwargs)
        self._app = app

    async def on_ready(self):
        self._app.call_from_thread(self._app.on_bot_ready, self.robot.name)

    async def on_c2c_message_create(self, message: C2CMessage):
        session_id = f"c2c_{message.author.user_openid}"
        async def reply(text: str):
            await message.reply(content=text)
        await self._app.handle_qq_message(reply, session_id, message.content)

    async def on_group_at_message_create(self, message: GroupMessage):
        session_id = f"group_{message.group_openid}_{message.author.member_openid}"
        async def reply(text: str):
            await message.reply(content=text)
        await self._app.handle_qq_message(reply, session_id, message.content)

    async def on_at_message_create(self, message: Message):
        session_id = f"channel_{message.channel_id}_{message.author.id}"
        async def reply(text: str):
            await message.reply(content=text)
        await self._app.handle_qq_message(reply, session_id, message.content)


# ─────────────────────────────────────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _clean_at(text: str) -> str:
    return re.sub(r"<@!?\d+>", "", text).strip()

def _split(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts

def _short_sid(sid: str) -> str:
    return sid[-12:] if len(sid) > 12 else sid

def _short_path(cwd: str) -> str:
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    # 最多显示 30 字符，从右侧截
    return cwd[-30:] if len(cwd) > 30 else cwd


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QQTerminalApp()
    app.run()
