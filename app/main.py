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
import json
import re
import requests
from datetime import datetime, timedelta

from app.audio_handler import AudioHandler
from app.database import (
    database, init_db, save_message, get_session_messages, get_user_sessions,
    create_session, delete_session, delete_message_by_id, get_session_last_message
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
    voice: Optional[str] = "zh-CN-XiaoxiaoNeural"

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

    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None, tool_choice: str = "auto") -> any:
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.8,
                max_tokens=800,
                tools=tools,
                tool_choice=tool_choice
            )
            return response.choices[0].message
        except Exception as e:
            logger.error(f"DeepSeek API错误: {e}")
            class DummyMessage:
                def __init__(self, content):
                    self.content = content
                    self.tool_calls = None
            return DummyMessage("抱歉，我现在遇到了一点问题。")

# ========== 工具定义 ==========
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定地点的天气信息，支持城市或区域名称，例如'南京'、'江宁'、'北京海淀'。可以指定日期：'今天'、'明天'、'后天'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市或区域名称，例如：南京、江宁、北京海淀",
                    },
                    "date": {
                        "type": "string",
                        "description": "日期，可选值：'today', 'tomorrow', 'dayafter'，默认为'today'",
                    }
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

# ========== 高德天气执行函数（带完整日志） ==========
async def execute_tool(tool_name: str, arguments: dict) -> str:
    logger.info(f"[TOOL_CALL] 工具名称: {tool_name}, 参数: {arguments}")
    if tool_name == "get_weather":
        city = arguments.get("city")
        date_type = arguments.get("date", "today")
        if not city:
            return "请提供城市或区域名称"
        try:
            GAODE_API_KEY = os.getenv("GAODE_API_KEY")
            if not GAODE_API_KEY:
                logger.error("[REAL_API_CALL] GAODE_API_KEY 未设置")
                return "天气服务配置错误，请联系管理员。"
            
            # 第一步：地理编码
            geo_url = "https://restapi.amap.com/v3/geocode/geo"
            geo_params = {"address": city, "key": GAODE_API_KEY}
            logger.info(f"[REAL_API_CALL] 请求高德地理编码: {geo_url}?address={city}&key=******")
            geo_resp = requests.get(geo_url, params=geo_params, timeout=10)
            geo_data = geo_resp.json()
            logger.info(f"[REAL_API_CALL] 高德地理编码响应: {geo_data}")
            
            if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
                return f"未找到城市“{city}”，请尝试输入完整城市名（如“南京市”）。"
            adcode = geo_data["geocodes"][0]["adcode"]
            logger.info(f"[REAL_API_CALL] 获取到 adcode: {adcode}")
            
            # 第二步：天气查询
            if date_type == "today":
                weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
                params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "base"}
                logger.info(f"[REAL_API_CALL] 请求高德实时天气: {weather_url}?city={adcode}&key=******")
                resp = requests.get(weather_url, params=params, timeout=10)
                data = resp.json()
                logger.info(f"[REAL_API_CALL] 高德实时天气响应: {data}")
                if data.get("status") != "1":
                    return f"无法获取 {city} 的实时天气信息。"
                live = data["lives"][0]
                condition = live["weather"]
                temp = live["temperature"]
                wind_dir = live["winddirection"]
                wind_power = live["windpower"]
                humidity = live["humidity"]
                result = f"{city} 当前天气：{condition}，气温 {temp}℃，{wind_dir}风 {wind_power}级，湿度 {humidity}%。"
                logger.info(f"[TOOL_RESULT] {result}")
                return result
            else:
                weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
                params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "all"}
                logger.info(f"[REAL_API_CALL] 请求高德天气预报: {weather_url}?city={adcode}&key=******")
                resp = requests.get(weather_url, params=params, timeout=10)
                data = resp.json()
                logger.info(f"[REAL_API_CALL] 高德天气预报响应: {data}")
                if data.get("status") != "1":
                    return f"无法获取 {city} 的天气预报信息。"
                forecasts = data["forecasts"][0]["casts"]
                if date_type == "tomorrow":
                    idx = 1
                    day_label = "明天"
                elif date_type == "dayafter":
                    idx = 2
                    day_label = "后天"
                else:
                    idx = 0
                    day_label = "今天"
                if idx >= len(forecasts):
                    return f"无法获取 {city} {day_label} 的天气信息。"
                fc = forecasts[idx]
                condition_day = fc["dayweather"]
                temp_day = fc["daytemp"]
                night_temp = fc["nighttemp"]
                wind_dir = fc["daywind"]
                wind_power = fc["daypower"]
                result = f"{city}{day_label}天气：{condition_day}，白天温度 {temp_day}℃，夜间温度 {night_temp}℃，{wind_dir}风 {wind_power}级。"
                logger.info(f"[TOOL_RESULT] {result}")
                return result
        except Exception as e:
            logger.error(f"[REAL_API_CALL] 高德请求异常: {e}", exc_info=True)
            return "天气服务暂时不可用，请稍后再试。"
    elif tool_name == "web_search":
        # 联网搜索（省略，保持不变）
        query = arguments.get("query")
        if not query:
            return "请提供搜索词"
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=3))
                if results:
                    snippets = [r['body'] for r in results]
                    return "搜索结果：\n" + "\n".join(snippets)
                else:
                    return f"未找到关于 '{query}' 的信息"
        except ImportError:
            return "联网搜索需要安装 ddgs"
        except Exception as e:
            logger.error(f"搜索错误: {e}")
            return "搜索服务暂时不可用"
    else:
        return f"未知工具: {tool_name}"

# ========== 隐式调用：提取地名和日期 ==========
def extract_region_and_date(text: str) -> tuple[Optional[str], Optional[str]]:
    date_map = {"今天": "today", "明天": "tomorrow", "后天": "dayafter", "明日": "tomorrow"}
    date_type = None
    for kw, dt in date_map.items():
        if kw in text:
            date_type = dt
            break
    for kw in date_map.keys():
        text = text.replace(kw, "")
    match = re.search(r"([\u4e00-\u9fa5]{2,})", text)
    region = match.group(1) if match else None
    logger.info(f"[EXTRACT] 提取到地区: {region}, 日期: {date_type}")
    return region, date_type

async def implicit_tool_call(user_msg: str) -> tuple[bool, str | None]:
    logger.info(f"[IMPLICIT] 收到用户消息: {user_msg}")
    if any(kw in user_msg for kw in ["天气", "温度", "气温"]):
        region, date_type = extract_region_and_date(user_msg)
        if region:
            logger.info(f"[IMPLICIT] 触发天气查询: {region}, 日期: {date_type or 'today'}")
            if date_type is None:
                date_type = "today"
            result = await execute_tool("get_weather", {"city": region, "date": date_type})
            return True, result
    search_match = re.search(r"搜索(.+)", user_msg)
    if search_match:
        query = search_match.group(1).strip()
        if query:
            logger.info(f"[IMPLICIT] 触发搜索: {query}")
            result = await execute_tool("web_search", {"query": query})
            return True, result
    return False, None

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

重要格式：直接输出对话内容，绝对不要使用括号描述表情、动作或语气。

当用户询问天气时，系统会提供实时数据或预报数据。请用自然、口语化的方式回答。例如：
- “南京今天天气多云，温度24度，有东风3级，湿度65%，挺舒适的。”
- “明天北京天气晴，白天温度28℃，夜间20℃，北风2级，适合出游。”"""

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

@app.get("/voices")
async def get_voices():
    voices = await audio_handler.get_voices()
    return {"voices": voices}

@app.post("/chat/text", response_model=ChatResponse)
async def text_chat(request: ChatRequest):
    user_id = request.user_id
    user_msg = request.message

    implicit_triggered, tool_result = await implicit_tool_call(user_msg)

    if request.session_id is None:
        session_id = await create_session(user_id, "新对话")
    else:
        session_id = request.session_id

    history = await get_session_messages(session_id, limit=10)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_msg})

    final_reply = ""

    if implicit_triggered:
        messages.append({"role": "assistant", "content": f"【系统提示】已获取到以下实时信息：{tool_result}"})
        assistant_message = deepseek_service.chat(messages, tools=None, tool_choice="none")
        final_reply = assistant_message.content
    else:
        assistant_message = deepseek_service.chat(messages, tools=TOOLS, tool_choice="auto")
        messages.append(assistant_message.model_dump() if hasattr(assistant_message, 'model_dump') else {"role": "assistant", "content": assistant_message.content})

        while assistant_message.tool_calls:
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                tool_result = await execute_tool(tool_name, arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            assistant_message = deepseek_service.chat(messages, tools=TOOLS, tool_choice="auto")
            messages.append(assistant_message.model_dump() if hasattr(assistant_message, 'model_dump') else {"role": "assistant", "content": assistant_message.content})

        final_reply = assistant_message.content if assistant_message.content else "抱歉，我无法处理当前请求。"

    await save_message(user_id, session_id, "user", user_msg)
    await save_message(user_id, session_id, "assistant", final_reply)

    if len(history) == 0:
        new_title = user_msg[:30] + ("..." if len(user_msg) > 30 else "")
        await database.execute("UPDATE sessions SET title = :title WHERE id = :session_id",
                               values={"title": new_title, "session_id": session_id})

    return ChatResponse(response=final_reply, session_id=session_id)

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
        audio_out = await audio_handler.text_to_speech(ai_text)
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