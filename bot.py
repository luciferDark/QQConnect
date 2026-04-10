"""
QQ 机器人主程序
支持：QQ群消息（@机器人触发）、频道消息（@机器人触发）

命令：
  /help   - 查看帮助
  /clear  - 清空当前会话的对话历史
  /info   - 查看当前会话信息
"""
import os
import re
import asyncio
import botpy
from botpy.message import GroupMessage, Message, C2CMessage
from botpy import logging as botpy_logging
from dotenv import load_dotenv

from session_manager import SessionManager
from claude_client import ClaudeClient

# ─── 加载配置 ────────────────────────────────────────────────────────────────
load_dotenv()

QQ_APP_ID     = os.environ["QQ_APP_ID"]
QQ_APP_SECRET = os.environ["QQ_APP_SECRET"]
API_KEY       = os.getenv("ANTHROPIC_API_KEY", "")
MODEL         = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
MAX_TURNS     = int(os.getenv("MAX_HISTORY_TURNS", "20"))
MAX_MSG_LEN   = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))

# ─── 初始化 ───────────────────────────────────────────────────────────────────
sessions = SessionManager(max_turns=MAX_TURNS)
claude   = ClaudeClient(api_key=API_KEY, model=MODEL)

_log = botpy_logging.get_logger()

HELP_TEXT = """🤖 Claude AI 助手

📌 使用方式：
  @我 + 你的问题

📌 命令：
  /help   查看帮助
  /clear  清空对话历史（开始新话题）
  /info   查看会话信息

💡 本机器人会记住本次对话内容，
   发送 /clear 可以重置。"""


# ─── 工具函数 ─────────────────────────────────────────────────────────────────
def split_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """将长文本按 max_len 切割成多段，避免 QQ 单条消息超限"""
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts


def clean_at_content(content: str) -> str:
    """
    移除消息中的 @机器人 标记（botpy 传入的 content 包含 <@!id> 格式）
    """
    content = re.sub(r"<@!?\d+>", "", content)
    return content.strip()


async def handle_message(reply_func, session_id: str, raw_content: str):
    """通用消息处理逻辑（群和频道复用）"""
    content = clean_at_content(raw_content or "")

    if not content:
        await reply_func("请 @我 并输入你的问题 😊")
        return

    # ── 内置命令 ──────────────────────────────────────────────────────────────
    if content.lower() == "/help":
        await reply_func(HELP_TEXT)
        return

    if content.lower() == "/clear":
        sessions.clear(session_id)
        await reply_func("✅ 对话历史已清空，我们重新开始吧！")
        return

    if content.lower() == "/info":
        history = sessions.get_history(session_id)
        turns = len(history) // 2
        await reply_func(
            f"📊 当前会话\n"
            f"  ID：{session_id[-20:]}\n"
            f"  已对话：{turns} 轮\n"
            f"  模型：{MODEL}"
        )
        return

    # ── 调用 Claude ───────────────────────────────────────────────────────────
    _log.info(f"[{session_id[-20:]}] 用户: {content[:80]}")
    try:
        reply = claude.chat(sessions, session_id, content)
    except Exception as e:
        _log.error(f"Claude API 错误: {e}")
        await reply_func(f"⚠️ 调用 Claude 出错：{str(e)[:200]}")
        return

    _log.info(f"[{session_id[-20:]}] 回复: {reply[:80]}")

    # 超长回复分段发送
    for part in split_message(reply):
        await reply_func(part)


# ─── QQ Bot 客户端 ────────────────────────────────────────────────────────────
class MyClient(botpy.Client):

    async def on_ready(self):
        _log.info(f"机器人就绪：{self.robot.name}（AppID: {QQ_APP_ID}）")
        asyncio.create_task(self._cleanup_loop())

    # ── QQ群消息（需要 @机器人 触发）─────────────────────────────────────────
    async def on_group_at_message_create(self, message: GroupMessage):
        """QQ 群 @消息（group_openid 是群唯一标识）"""
        session_id = f"group_{message.group_openid}_{message.author.member_openid}"

        async def reply(text: str):
            await message.reply(content=text)

        await handle_message(reply, session_id, message.content)

    # ── QQ私聊消息（C2C，用户直接发消息给机器人）────────────────────────────
    async def on_c2c_message_create(self, message: C2CMessage):
        """QQ 私聊消息（无需 @，直接发即可）"""
        session_id = f"c2c_{message.author.user_openid}"

        async def reply(text: str):
            await message.reply(content=text)

        await handle_message(reply, session_id, message.content)

    # ── QQ频道消息（需要 @机器人 触发）───────────────────────────────────────
    async def on_at_message_create(self, message: Message):
        """QQ 频道 @消息"""
        session_id = f"channel_{message.channel_id}_{message.author.id}"

        async def reply(text: str):
            await message.reply(content=text)

        await handle_message(reply, session_id, message.content)

    # ── 定时清理不活跃会话 ────────────────────────────────────────────────────
    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(1800)  # 每 30 分钟清理一次
            cleaned = sessions.cleanup_inactive(max_age_seconds=3600)
            if cleaned:
                _log.info(
                    f"清理了 {cleaned} 个不活跃会话，"
                    f"当前活跃：{sessions.get_session_count()}"
                )


# ─── 启动 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    intents = botpy.Intents(
        public_guild_messages=True,  # 频道 @消息
        public_messages=True,        # 群 @消息（群机器人）
    )
    client = MyClient(intents=intents)
    client.run(appid=QQ_APP_ID, secret=QQ_APP_SECRET)
