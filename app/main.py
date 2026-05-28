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
                max_tokens=250,
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

# ========== 高德天气执行函数 ==========
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
                return "天气服务配置错误，请联系管理员。"
            geo_url = "https://restapi.amap.com/v3/geocode/geo"
            geo_params = {"address": city, "key": GAODE_API_KEY}
            geo_resp = requests.get(geo_url, params=geo_params, timeout=10)
            geo_data = geo_resp.json()
            if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
                return f"未找到城市“{city}”，请尝试输入完整城市名（如“南京市”）。"
            adcode = geo_data["geocodes"][0]["adcode"]
            if date_type == "today":
                weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
                params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "base"}
                resp = requests.get(weather_url, params=params, timeout=10)
                data = resp.json()
                if data.get("status") != "1":
                    return f"无法获取 {city} 的实时天气信息。"
                live = data["lives"][0]
                result = f"{city} 当前天气：{live['weather']}，气温 {live['temperature']}℃，{live['winddirection']}风 {live['windpower']}级，湿度 {live['humidity']}%。"
                return result
            else:
                weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
                params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "all"}
                resp = requests.get(weather_url, params=params, timeout=10)
                data = resp.json()
                if data.get("status") != "1":
                    return f"无法获取 {city} 的天气预报信息。"
                forecasts = data["forecasts"][0]["casts"]
                idx_map = {"tomorrow": 1, "dayafter": 2}
                idx = idx_map.get(date_type, 0)
                day_label = {"tomorrow": "明天", "dayafter": "后天"}.get(date_type, "今天")
                if idx >= len(forecasts):
                    return f"无法获取 {city} {day_label} 的天气信息。"
                fc = forecasts[idx]
                result = f"{city}{day_label}天气：{fc['dayweather']}，白天温度 {fc['daytemp']}℃，夜间温度 {fc['nighttemp']}℃，{fc['daywind']}风 {fc['daypower']}级。"
                return result
        except Exception as e:
            logger.error(f"高德请求异常: {e}", exc_info=True)
            return "天气服务暂时不可用，请稍后再试。"

    elif tool_name == "web_search":
        query = arguments.get("query")
        if not query:
            return "请提供搜索词"
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=2))
            if results:
                snippets = [r['body'][:80] for r in results]
                return "搜索结果：\n" + "\n".join(snippets)
            else:
                return f"未找到关于 '{query}' 的信息"
        except ImportError:
            return "联网搜索需要安装 ddgs"
        except Exception as e:
            logger.error(f"搜索错误: {e}")
            return "搜索服务暂时不可用"

    return f"未知工具: {tool_name}"

# ========== 获取当前时间（精确到分钟，12小时制） ==========
def get_current_time() -> str:
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_str = weekdays[now.weekday()]
    hour = now.hour
    am_pm = "上午" if hour < 12 else "下午"
    if hour == 0:
        hour_12 = 12
    elif hour > 12:
        hour_12 = hour - 12
    else:
        hour_12 = hour
    minute = str(now.minute).zfill(2)
    return f"现在是{now.year}年{now.month}月{now.day}日 {am_pm}{hour_12}点{minute}分，{weekday_str}"

# ========== 隐式调用：提取地名和日期（默认南京） ==========
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
    return region, date_type

async def implicit_tool_call(user_msg: str) -> tuple[bool, str | None]:
    # 1. 天气
    if any(kw in user_msg for kw in ["天气", "温度", "气温"]):
        region, date_type = extract_region_and_date(user_msg)
        if not region:
            region = "南京"
        if date_type is None:
            date_type = "today"
        result = await execute_tool("get_weather", {"city": region, "date": date_type})
        return True, result

    # 2. 时间
    if any(kw in user_msg for kw in ["几点", "时间", "日期", "星期几", "几号"]):
        result = get_current_time()
        return True, result

    # 3. 搜索
    search_match = re.search(r"搜索(.+)", user_msg)
    if search_match:
        query = search_match.group(1).strip()
        if query:
            result = await execute_tool("web_search", {"query": query})
            return True, result

    return False, None

# ========== 系统提示词（要求精确时间） ==========
SYSTEM_PROMPT = (
    "你是小暖，一个面向老年人的语音陪聊助手。"
    "说话亲切、简短、易懂，使用“您”。"
    "多关心老人身体和心情。"
    "每次回复不超过2-3句话，方便语音播放。"
    "涉及天气时，使用系统提供的实时数据，用口语转述。"
    "回答天气问题时，请完整说出所有数据：天气状况、温度、湿度、风力等，不要遗漏。"
    "如果系统提示默认查询南京，请按南京的数据回答。"
    "当用户询问时间、日期、星期几时，请直接说出系统提供的具体时间，精确到分钟，不要做近似或四舍五入。"
    "时间回答示例：现在是下午2点26分，星期四。"
    "不要使用括号描述动作或语气。"
)

# ========== 硬校验：防止模型幻觉（天气/时间，并确保时间精确） ==========
def validate_and_fix_reply(user_msg: str, ai_reply: str, tool_result: str | None) -> str:
    # 天气校验
    if any(kw in user_msg for kw in ["天气", "温度", "气温"]) and tool_result:
        has_temp = bool(re.search(r'\d+\s*[℃度]', ai_reply))
        has_humidity = "湿度" in ai_reply
        has_wind = "风" in ai_reply
        if not (has_temp and has_humidity and has_wind):
            return f"好的，{tool_result}。您要注意天气变化，保重身体哦。"

    # 时间校验：必须包含具体的几点几分
    if any(kw in user_msg for kw in ["几点", "时间", "日期", "星期几", "几号"]) and tool_result:
        # 检查是否有“X点Y分”或“X:Y”的精确分钟表示
        has_precise_time = bool(re.search(r'\d+点\d{1,2}分', ai_reply)) or bool(re.search(r'\d+:\d{2}', ai_reply))
        if not has_precise_time:
            return f"{tool_result}，您有什么安排吗？"

    return ai_reply

# ========== 全局实例 ==========
deepseek_service = None
audio_handler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global deepseek_service, audio_handler
    logger.info("初始化智能体...")
    init_db()
    await database.connect()
    deepseek_service = DeepSeekService()
    audio_handler = AudioHandler()
    logger.info("所有服务初始化完成")
    yield
    await database.disconnect()

app = FastAPI(title="语音陪聊智能体", version="4.5", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
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
        s["preview"] = (last_msg["content"][:50] + "...") if last_msg and len(last_msg["content"]) > 50 else (last_msg["content"] if last_msg else "暂无消息")
    return {"sessions": sessions}

@app.get("/session/{session_id}/messages")
async def get_messages(session_id: int):
    return {"messages": await get_session_messages(session_id)}

@app.delete("/session/{session_id}")
async def del_session(session_id: int, user_id: str = "default_user"):
    if not await delete_session(session_id, user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}

@app.delete("/conversation/message/{message_id}")
async def delete_message_api(message_id: int, user_id: str = "default_user"):
    if not await delete_message_by_id(message_id, user_id):
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "deleted"}

@app.get("/voices")
async def get_voices():
    return {"voices": await audio_handler.get_voices()}

# ========== 核心聊天接口（含幻觉校验） ==========
@app.post("/chat/text", response_model=ChatResponse)
async def text_chat(request: ChatRequest):
    user_id = request.user_id
    user_msg = request.message

    implicit_triggered, tool_result = await implicit_tool_call(user_msg)

    if request.session_id is None:
        session_id = await create_session(user_id, "新对话")
    else:
        session_id = request.session_id

    history = await get_session_messages(session_id, limit=4)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_msg})

    final_reply = ""

    if implicit_triggered:
        messages.append({"role": "assistant", "content": f"【实时信息】{tool_result}"})
        assistant_message = deepseek_service.chat(messages, tools=None, tool_choice="none")
        final_reply = assistant_message.content
    else:
        MAX_TOOL_ROUNDS = 1
        tool_round = 0
        assistant_message = deepseek_service.chat(messages, tools=TOOLS, tool_choice="auto")
        messages.append(assistant_message.model_dump() if hasattr(assistant_message, 'model_dump') else {"role": "assistant", "content": assistant_message.content})

        while assistant_message.tool_calls and tool_round < MAX_TOOL_ROUNDS:
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                tool_result = await execute_tool(tool_name, arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            tool_round += 1
            assistant_message = deepseek_service.chat(messages, tools=TOOLS, tool_choice="auto")
            messages.append(assistant_message.model_dump() if hasattr(assistant_message, 'model_dump') else {"role": "assistant", "content": assistant_message.content})

        final_reply = assistant_message.content if assistant_message.content else "抱歉，我无法处理当前请求。"

    # 防幻觉硬校验
    final_reply = validate_and_fix_reply(user_msg, final_reply, tool_result if implicit_triggered else None)

    await save_message(user_id, session_id, "user", user_msg)
    await save_message(user_id, session_id, "assistant", final_reply)

    if len(history) == 0:
        new_title = user_msg[:30] + ("..." if len(user_msg) > 30 else "")
        await database.execute("UPDATE sessions SET title = :title WHERE id = :session_id",
                               values={"title": new_title, "session_id": session_id})

    return ChatResponse(response=final_reply, session_id=session_id)

# ========== 语音接口 ==========
@app.post("/chat/text-to-speech")
async def text_to_speech_only(request: TextToSpeechRequest):
    audio_bytes = await audio_handler.text_to_speech(request.text, request.voice)
    audio_b64 = audio_handler.audio_to_base64(audio_bytes) if audio_bytes else ""
    return {"audio_response": audio_b64}

@app.post("/chat/voice")
async def voice_chat(audio: UploadFile = File(...), user_id: str = Form("default_user"), session_id: Optional[int] = Form(None)):
    try:
        audio_bytes = await audio.read()
        user_text = audio_handler.speech_to_text(audio_bytes)
        if not user_text:
            return JSONResponse(status_code=400, content={"error": "无法识别语音"})
        chat_req = ChatRequest(message=user_text, user_id=user_id, session_id=session_id)
        chat_res = await text_chat(chat_req)
        audio_out = await audio_handler.text_to_speech(chat_res.response)
        audio_b64 = audio_handler.audio_to_base64(audio_out) if audio_out else ""
        return {"recognized_text": user_text, "ai_response": chat_res.response, "audio_base64": audio_b64, "session_id": chat_res.session_id}
    except Exception as e:
        logger.error(f"语音聊天错误: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/web", response_class=HTMLResponse)
async def web_chat(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False)