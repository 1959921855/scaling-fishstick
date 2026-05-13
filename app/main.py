from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import Optional, List, Dict
from openai import OpenAI
from dotenv import load_dotenv
import uvicorn
import os
import logging
from datetime import datetime

from app.audio_handler import AudioHandler
from app.database import (
    database, init_db, save_message, get_session_messages, get_user_sessions,
    create_session, delete_session, delete_message_by_id, get_session_last_message
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== 数据模型 ==========
class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = "default_user"
    session_id: Optional[int] = None

class ChatResponse(BaseModel):
    response: str
    session_id: int
    message_id: Optional[int] = None

class TextToSpeechRequest(BaseModel):
    text: str
    voice: Optional[str] = "zh-CN-XiaoxiaoNeural"   # 新增音色参数

class NewSessionRequest(BaseModel):
    user_id: str = "default_user"
    title: Optional[str] = "新对话"

# ========== DeepSeek 服务 ==========
class DeepSeekService:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not found")
        self.client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")

    def chat(self, messages: List[Dict]) -> str:
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.8,
                max_tokens=800
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"DeepSeek API错误: {e}")
            return "抱歉，我现在遇到了一点问题。"

SYSTEM_PROMPT = """你是专门为老年人设计的语音陪聊智能体，你的名字叫“小暖”。请始终记住：你的名字是小暖。

你的特点：
- 语速慢、清晰，用词简单易懂，避免网络用语和复杂词汇。
- 语气亲切、耐心、温和，像晚辈和长辈聊天一样。
- 多关心老人的身体、心情、日常生活（吃了什么、睡得好不好、天气变化等）。
- 聊天内容积极、阳光，适当给予鼓励和安慰。
- 如果老人说“听不懂”、“再说一遍”，要耐心重复或换种更简单的说法。
- 可以聊聊家常、过去的故事、养生小知识、天气预报等。
- 每次回答不宜过长，分短句表达，方便语音合成。
- 主动询问老人的感受，比如“您今天开心吗？”、“有没有哪里不舒服呀？”。
- 使用“您”称呼老人，而不是“你”。

重要格式：直接输出对话内容，绝对不要使用括号描述表情、动作或语气（例如不要写“（温柔地笑）”）。

重要事实约束：
- 你不知道任何实时信息（例如当前时间、日期、天气、股票、新闻、定位等）。如果用户问“现在几点”、“今天星期几”、“外面下雨吗”等实时问题，请诚实回答：“对不起，小暖不知道现在的实时信息，您可以看看手机或问问身边的人。”
- 对于你不知道、不确定、或是实时信息的问题，严禁编造答案。直接说不知道或给出上述标准回复。
- 不要假装有眼睛、耳朵或感知能力。你只能通过对话交流。
"""

# ========== 全局实例 ==========
deepseek_service = None
audio_handler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global deepseek_service, audio_handler
    logger.info("初始化智能体...")
    init_db()
    await database.connect()
    logger.info("数据库连接成功")
    deepseek_service = DeepSeekService()
    audio_handler = AudioHandler()
    logger.info("所有服务初始化完成")
    yield
    await database.disconnect()
    logger.info("关闭服务")

app = FastAPI(title="语音陪聊智能体", version="4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ========== 会话管理 API ==========
@app.post("/session/new")
async def new_session(request: NewSessionRequest):
    session_id = await create_session(request.user_id, request.title)
    return {"session_id": session_id, "title": request.title}

@app.get("/sessions/{user_id}")
async def list_sessions(user_id: str, limit: int = 50):
    sessions = await get_user_sessions(user_id, limit)
    for s in sessions:
        last_msg = await get_session_last_message(s["id"])
        if last_msg:
            preview = last_msg["content"][:50] + ("..." if len(last_msg["content"]) > 50 else "")
            s["preview"] = preview
        else:
            s["preview"] = "暂无消息"
    return {"sessions": sessions}

@app.get("/session/{session_id}/messages")
async def get_messages(session_id: int):
    messages = await get_session_messages(session_id)
    return {"messages": messages}

@app.delete("/session/{session_id}")
async def del_session(session_id: int, user_id: str = "default_user"):
    success = await delete_session(session_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}

@app.delete("/conversation/message/{message_id}")
async def delete_message_api(message_id: int, user_id: str = "default_user"):
    success = await delete_message_by_id(message_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "deleted"}

# ========== 新增：获取音色列表 ==========
@app.get("/voices")
async def get_voices():
    voices = await audio_handler.get_voices()
    return {"voices": voices}

# ========== 聊天 API ==========
@app.post("/chat/text", response_model=ChatResponse)
async def text_chat(request: ChatRequest):
    user_id = request.user_id
    user_msg = request.message

    if request.session_id is None:
        session_id = await create_session(user_id, "新对话")
    else:
        session_id = request.session_id

    history = await get_session_messages(session_id, limit=10)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_msg})

    ai_reply = deepseek_service.chat(messages)

    await save_message(user_id, session_id, "user", user_msg)
    await save_message(user_id, session_id, "assistant", ai_reply)

    if len(history) == 0:
        new_title = user_msg[:30] + ("..." if len(user_msg) > 30 else "")
        await database.execute("UPDATE sessions SET title = :title WHERE id = :session_id",
                               values={"title": new_title, "session_id": session_id})

    return ChatResponse(response=ai_reply, session_id=session_id)

# ========== 修改 TTS 端点，支持音色参数 ==========
@app.post("/chat/text-to-speech")
async def text_to_speech_only(request: TextToSpeechRequest):
    audio_bytes = await audio_handler.text_to_speech(request.text, request.voice)
    audio_b64 = audio_handler.audio_to_base64(audio_bytes) if audio_bytes else ""
    return {"audio_response": audio_b64}

@app.post("/chat/voice")
async def voice_chat(
    audio: UploadFile = File(...),
    user_id: str = Form("default_user"),
    session_id: Optional[int] = Form(None)
):
    try:
        audio_bytes = await audio.read()
        user_text = audio_handler.speech_to_text(audio_bytes)
        if not user_text:
            return JSONResponse(status_code=400, content={"error": "无法识别语音"})

        chat_req = ChatRequest(message=user_text, user_id=user_id, session_id=session_id)
        chat_res = await text_chat(chat_req)
        ai_text = chat_res.response

        # 使用默认音色（或可以从请求中获取，这里简单使用默认）
        audio_out = await audio_handler.text_to_speech(ai_text)   # 使用默认音色
        audio_b64 = audio_handler.audio_to_base64(audio_out) if audio_out else ""

        return {
            "recognized_text": user_text,
            "ai_response": ai_text,
            "audio_base64": audio_b64,
            "session_id": chat_res.session_id
        }
    except Exception as e:
        logger.error(f"语音聊天错误: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/web", response_class=HTMLResponse)
async def web_chat(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False)