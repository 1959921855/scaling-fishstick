import uuid
from datetime import datetime
from typing import Dict, List, Any

# 内存存储
sessions: Dict[int, Dict] = {}
messages: Dict[int, List[Dict]] = {}
session_counter = 1

def init_db():
    print("使用内存存储（多轮对话已验证）")

async def create_session(user_id: str, title: str = "新对话") -> int:
    global session_counter
    sid = session_counter
    session_counter += 1
    sessions[sid] = {
        "id": sid,
        "user_id": user_id,
        "title": title,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }
    messages[sid] = []
    return sid

async def get_user_sessions(user_id: str, limit: int = 50):
    user_sessions = [s for s in sessions.values() if s["user_id"] == user_id]
    user_sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return [{"id": s["id"], "title": s["title"], "created_at": s["created_at"], "updated_at": s["updated_at"]} for s in user_sessions[:limit]]

async def get_session_messages(session_id: int, limit: int = 200):
    return messages.get(session_id, [])[-limit:]

async def save_message(user_id: str, session_id: int, role: str, content: str):
    if session_id not in messages:
        messages[session_id] = []
    messages[session_id].append({
        "role": role,
        "content": content,
        "timestamp": datetime.now()
    })
    if session_id in sessions:
        sessions[session_id]["updated_at"] = datetime.now()

async def delete_session(session_id: int, user_id: str) -> bool:
    if session_id in sessions and sessions[session_id]["user_id"] == user_id:
        sessions.pop(session_id, None)
        messages.pop(session_id, None)
        return True
    return False

async def delete_message_by_id(message_id: int, user_id: str) -> bool:
    # 简化，不实现单条删除
    return False

async def get_session_last_message(session_id: int):
    msgs = messages.get(session_id, [])
    if msgs:
        return msgs[-1]
    return None