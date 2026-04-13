"""
Admin Web UI — FastAPI
──────────────────────
默认端口 8080，通过 ADMIN_PORT 环境变量修改。
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from data_store import get_store
import app_state

app = FastAPI(title="QQ Terminal Bridge Admin", docs_url=None, redoc_url=None)


# ── 内存 → 统一格式 ───────────────────────────────────────────────────────────

def _users_from_memory() -> dict | None:
    """从内存 SessionManager 读取数据，转为与磁盘相同的 dict 格式。"""
    sm = app_state.get_sessions()
    if sm is None:
        return None
    result = {}
    for user_key, ctx in sm._users.items():
        sessions = {}
        for name, sess in ctx._sessions.items():
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
        result[user_key] = {
            "active":     ctx._active,
            "shell_mode": ctx.shell_mode,
            "sessions":   sessions,
        }
    return result


def _get_users() -> dict:
    """优先内存，回退磁盘。"""
    mem = _users_from_memory()
    if mem is not None:
        return mem
    return get_store().get_all_users()


# ─────────────────────────────────────────────────────────────────────────────
#  REST API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    users = _get_users()
    session_count = sum(len(u.get("sessions", {})) for u in users.values())
    return {"users": len(users), "sessions": session_count, "running": True}


@app.get("/api/users")
def api_users():
    users = _get_users()
    result = []
    for user_key, data in users.items():
        sessions = data.get("sessions", {})
        active   = data.get("active", "")
        sess_list = []
        for name, s in sessions.items():
            backend = s.get("backend", "codex-cli")
            model   = s.get("codex_model") or s.get("model", "") if backend != "claude" else s.get("model", "")
            sess_list.append({
                "name":       name,
                "backend":    backend,
                "model":      model,
                "turns":      sum(1 for m in s.get("history", []) if m["role"] == "user"),
                "updated_at": s.get("updated_at", 0),
                "active":     name == active,
            })
        sess_list.sort(key=lambda x: x["updated_at"], reverse=True)
        result.append({
            "user_key":      user_key,
            "short_key":     user_key[-16:] if len(user_key) > 16 else user_key,
            "type":          user_key.split("_")[0],
            "session_count": len(sessions),
            "sessions":      sess_list,
            "last_active":   max((s.get("updated_at", 0) for s in sessions.values()), default=0),
        })
    result.sort(key=lambda x: x["last_active"], reverse=True)
    return result


@app.get("/api/users/{user_key}/sessions/{session_name}/history")
def api_history(user_key: str, session_name: str):
    users = _get_users()
    if user_key not in users:
        raise HTTPException(404, "User not found")
    sess = users[user_key].get("sessions", {}).get(session_name)
    if not sess:
        raise HTTPException(404, "Session not found")
    backend = sess.get("backend", "codex-cli")
    model   = sess.get("codex_model") or sess.get("model", "") if backend != "claude" else sess.get("model", "")
    return {
        "session": {
            "name":       sess["name"],
            "backend":    backend,
            "model":      model,
            "system":     sess.get("system", ""),
            "created_at": sess.get("created_at", 0),
            "updated_at": sess.get("updated_at", 0),
        },
        "history": sess.get("history", []),
    }


@app.delete("/api/users/{user_key}/sessions/{session_name}")
def api_delete_session(user_key: str, session_name: str):
    # 从内存删除
    sm = app_state.get_sessions()
    if sm and user_key in sm._users:
        sm._users[user_key].delete_session(session_name)
    # 同步删磁盘
    get_store().delete_user_session(user_key, session_name)
    return {"ok": True}


@app.get("/api/tokens")
def api_tokens():
    store = get_store()
    stats = store.get_token_stats()

    # 最近 7 天
    today  = datetime.now()
    days   = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    daily  = stats.get("daily", {})
    chart_data = {d: daily.get(d, {}) for d in days}

    return {
        "total":      stats.get("total", {}),
        "daily":      stats.get("daily", {}),
        "chart_days": days,
        "chart_data": chart_data,
        "sessions":   stats.get("sessions", {}),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Main HTML
# ─────────────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QQ Terminal Bridge \u2014 Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@9.1.6/marked.min.js"></script>
<style>
  :root {
    --bg:        #0d0f16;
    --surface:   #141720;
    --card:      #1a1d2e;
    --border:    #252840;
    --hover:     #1f2235;
    --accent:    #6d5dfc;
    --claude:    #f59e0b;
    --codex:     #3b82f6;
    --codex-cli: #10b981;
    --danger:    #ef4444;
    --text:      #e2e8f0;
    --text2:     #94a3b8;
    --text3:     #4b5563;
    --radius:    12px;
    --radius-sm: 8px;
    --think-bg:  #111826;
    --think-border: #1e3050;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text);
         height: 100vh; overflow: hidden; display: flex; flex-direction: column; }

  /* Header */
  .header { display: flex; align-items: center; gap: 12px; padding: 0 24px; height: 56px;
            background: var(--surface); border-bottom: 1px solid var(--border);
            flex-shrink: 0; z-index: 10; }
  .header-logo { display: flex; align-items: center; gap: 10px; font-weight: 700;
                 font-size: 15px; color: var(--text); letter-spacing: -.3px; }
  .logo-icon { width: 30px; height: 30px;
               background: linear-gradient(135deg, var(--accent), #a78bfa);
               border-radius: 8px; display: flex; align-items: center;
               justify-content: center; font-size: 16px; }
  .header-right { margin-left: auto; display: flex; align-items: center; gap: 16px; }
  .live-badge { display: flex; align-items: center; gap: 6px; font-size: 12px;
                font-weight: 600; color: #10b981; background: #10b98120;
                padding: 4px 10px; border-radius: 20px; border: 1px solid #10b98140; }
  .live-dot { width: 6px; height: 6px; background: #10b981; border-radius: 50%;
              animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .stat-pill { font-size: 12px; color: var(--text2); background: var(--card);
               border: 1px solid var(--border); padding: 4px 12px; border-radius: 20px; }
  .stat-pill span { color: var(--text); font-weight: 600; }

  /* Layout */
  .layout { display: flex; flex: 1; overflow: hidden; }

  /* Sidebar */
  .sidebar { width: 280px; background: var(--surface); border-right: 1px solid var(--border);
             display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0; }
  .sidebar-tabs { display: flex; padding: 12px; gap: 6px; border-bottom: 1px solid var(--border); }
  .sidebar-tab { flex: 1; padding: 6px; border-radius: var(--radius-sm); background: none;
                 border: none; color: var(--text2); font-size: 12px; font-weight: 500;
                 cursor: pointer; transition: all .15s; }
  .sidebar-tab.active { background: var(--accent); color: #fff; }
  .search-box { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  .search-box input { width: 100%; background: var(--card); border: 1px solid var(--border);
                      border-radius: var(--radius-sm); padding: 7px 12px; color: var(--text);
                      font-size: 13px; outline: none; transition: border-color .15s; }
  .search-box input:focus { border-color: var(--accent); }
  .user-list { overflow-y: auto; flex: 1; padding: 8px; }
  .user-item { padding: 10px 12px; border-radius: var(--radius-sm); cursor: pointer;
               transition: background .15s; margin-bottom: 2px; }
  .user-item:hover { background: var(--hover); }
  .user-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .user-type-badge { font-size: 10px; font-weight: 700; padding: 2px 6px;
                     border-radius: 4px; text-transform: uppercase; }
  .type-c2c     { background: #6d5dfc22; color: #a78bfa; border: 1px solid #6d5dfc44; }
  .type-group   { background: #3b82f622; color: #60a5fa; border: 1px solid #3b82f644; }
  .type-channel { background: #10b98122; color: #34d399; border: 1px solid #10b98144; }
  .user-key  { font-size: 12px; font-weight: 600; color: var(--text);
               font-family: 'JetBrains Mono', monospace; }
  .user-meta { font-size: 11px; color: var(--text2); }
  .session-sublist { margin-top: 4px; padding-left: 8px; display: none; }
  .user-item.expanded .session-sublist { display: block; }
  .session-item { display: flex; align-items: center; gap: 6px; padding: 5px 8px;
                  border-radius: 6px; cursor: pointer; transition: background .12s;
                  margin-bottom: 2px; }
  .session-item:hover { background: var(--hover); }
  .session-item.active-sess { background: #6d5dfc18; border-left: 2px solid var(--accent); }
  .sess-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .bd-claude    { background: var(--claude); }
  .bd-codex     { background: var(--codex); }
  .bd-codex-cli { background: var(--codex-cli); }
  .sess-name  { font-size: 12px; color: var(--text); flex: 1; white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; }
  .sess-turns { font-size: 11px; color: var(--text3); white-space: nowrap; }

  /* Main */
  .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .tab-bar { display: flex; align-items: center; padding: 0 24px; background: var(--surface);
             border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .main-tab { padding: 16px 20px; font-size: 13px; font-weight: 500; color: var(--text2);
              cursor: pointer; border-bottom: 2px solid transparent; transition: all .15s;
              background: none; border-top: none; border-left: none; border-right: none; }
  .main-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-actions { margin-left: auto; display: flex; gap: 8px; }
  .btn { padding: 6px 14px; border-radius: var(--radius-sm); font-size: 12px; font-weight: 500;
         cursor: pointer; transition: all .15s; border: 1px solid var(--border);
         background: var(--card); color: var(--text2); }
  .btn:hover { border-color: var(--accent); color: var(--accent); }
  .btn-danger { border-color: #ef444440; color: var(--danger); }
  .btn-danger:hover { background: #ef444420; border-color: var(--danger); }

  /* History Panel */
  .history-panel { flex: 1; display: flex; overflow: hidden; }

  /* Session info sidebar */
  .sess-sidebar { width: 240px; flex-shrink: 0; border-right: 1px solid var(--border);
                  overflow-y: auto; background: var(--surface);
                  display: flex; flex-direction: column; }
  .info-section { padding: 14px 16px; border-bottom: 1px solid var(--border); }
  .info-section:last-child { border-bottom: none; flex: 1; }
  .info-title { font-size: 10px; font-weight: 700; color: var(--text3);
                text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }
  .info-row { display: flex; justify-content: space-between; align-items: flex-start;
              margin-bottom: 8px; gap: 8px; }
  .info-label { font-size: 12px; color: var(--text2); white-space: nowrap; }
  .info-value { font-size: 12px; color: var(--text); font-weight: 500; text-align: right;
                word-break: break-all; max-width: 130px; }
  .backend-badge { font-size: 10px; font-weight: 700; padding: 2px 7px;
                   border-radius: 4px; text-transform: uppercase; }
  .bg-claude    { background: var(--claude)22;    color: var(--claude);    border: 1px solid var(--claude)44; }
  .bg-codex     { background: var(--codex)22;     color: var(--codex);     border: 1px solid var(--codex)44; }
  .bg-codex-cli { background: var(--codex-cli)22; color: var(--codex-cli); border: 1px solid var(--codex-cli)44; }
  .stat-chips { display: flex; gap: 8px; flex-wrap: wrap; }
  .stat-chip { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
               padding: 8px 10px; text-align: center; flex: 1; min-width: 60px; }
  .chip-val { font-size: 20px; font-weight: 700; color: var(--text); line-height: 1; }
  .chip-lab { font-size: 10px; color: var(--text3); margin-top: 3px; }
  .sys-prompt { font-size: 11px; color: var(--text2); line-height: 1.5; background: var(--card);
                border-radius: 6px; padding: 8px; border: 1px solid var(--border);
                max-height: 120px; overflow-y: auto; font-family: 'JetBrains Mono', monospace;
                white-space: pre-wrap; word-break: break-word; }

  /* Chat area */
  .chat-area { flex: 1; overflow-y: auto; padding: 20px 28px;
               display: flex; flex-direction: column; gap: 20px; }
  .empty-state { flex: 1; display: flex; flex-direction: column; align-items: center;
                 justify-content: center; gap: 16px; color: var(--text3); }
  .empty-icon { font-size: 48px; opacity: .4; }
  .empty-text { font-size: 14px; }

  /* Messages */
  .msg { display: flex; gap: 12px; }
  .msg.user { flex-direction: row-reverse; }
  .msg-avatar { width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
                display: flex; align-items: center; justify-content: center;
                font-size: 15px; }
  .msg.user      .msg-avatar { background: linear-gradient(135deg,#6d5dfc,#a78bfa); color:#fff; }
  .msg.assistant .msg-avatar { background: var(--card); border: 1px solid var(--border); }
  .msg-body { max-width: 80%; display: flex; flex-direction: column; gap: 6px; }
  .msg.user .msg-body { align-items: flex-end; }
  .msg-meta { font-size: 11px; color: var(--text3); display: flex; align-items: center; gap: 6px; }
  .msg.user .msg-meta { flex-direction: row-reverse; }
  .msg-idx { background: var(--card); border: 1px solid var(--border); border-radius: 4px;
             padding: 1px 5px; font-family: 'JetBrains Mono', monospace; }

  /* Bubbles */
  .user-bubble { padding: 10px 14px; border-radius: 14px; border-bottom-right-radius: 4px;
                 font-size: 13px; line-height: 1.65; word-break: break-word; white-space: pre-wrap;
                 background: linear-gradient(135deg, var(--accent), #a78bfa); color:#fff; }
  .asst-bubble { padding: 12px 16px; border-radius: 14px; border-bottom-left-radius: 4px;
                 font-size: 13px; line-height: 1.7; word-break: break-word;
                 background: var(--card); border: 1px solid var(--border); color: var(--text); }
  .asst-bubble p { margin: 0 0 8px; }
  .asst-bubble p:last-child { margin-bottom: 0; }
  .asst-bubble pre { background: #0a0c14; border: 1px solid var(--border); border-radius: 6px;
                     padding: 10px 12px; overflow-x: auto;
                     font-family: 'JetBrains Mono', monospace; font-size: 12px;
                     margin: 6px 0; line-height: 1.5; }
  .asst-bubble code { font-family: 'JetBrains Mono', monospace; font-size: 12px;
                      background: #0a0c14; padding: 1px 5px; border-radius: 3px; color: #a5f3fc; }
  .asst-bubble pre code { background: none; padding: 0; color: #e2e8f0; }
  .asst-bubble ul, .asst-bubble ol { padding-left: 20px; margin: 4px 0; }
  .asst-bubble li { margin-bottom: 3px; }
  .asst-bubble h1,.asst-bubble h2,.asst-bubble h3 { font-size:14px; font-weight:600; margin: 8px 0 4px; }
  .asst-bubble blockquote { border-left: 3px solid var(--accent); padding-left: 10px;
                             margin: 6px 0; color: var(--text2); }
  .asst-bubble table { font-size: 12px; margin: 6px 0; }
  .asst-bubble th { background: var(--surface); }
  .asst-bubble td { background: none; }

  /* Thinking block */
  .think-block { border: 1px solid var(--think-border); border-radius: 10px;
                 overflow: hidden; background: var(--think-bg); }
  .think-header { display: flex; align-items: center; gap: 8px; padding: 9px 13px;
                  cursor: pointer; transition: background .12s; user-select: none; }
  .think-header:hover { background: #17243a; }
  .think-icon  { font-size: 13px; }
  .think-label { font-size: 12px; font-weight: 600; color: #6fa0cc; flex: 1; }
  .think-chars { font-size: 11px; color: var(--text3); font-family: 'JetBrains Mono', monospace; }
  .think-toggle { font-size: 10px; color: var(--text3); transition: transform .2s;
                  display: inline-block; width: 14px; text-align: center; }
  .think-body { padding: 10px 14px 12px; font-size: 12px; line-height: 1.65; color: #6fa0cc;
                font-family: 'JetBrains Mono', monospace; white-space: pre-wrap;
                word-break: break-word; border-top: 1px solid var(--think-border);
                max-height: 480px; overflow-y: auto; }

  /* Tokens panel */
  .tokens-panel { flex: 1; min-height: 0; overflow-y: auto; padding: 24px;
                  display: none; flex-direction: column; gap: 20px; }
  .tokens-panel.visible { display: flex; }
  .tokens-panel > * { flex-shrink: 0; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
  .stat-card { background: var(--card); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 16px; transition: border-color .15s; }
  .stat-card:hover { border-color: #6d5dfc44; }
  .stat-card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
  .stat-card-icon  { font-size: 20px; }
  .stat-card-title { font-size: 12px; color: var(--text2); font-weight: 500; }
  .stat-card-value { font-size: 28px; font-weight: 700; color: var(--text); letter-spacing: -1px; }
  .stat-card-sub   { font-size: 11px; color: var(--text3); margin-top: 4px; }
  .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; min-height: 0; }
  .chart-card { background: var(--card); border: 1px solid var(--border);
                border-radius: var(--radius); padding: 20px; overflow: hidden; }
  .chart-canvas-wrap { position: relative; height: 220px; width: 100%; }
  .chart-title { font-size: 13px; font-weight: 600; margin-bottom: 16px; color: var(--text); }
  .backend-bars { display: flex; flex-direction: column; gap: 12px; }
  .bar-row  { display: flex; flex-direction: column; gap: 4px; }
  .bar-label{ display: flex; justify-content: space-between; font-size: 12px; }
  .bar-name { color: var(--text2); font-weight: 500; }
  .bar-val  { color: var(--text); font-weight: 600; }
  .bar-track{ height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 3px; transition: width .6s ease; }
  .tokens-table-card { background: var(--card); border: 1px solid var(--border);
                       border-radius: var(--radius); overflow: hidden; }
  .tokens-table-card .chart-title { padding: 20px 20px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 10px 16px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--text3); font-weight: 600; text-transform: uppercase;
       letter-spacing: .6px; font-size: 11px; background: var(--surface); }
  td { color: var(--text2); }
  td:first-child { font-family: 'JetBrains Mono', monospace; color: var(--text); font-size: 11px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--hover); }

  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text3); }
  .hidden { display: none !important; }
  .loading { color: var(--text3); font-size: 13px; text-align: center; padding: 32px; }
  .mono { font-family: 'JetBrains Mono', monospace; }
</style>
</head>
<body>

<div class="header">
  <div class="header-logo">
    <div class="logo-icon">\u26a1</div>
    QQ Terminal Bridge
  </div>
  <div class="header-right">
    <div class="live-badge"><div class="live-dot"></div> \u8fd0\u884c\u4e2d</div>
    <div class="stat-pill">\u7528\u6237 <span id="hdr-users">-</span></div>
    <div class="stat-pill">\u4f1a\u8bdd <span id="hdr-sessions">-</span></div>
  </div>
</div>

<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-tabs">
      <button class="sidebar-tab active" onclick="sideTab('sessions',this)">\u4f1a\u8bdd</button>
      <button class="sidebar-tab" onclick="sideTab('tokens',this)">Token</button>
    </div>
    <div class="search-box">
      <input type="text" id="search" placeholder="\u641c\u7d22\u7528\u6237 / \u4f1a\u8bdd..." oninput="filterUsers()">
    </div>
    <div class="user-list" id="user-list">
      <div class="loading">\u52a0\u8f7d\u4e2d...</div>
    </div>
  </aside>

  <div class="main">
    <div class="tab-bar">
      <button class="main-tab active" id="tab-history" onclick="mainTab('history')">💬 \u4f1a\u8bdd\u5386\u53f2</button>
      <button class="main-tab" id="tab-tokens"  onclick="mainTab('tokens')">📊 Token \u7528\u91cf</button>
      <div class="tab-actions">
        <button class="btn btn-danger hidden" id="btn-del" onclick="deleteSession()">🗑 \u5220\u9664\u4f1a\u8bdd</button>
        <button class="btn" onclick="refresh()">\u21bb \u5237\u65b0</button>
      </div>
    </div>

    <div class="history-panel" id="panel-history">
      <div class="sess-sidebar" id="sess-info" style="display:none">
        <div class="info-section">
          <div class="info-title">\u4f1a\u8bdd\u8be6\u60c5</div>
          <div id="sess-info-content"></div>
        </div>
        <div class="info-section">
          <div class="info-title">\u7edf\u8ba1</div>
          <div id="sess-stats"></div>
        </div>
        <div class="info-section">
          <div class="info-title">\u7cfb\u7edf\u63d0\u793a</div>
          <div class="sys-prompt" id="sess-system">\u2014</div>
        </div>
      </div>
      <div class="chat-area" id="chat-area">
        <div class="empty-state">
          <div class="empty-icon">💬</div>
          <div class="empty-text">\u4ece\u5de6\u4fa7\u9009\u62e9\u4e00\u4e2a\u4f1a\u8bdd\u67e5\u770b\u5386\u53f2</div>
        </div>
      </div>
    </div>

    <div class="tokens-panel" id="panel-tokens">
      <div class="loading" id="tokens-loading">\u52a0\u8f7d\u4e2d...</div>
    </div>
  </div>
</div>

<script>
let users = [], selectedUser = null, selectedSession = null;
let currentMainTab = 'history', chartLine = null;

async function init() {
  await loadStatus();
  await loadUsers();
  setInterval(loadStatus, 5000);
  setInterval(() => loadUsers(true), 15000);
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

async function loadStatus() {
  const s = await api('/api/status').catch(() => null);
  if (!s) return;
  document.getElementById('hdr-users').textContent    = s.users;
  document.getElementById('hdr-sessions').textContent = s.sessions;
}

async function loadUsers(silent = false) {
  if (!silent) document.getElementById('user-list').innerHTML = '<div class="loading">\u52a0\u8f7d\u4e2d...</div>';
  users = await api('/api/users').catch(() => []);
  renderUsers();
}

async function loadHistory(userKey, sessName) {
  document.getElementById('chat-area').innerHTML = '<div class="loading">\u52a0\u8f7d\u5386\u53f2\u8bb0\u5f55...</div>';
  const data = await api('/api/users/' + encodeURIComponent(userKey) + '/sessions/' + encodeURIComponent(sessName) + '/history').catch(() => null);
  if (!data) { document.getElementById('chat-area').innerHTML = '<div class="loading">\u52a0\u8f7d\u5931\u8d25</div>'; return; }
  renderSession(data);
}

async function loadTokens() {
  document.getElementById('tokens-loading').style.display = 'block';
  const t = await api('/api/tokens').catch(() => null);
  document.getElementById('tokens-loading').style.display = 'none';
  if (!t) return;
  renderTokens(t);
}

function filterUsers() { renderUsers(); }

function renderUsers() {
  const q   = document.getElementById('search').value.toLowerCase();
  const tab = document.getElementById('user-list');
  if (!users.length) { tab.innerHTML = '<div class="loading">\u6682\u65e0\u4f1a\u8bdd\u6570\u636e</div>'; return; }
  const filtered = users.filter(u =>
    !q || u.user_key.toLowerCase().includes(q) ||
    u.sessions.some(s => s.name.toLowerCase().includes(q))
  );
  tab.innerHTML = filtered.map(u => {
    const isExp = u.user_key === selectedUser;
    const age   = u.last_active ? timeAgo(u.last_active) : '\u2014';
    const sessions = u.sessions.map(s => {
      const isCurr = u.user_key === selectedUser && s.name === selectedSession;
      const dotCls = 'bd-' + s.backend;
      return '<div class="session-item' + (isCurr ? ' active-sess' : '') + '" onclick="selectSession(\'' + escJs(u.user_key) + '\',\'' + escJs(s.name) + '\',event)">'
        + '<div class="sess-dot ' + dotCls + '"></div>'
        + '<div class="sess-name">' + esc(s.name) + (s.active ? ' \u25b6' : '') + '</div>'
        + '<div class="sess-turns">' + s.turns + '\u8f6e</div></div>';
    }).join('');
    return '<div class="user-item' + (isExp ? ' expanded' : '') + '" id="ui-' + escJs(u.user_key) + '" onclick="toggleUser(\'' + escJs(u.user_key) + '\',event)">'
      + '<div class="user-header"><span class="user-type-badge type-' + u.type + '">' + u.type + '</span>'
      + '<span class="user-key">' + esc(u.short_key) + '</span></div>'
      + '<div class="user-meta">' + u.session_count + ' \u4e2a\u4f1a\u8bdd \u00b7 ' + age + '</div>'
      + '<div class="session-sublist">' + sessions + '</div></div>';
  }).join('');
}

// ── Session history renderer ──────────────────────────────────────────────────
function renderSession(data) {
  const s    = data.session;
  const hist = data.history || [];

  document.getElementById('sess-info').style.display = '';
  document.getElementById('sess-system').textContent = s.system || '\uff08\u9ed8\u8ba4\uff09';

  const badgeCls = 'bg-' + s.backend;
  document.getElementById('sess-info-content').innerHTML =
    infoRow('\u540d\u79f0', '<span class="mono" style="font-size:11px">' + esc(s.name) + '</span>') +
    infoRow('\u540e\u7aef', '<span class="backend-badge ' + badgeCls + '">' + s.backend + '</span>') +
    infoRow('\u6a21\u578b', '<span class="mono" style="font-size:11px">' + esc(s.model) + '</span>') +
    infoRow('\u521b\u5efa', fmtTime(s.created_at)) +
    infoRow('\u66f4\u65b0', fmtTime(s.updated_at));

  const turns    = hist.filter(m => m.role === 'user').length;
  const hasThink = hist.some(m => m.role === 'assistant' && parseThinking(m.content).thinking != null);
  document.getElementById('sess-stats').innerHTML =
    '<div class="stat-chips">'
    + '<div class="stat-chip"><div class="chip-val">' + turns + '</div><div class="chip-lab">\u5bf9\u8bdd\u8f6e</div></div>'
    + '<div class="stat-chip"><div class="chip-val">' + hist.length + '</div><div class="chip-lab">\u6d88\u606f\u6570</div></div>'
    + '</div>'
    + '<div style="margin-top:8px;font-size:11px;color:' + (hasThink ? 'var(--codex-cli)' : 'var(--text3)') + '">'
    + (hasThink ? '\u2713 \u542b\u601d\u8003\u5185\u5bb9' : '\u2014 \u65e0\u601d\u8003\u5185\u5bb9') + '</div>';

  const area = document.getElementById('chat-area');
  if (!hist.length) {
    area.innerHTML = '<div class="empty-state"><div class="empty-icon">💭</div><div class="empty-text">\u8be5\u4f1a\u8bdd\u6682\u65e0\u6d88\u606f</div></div>';
    return;
  }

  const icons = { claude: '🧠', codex: '🤖', 'codex-cli': '💻' };
  const asstIcon = icons[s.backend] || '🤖';

  area.innerHTML = hist.map((m, i) => {
    if (m.role === 'user') {
      return '<div class="msg user">'
        + '<div class="msg-avatar">👤</div>'
        + '<div class="msg-body">'
        + '<div class="msg-meta"><span class="msg-idx">#' + (i+1) + '</span> \u7528\u6237</div>'
        + '<div class="user-bubble">' + renderText(m.content) + '</div>'
        + '</div></div>';
    } else {
      const parsed = parseThinking(m.content);
      return '<div class="msg assistant">'
        + '<div class="msg-avatar">' + asstIcon + '</div>'
        + '<div class="msg-body">'
        + '<div class="msg-meta"><span class="msg-idx">#' + (i+1) + '</span> ' + esc(s.backend) + '</div>'
        + renderAsstContent(parsed, i)
        + '</div></div>';
    }
  }).join('');
  area.scrollTop = area.scrollHeight;
}

function infoRow(label, valHtml) {
  return '<div class="info-row"><span class="info-label">' + label + '</span><span class="info-value">' + valHtml + '</span></div>';
}

// ── Thinking block ────────────────────────────────────────────────────────────
function parseThinking(content) {
  if (!content) return { thinking: null, reply: '' };
  // Match <think>...</think> or <thinking>...</thinking> at the start
  const trimmed = content.trimStart();
  const tag1 = trimmed.match(/^<think(?:ing)?>/i);
  if (!tag1) return { thinking: null, reply: content };
  const openTag  = tag1[0];
  const closeTag = openTag.replace('<', '</');
  const closeIdx = trimmed.toLowerCase().indexOf(closeTag.toLowerCase());
  if (closeIdx === -1) return { thinking: null, reply: content };
  const thinkText = trimmed.substring(openTag.length, closeIdx).trim();
  const reply     = trimmed.substring(closeIdx + closeTag.length).trim();
  return { thinking: thinkText, reply };
}

function renderAsstContent(parsed, idx) {
  let html = '';
  if (parsed.thinking != null) {
    html += '<div class="think-block">'
      + '<div class="think-header" onclick="toggleThink(' + idx + ',this)">'
      + '<span class="think-icon">🧩</span>'
      + '<span class="think-label">\u601d\u8003\u8fc7\u7a0b</span>'
      + '<span class="think-chars">' + parsed.thinking.length + ' \u5b57\u7b26</span>'
      + '<span class="think-toggle">\u25b6</span>'
      + '</div>'
      + '<div class="think-body hidden" id="tb-' + idx + '">' + esc(parsed.thinking) + '</div>'
      + '</div>';
  }
  if (parsed.reply) {
    html += '<div class="asst-bubble">' + renderMarkdown(parsed.reply) + '</div>';
  } else if (parsed.thinking == null) {
    html += '<div class="asst-bubble" style="color:var(--text3);font-style:italic">\uff08\u7a7a\u56de\u590d\uff09</div>';
  }
  return html;
}

function toggleThink(idx, headerEl) {
  const body = document.getElementById('tb-' + idx);
  const tog  = headerEl.querySelector('.think-toggle');
  const open = body.classList.toggle('hidden');
  tog.textContent = open ? '\u25b6' : '\u25bc';
}

// ── Text helpers ──────────────────────────────────────────────────────────────
function renderText(t) {
  return esc(t).replace(/\\n/g, '<br>');
}
function renderMarkdown(t) {
  if (typeof marked !== 'undefined') {
    try { marked.setOptions({ breaks: true, gfm: true }); return marked.parse(t); } catch(e) {}
  }
  return renderText(t);
}

// ── Token stats ───────────────────────────────────────────────────────────────
function renderTokens(t) {
  const panel = document.getElementById('panel-tokens');
  const total = t.total || {};
  const backends = ['claude', 'codex', 'codex-cli'];
  const bColors  = { claude: '#f59e0b', codex: '#3b82f6', 'codex-cli': '#10b981' };
  const bLabels  = { claude: 'Claude CLI', codex: 'Codex API', 'codex-cli': 'Codex CLI' };

  const allIn  = Object.values(total).reduce((s,b) => s + (b.input||0), 0);
  const allOut = Object.values(total).reduce((s,b) => s + (b.output||0), 0);
  const allTot = allIn + allOut;
  const todayKey = new Date().toISOString().slice(0,10);
  const todayTot = Object.values((t.daily||{})[todayKey]||{}).reduce((s,b) => s+(b.input||0)+(b.output||0), 0);

  const cards = [
    { icon:'🔢', title:'\u603b Token \u7528\u91cf', value: fmtNum(allTot),   sub: '\u8f93\u5165 ' + fmtNum(allIn) + ' \u00b7 \u8f93\u51fa ' + fmtNum(allOut) },
    { icon:'📅', title:'\u4eca\u65e5\u7528\u91cf',  value: fmtNum(todayTot), sub: todayKey },
    { icon:'👥', title:'\u4f1a\u8bdd\u6570',        value: Object.keys(t.sessions||{}).length, sub:'\u5386\u53f2\u4f1a\u8bdd' },
    { icon:'🌐', title:'\u6d3b\u8dc3\u540e\u7aef',  value: Object.keys(total).length, sub: backends.filter(b=>total[b]).join(' / ')||'\u2014' },
  ];
  const statsHtml = '<div class="stats-grid">' + cards.map(c =>
    '<div class="stat-card"><div class="stat-card-header"><div class="stat-card-icon">' + c.icon + '</div>'
    + '<div class="stat-card-title">' + c.title + '</div></div>'
    + '<div class="stat-card-value">' + c.value + '</div>'
    + '<div class="stat-card-sub">' + c.sub + '</div></div>'
  ).join('') + '</div>';

  const maxTot = Math.max(...backends.map(b => (total[b]?.input||0)+(total[b]?.output||0)), 1);
  const barsHtml = '<div class="chart-card"><div class="chart-title">\u5404\u540e\u7aef\u7d2f\u8ba1\u7528\u91cf</div>'
    + '<div class="backend-bars">' + backends.map(b => {
      const tot = (total[b]?.input||0)+(total[b]?.output||0);
      const pct = Math.round(tot/maxTot*100);
      return '<div class="bar-row"><div class="bar-label">'
        + '<span class="bar-name" style="color:' + bColors[b] + '">' + bLabels[b] + '</span>'
        + '<span class="bar-val">' + fmtNum(tot) + '</span></div>'
        + '<div class="bar-track"><div class="bar-fill" style="width:' + pct + '%;background:' + bColors[b] + '"></div></div></div>';
    }).join('') + '</div></div>';

  const days  = t.chart_days || [];
  const cdata = t.chart_data || {};
  const chartHtml = '<div class="chart-card"><div class="chart-title">\u8fd1 7 \u5929 Token \u8d8b\u52bf</div>'
    + '<div class="chart-canvas-wrap"><canvas id="daily-chart"></canvas></div></div>';

  const sessRows = Object.entries(t.sessions||{})
    .sort((a,b) => (b[1].input+b[1].output)-(a[1].input+a[1].output)).slice(0,20);
  const tableHtml = '<div class="tokens-table-card"><div class="chart-title">\u4f1a\u8bdd\u7528\u91cf Top 20</div>'
    + '<table><thead><tr><th>\u4f1a\u8bdd</th><th>\u540e\u7aef</th><th>\u8f93\u5165</th><th>\u8f93\u51fa</th><th>\u5408\u8ba1</th></tr></thead><tbody>'
    + sessRows.map(([k,v]) =>
        '<tr><td>' + esc(k) + '</td>'
        + '<td><span class="backend-badge bg-' + (v.backend||'') + '">' + (v.backend||'-') + '</span></td>'
        + '<td>' + fmtNum(v.input) + '</td><td>' + fmtNum(v.output) + '</td>'
        + '<td style="font-weight:600;color:var(--text)">' + fmtNum(v.input+v.output) + '</td></tr>'
      ).join('')
    + '</tbody></table></div>';

  panel.innerHTML = statsHtml + '<div class="charts-row">' + barsHtml + chartHtml + '</div>' + tableHtml;

  const ctx = document.getElementById('daily-chart');
  if (ctx && days.length) {
    if (chartLine) chartLine.destroy();
    chartLine = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: days.map(d => d.slice(5)),
        datasets: backends.filter(b => days.some(d => cdata[d]?.[b])).map(b => ({
          label: bLabels[b],
          data:  days.map(d => ((cdata[d]||{})[b]?.input||0)+((cdata[d]||{})[b]?.output||0)),
          backgroundColor: bColors[b] + 'cc', borderRadius: 4, borderSkipped: false,
        })),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
        scales: {
          x: { stacked: true, grid: { color: '#252840' }, ticks: { color: '#64748b' } },
          y: { stacked: true, grid: { color: '#252840' }, ticks: { color: '#64748b', callback: v => fmtNum(v) } },
        },
      },
    });
  }
}

// ── Actions ───────────────────────────────────────────────────────────────────
function toggleUser(key, e) {
  e.stopPropagation();
  const item = document.getElementById('ui-' + key);
  const was  = item.classList.contains('expanded');
  document.querySelectorAll('.user-item.expanded').forEach(el => el.classList.remove('expanded'));
  if (!was) { item.classList.add('expanded'); selectedUser = key; }
  else { selectedUser = null; }
}

function selectSession(userKey, sessName, e) {
  e.stopPropagation();
  selectedUser    = userKey;
  selectedSession = sessName;
  document.getElementById('btn-del').classList.remove('hidden');
  renderUsers();
  loadHistory(userKey, sessName);
}

async function deleteSession() {
  if (!selectedUser || !selectedSession) return;
  if (!confirm('\u5220\u9664\u4f1a\u8bdd "' + selectedSession + '"\uff1f\u6b64\u64cd\u4f5c\u4e0d\u53ef\u6062\u590d\u3002')) return;
  await fetch('/api/users/' + encodeURIComponent(selectedUser) + '/sessions/' + encodeURIComponent(selectedSession), { method: 'DELETE' });
  selectedSession = null;
  document.getElementById('btn-del').classList.add('hidden');
  document.getElementById('sess-info').style.display = 'none';
  document.getElementById('chat-area').innerHTML = '<div class="empty-state"><div class="empty-icon">💬</div><div class="empty-text">\u4ece\u5de6\u4fa7\u9009\u62e9\u4e00\u4e2a\u4f1a\u8bdd\u67e5\u770b\u5386\u53f2</div></div>';
  await loadUsers();
}

function mainTab(tab) {
  currentMainTab = tab;
  document.getElementById('panel-history').style.display = tab === 'history' ? '' : 'none';
  document.getElementById('panel-tokens').classList.toggle('visible', tab === 'tokens');
  document.getElementById('tab-history').classList.toggle('active', tab === 'history');
  document.getElementById('tab-tokens').classList.toggle('active',  tab === 'tokens');
  if (tab === 'tokens') loadTokens();
}

function sideTab(tab, btn) {
  document.querySelectorAll('.sidebar-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  mainTab(tab === 'tokens' ? 'tokens' : 'history');
}

function refresh() {
  loadUsers();
  if (selectedUser && selectedSession) loadHistory(selectedUser, selectedSession);
  if (currentMainTab === 'tokens') loadTokens();
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                  .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function escJs(s) { return String(s).replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'"); }
function fmtNum(n) {
  n = Number(n)||0;
  return n>=1e6 ? (n/1e6).toFixed(1)+'M' : n>=1e3 ? (n/1e3).toFixed(1)+'K' : String(n);
}
function fmtTime(ts) {
  if (!ts) return '\u2014';
  return new Date(ts*1000).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}
function timeAgo(ts) {
  if (!ts) return '\u2014';
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60)    return '\u521a\u521a';
  if (s < 3600)  return Math.floor(s/60) + '\u5206\u949f\u524d';
  if (s < 86400) return Math.floor(s/3600) + '\u5c0f\u65f6\u524d';
  return Math.floor(s/86400) + '\u5929\u524d';
}

init();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


# ─────────────────────────────────────────────────────────────────────────────
#  启动函数（供 bot 调用）
# ─────────────────────────────────────────────────────────────────────────────

async def start_admin(port: int = 8080):
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
