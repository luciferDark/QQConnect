"""
QQ Terminal Bridge — 无 TUI 版（Docker / 服务器部署）
────────────────────────────────────────────────────
命令与 tui.py 完全一致，输出改为标准日志。
"""
import asyncio
import logging
import os
import re

import botpy
from botpy.message import GroupMessage, Message, C2CMessage
from dotenv import load_dotenv

from session_manager import SessionManager, CLAUDE_MODELS, DEFAULT_SYSTEM
import session_manager as _sm
from claude_client import ClaudeClient
from codex_client import CodexClient
from codex_cli_client import CodexCliClient
from shell_session import get_shell
from skill_loader import list_skills_text, load_skill, create_skill, write_skill, delete_skill

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("qq-bridge")

QQ_APP_ID      = os.environ["QQ_APP_ID"]
QQ_APP_SECRET  = os.environ["QQ_APP_SECRET"]
API_KEY        = os.getenv("ANTHROPIC_API_KEY", "")
MODEL          = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
MAX_TURNS      = int(os.getenv("MAX_HISTORY_TURNS", "20"))
MAX_MSG_LEN    = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))
CODEX_BASE_URL = os.getenv("CODEX_BASE_URL", "")
CODEX_API_KEY  = os.getenv("CODEX_API_KEY", "")
CODEX_MODEL    = os.getenv("CODEX_MODEL", "")

ADMIN_PORT = int(os.getenv("ADMIN_PORT", "12321"))

_sm.DEFAULT_CODEX_MODEL = CODEX_MODEL   # 注入全局默认 Codex 模型

_sessions = SessionManager(max_turns=MAX_TURNS)
restored  = _sessions.restore_from_disk()

import app_state
app_state.set_sessions(_sessions)        # 暴露给 admin server
log.info("已从磁盘恢复 %d 个用户会话", restored)

_claude   = ClaudeClient(api_key=API_KEY, model=MODEL)
_codex     = CodexClient(
    base_url=CODEX_BASE_URL,
    api_key=CODEX_API_KEY,
    model=CODEX_MODEL,
) if CODEX_BASE_URL else None
_codex_cli = CodexCliClient(
    base_url=CODEX_BASE_URL,
    api_key=CODEX_API_KEY,
    model=CODEX_MODEL,
) if CODEX_BASE_URL else None


# ─────────────────────────────────────────────────────────────────────────────
#  消息处理（与 tui.py 逻辑完全一致）
# ─────────────────────────────────────────────────────────────────────────────

async def handle_qq_message(reply_func, session_id: str, raw_content: str) -> None:
    content = _clean_at(raw_content or "").strip()
    ctx     = _sessions.get(session_id)
    shell   = get_shell(session_id)

    log.info("[%s] 收到: %s", _short_sid(session_id), content)

    if not content:
        await reply_func("请输入内容 😊")
        return

    lower = content.lower()

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

    if lower in ("/codex", "/codex api", "/codex cli"):
        if not _codex:
            await reply_func("⚠️ Codex 未配置，请在 .env 中填写 CODEX_BASE_URL / CODEX_API_KEY / CODEX_MODEL")
            return
        sub = lower.split()[-1]
        if sub == "cli":
            ctx.session.backend = "codex-cli"
            label = "Codex CLI（openai api chat.completions.create）"
        else:
            ctx.session.backend = "codex"
            label = "Codex API（SDK）"
        ctx.clear()
        await reply_func(f"🤖 已切换到 {label}\n模型：{CODEX_MODEL}，历史已清空")
        return

    if lower == "/claude":
        ctx.session.backend = "claude"
        ctx.clear()
        await reply_func(f"🧠 已切换到 Claude 模式（{ctx.session.model}）\n历史已清空")
        return

    if lower == "/shell":
        ctx.shell_mode = True
        ctx.clear()
        await reply_func(f"🖥️ 已切换到 Shell 模式\n当前目录：{shell.cwd}")
        return

    if lower == "/chat":
        ctx.shell_mode = False
        ctx.clear()
        await reply_func("💬 已切换到 Claude 对话模式")
        return

    if lower == "/mode":
        mode = "Shell 模式" if ctx.shell_mode else "Claude 对话模式"
        await reply_func(f"当前模式：{mode}\n当前目录：{shell.cwd}")
        return

    if lower == "/clear":
        ctx.clear()
        await reply_func("✅ 对话历史已清空")
        return

    if lower == "/cwd":
        await reply_func(f"📁 当前目录：{shell.cwd}")
        return

    if lower == "/ctx":
        await reply_func(ctx.session.info())
        return

    if lower == "/sessions":
        await reply_func(ctx.list_sessions())
        return

    if lower == "/models":
        backend = ctx.session.backend
        if backend == "claude":
            lines = ["Claude 可用模型："]
            seen = set()
            for alias, full in CLAUDE_MODELS.items():
                if full not in seen and len(alias) <= 6:
                    marker = "▶" if full == ctx.session.model else " "
                    lines.append(f"  {marker} {alias:<8} → {full}")
                    seen.add(full)
        else:
            cur = ctx.session.codex_model or CODEX_MODEL
            lines = [
                f"Codex 当前模型：{cur}",
                "",
                "用 /model <模型名> 切换到任意模型",
            ]
        await reply_func("\n".join(lines))
        return

    if lower == "/new" or lower.startswith("/new "):
        name = content[4:].strip() or None
        _, msg = ctx.new_session(name)
        await reply_func(msg)
        return

    if lower.startswith("/switch "):
        name = content[8:].strip()
        if not name:
            await reply_func("用法：/switch <会话名称>")
            return
        _, msg = ctx.switch_session(name)
        await reply_func(msg)
        return

    if lower.startswith("/rename "):
        name = content[8:].strip()
        if not name:
            await reply_func("用法：/rename <新名称>")
            return
        await reply_func(ctx.rename_session(name))
        return

    if lower.startswith("/del "):
        name = content[5:].strip()
        if not name:
            await reply_func("用法：/del <会话名称>")
            return
        await reply_func(ctx.delete_session(name))
        return

    if lower == "/model" or lower.startswith("/model "):
        arg     = content[6:].strip()
        backend = ctx.session.backend
        if not arg:
            await reply_func(f"当前模型：{ctx.session.active_model}")
            return
        if backend == "claude":
            key = arg.lower()
            if key not in CLAUDE_MODELS:
                aliases = ", ".join(k for k in CLAUDE_MODELS if len(k) <= 6)
                await reply_func(f"未知 Claude 模型：{arg}\n可用别名：{aliases}")
                return
            ctx.session.model = CLAUDE_MODELS[key]
            await reply_func(f"✅ Claude 模型已切换为：{ctx.session.model}")
        else:
            ctx.session.codex_model = arg
            await reply_func(f"✅ Codex 模型已切换为：{arg}")
        return

    if lower == "/system" or lower.startswith("/system "):
        arg = content[7:].strip()
        if not arg:
            sys_text = ctx.session.system
            label = "(默认)" if sys_text == DEFAULT_SYSTEM else ""
            await reply_func(f"当前系统提示{label}：\n{sys_text}")
            return
        ctx.session.system = arg
        ctx.clear()
        await reply_func("✅ 系统提示已更新，历史已清空")
        return

    if lower.startswith("/trim "):
        arg = content[6:].strip()
        try:
            n = int(arg)
            assert n >= 1
        except (ValueError, AssertionError):
            await reply_func("用法：/trim <正整数>  例：/trim 5")
            return
        ctx.session.trim_to(n)
        await reply_func(f"✅ 已裁剪，当前保留 {ctx.session.turn_count} 轮")
        return

    if lower == "/skills":
        await reply_func(list_skills_text())
        return

    if lower.startswith("/skill "):
        arg   = content[7:].strip()
        parts_s = arg.split(None, 1)
        sub   = parts_s[0].lower()
        rest  = parts_s[1] if len(parts_s) > 1 else ""

        if sub == "new":
            sub2 = rest.split(None, 1)
            if not sub2:
                await reply_func("用法：/skill new <name> <描述>")
                return
            await reply_func(create_skill(sub2[0], sub2[1] if len(sub2) > 1 else ""))
            return

        if sub == "write":
            sub2 = rest.split(None, 1)
            if len(sub2) < 2:
                await reply_func("用法：/skill write <name> <SKILL.md 内容>")
                return
            await reply_func(write_skill(sub2[0], sub2[1]))
            return

        if sub == "del":
            if not rest:
                await reply_func("用法：/skill del <name>")
                return
            await reply_func(delete_skill(rest.strip()))
            return

        skill_name = sub
        skill_msg  = rest
        if not skill_msg:
            await reply_func(load_skill(skill_name)[:3000])
            return
        backend = ctx.session.backend
        try:
            if backend == "codex":
                injected = f"请先调用 read_skill('{skill_name}') 获取指令，再完成：{skill_msg}"
                reply = await asyncio.to_thread(
                    _codex.chat, _sessions, session_id, injected
                ) if _codex else "⚠️ Codex 未配置"
            elif backend == "codex-cli":
                skill_ctx = load_skill(skill_name)
                injected  = f"[Skill: {skill_name}]\n{skill_ctx}\n\n请按以上指令完成：{skill_msg}"
                reply = await asyncio.to_thread(
                    _codex_cli.chat, _sessions, session_id, injected
                ) if _codex_cli else "⚠️ Codex CLI 未配置"
            else:
                reply = await asyncio.to_thread(
                    _claude.chat, _sessions, session_id, skill_msg, skill_name
                )
        except Exception as e:
            reply = f"⚠️ 出错：{str(e)[:200]}"
        for part in _split(reply, MAX_MSG_LEN):
            await reply_func(part)
        return

    if content == "cd" or content.startswith("cd ") or content.startswith("cd\t"):
        result = shell.execute(content)
        log.info("$ %s → %s", content, result[:80])
        await reply_func(result)
        return

    if content.startswith("!"):
        cmd = content[1:].strip()
        result = await asyncio.to_thread(shell.execute, cmd)
        log.info("$ %s", cmd)
        reply_text = f"$ {cmd}\n{result}\n📁 {shell.cwd}"
        for part in _split(reply_text, MAX_MSG_LEN):
            await reply_func(part)
        return

    if ctx.shell_mode:
        result = await asyncio.to_thread(shell.execute, content)
        log.info("$ %s", content)
        reply_text = f"$ {content}\n{result}\n📁 {shell.cwd}"
        for part in _split(reply_text, MAX_MSG_LEN):
            await reply_func(part)
        return

    # Chat 模式：按 backend 路由
    backend = ctx.session.backend
    try:
        if backend == "codex":
            if not _codex:
                reply = "⚠️ Codex 未配置，请先在 .env 填写配置后重启，或用 /claude 切换回 Claude"
            else:
                reply = await asyncio.to_thread(_codex.chat, _sessions, session_id, content)
        elif backend == "codex-cli":
            if not _codex_cli:
                reply = "⚠️ Codex CLI 未配置，请先在 .env 填写配置后重启"
            else:
                reply = await asyncio.to_thread(_codex_cli.chat, _sessions, session_id, content)
        else:
            reply = await asyncio.to_thread(_claude.chat, _sessions, session_id, content)
    except Exception as e:
        reply = f"⚠️ {backend} 出错：{str(e)[:200]}"

    log.info("%s → %s", backend, reply[:100])
    for part in _split(reply, MAX_MSG_LEN):
        await reply_func(part)


# ─────────────────────────────────────────────────────────────────────────────
#  botpy 客户端
# ─────────────────────────────────────────────────────────────────────────────

class BotClient(botpy.Client):
    async def on_ready(self):
        log.info("机器人就绪：%s", self.robot.name)

    async def on_c2c_message_create(self, message: C2CMessage):
        session_id = f"c2c_{message.author.user_openid}"
        async def reply(text: str):
            await message.reply(content=text)
        await handle_qq_message(reply, session_id, message.content)

    async def on_group_at_message_create(self, message: GroupMessage):
        session_id = f"group_{message.group_openid}_{message.author.member_openid}"
        async def reply(text: str):
            await message.reply(content=text)
        await handle_qq_message(reply, session_id, message.content)

    async def on_at_message_create(self, message: Message):
        session_id = f"channel_{message.channel_id}_{message.author.id}"
        async def reply(text: str):
            await message.reply(content=text)
        await handle_qq_message(reply, session_id, message.content)


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


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────

async def _main():
    from admin_server import start_admin
    intents = botpy.Intents(
        public_guild_messages=True,
        public_messages=True,
    )
    client = BotClient(intents=intents)
    log.info("Admin UI 启动于 http://localhost:%d", ADMIN_PORT)
    await asyncio.gather(
        start_admin(ADMIN_PORT),
        client.start(appid=QQ_APP_ID, secret=QQ_APP_SECRET),
    )

if __name__ == "__main__":
    asyncio.run(_main())
