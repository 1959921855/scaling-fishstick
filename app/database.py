import os
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from databases import Database

# ========== 数据库路径配置 ==========
DB_PATH = "/app/data/conversations.db"
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
    role = Column(String(20))
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.now)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def init_db():
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
    Base.metadata.create_all(bind=engine)
    print("数据库表初始化完成")

# 以下其余的函数保持不变...
# （省略 save_message, get_user_sessions 等，它们和之前一样）