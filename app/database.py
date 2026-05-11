import os
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from databases import Database

# ========== 数据库路径配置 ==========
# 使用 Railway 上的可写目录
DB_PATH = "/app/data/conversations.db"
# 增加 timeout=30 秒，避免写锁竞争导致请求超时
DATABASE_URL = f"sqlite:///{DB_PATH}?timeout=30"

print(f"[database] 数据库路径: {DB_PATH}")

database = Database(DATABASE_URL)
Base = declarative_base()

# ========== 会话表 ==========
class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(100), index=True)
    title = Column(String(200), default="新对话")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# ========== 消息表 ==========
class ConversationRecord(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(100), index=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role = Column(String(20))   # 'user' 或 'assistant'
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.now)

# ========== 同步引擎（用于创建表） ==========
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def init_db():
    """初始化数据库：创建表并优化并发性能"""
    # 启用 WAL 模式，提高读写并发能力
    with engine.connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    Base.metadata.create_all(bind=engine)
    print("数据库表初始化完成")

# ========== 异步 CRUD 操作 ==========
async def create_session(user_id: str, title: str = "新对话") -> int:
    query = """
        INSERT INTO sessions (user_id, title, created_at, updated_at)
        VALUES (:user_id, :title, :created_at, :updated_at)
    """
    now = datetime.now()
    result = await database.execute(query, values={
        "user_id": user_id,
        "title": title,
        "created_at": now,
        "updated_at": now
    })
    return result

async def get_user_sessions(user_id: str, limit: int = 50):
    """获取用户的会话列表，按更新时间倒序"""
    query = """
        SELECT id, title, created_at, updated_at
        FROM sessions
        WHERE user_id = :user_id
        ORDER BY updated_at DESC
        LIMIT :limit
    """
    rows = await database.fetch_all(query, values={"user_id": user_id, "limit": limit})
    return [{"id": row["id"], "title": row["title"], "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

async def get_session_messages(session_id: int, limit: int = 200):
    """获取某个会话的所有消息，按时间升序"""
    query = """
        SELECT id, role, content, timestamp
        FROM conversations
        WHERE session_id = :session_id
        ORDER BY timestamp ASC
        LIMIT :limit
    """
    rows = await database.fetch_all(query, values={"session_id": session_id, "limit": limit})
    return [{"id": row["id"], "role": row["role"], "content": row["content"], "timestamp": row["timestamp"]} for row in rows]

async def save_message(user_id: str, session_id: int, role: str, content: str):
    query = """
        INSERT INTO conversations (user_id, session_id, role, content, timestamp)
        VALUES (:user_id, :session_id, :role, :content, :timestamp)
    """
    await database.execute(query, values={
        "user_id": user_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": datetime.now()
    })
    # 更新会话的更新时间
    update_query = "UPDATE sessions SET updated_at = :updated_at WHERE id = :session_id"
    await database.execute(update_query, values={"updated_at": datetime.now(), "session_id": session_id})

async def delete_session(session_id: int, user_id: str) -> bool:
    """删除会话及其所有消息（级联删除由外键处理）"""
    query = "DELETE FROM sessions WHERE id = :session_id AND user_id = :user_id"
    result = await database.execute(query, values={"session_id": session_id, "user_id": user_id})
    return result > 0

async def delete_message_by_id(message_id: int, user_id: str) -> bool:
    query = "DELETE FROM conversations WHERE id = :id AND user_id = :user_id"
    result = await database.execute(query, values={"id": message_id, "user_id": user_id})
    return result > 0

async def get_session_last_message(session_id: int):
    """获取会话的最后一条消息（用于预览）"""
    query = """
        SELECT content, role FROM conversations
        WHERE session_id = :session_id
        ORDER BY timestamp DESC
        LIMIT 1
    """
    row = await database.fetch_one(query, values={"session_id": session_id})
    return row