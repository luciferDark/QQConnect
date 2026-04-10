"""
快速验证 Claude Code CLI 是否可用
运行：python test_claude.py
"""
import os
from dotenv import load_dotenv
from session_manager import SessionManager
from claude_client import ClaudeClient

load_dotenv()

print("正在测试 Claude Code CLI ...")
sm = SessionManager()
client = ClaudeClient()

try:
    reply = client.chat(sm, "test_user", "用一句话介绍你自己")
    print(f"Claude 回复: {reply[:200]}")
    print("Claude Code CLI 调用成功!")
except Exception as e:
    print(f"[ERROR] {e}")
