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
                    "city": {"type": "string", "description": "城市或区域名称，例如：南京、江宁、北京海淀"},
                    "date": {"type": "string", "description": "日期，可选值：'today', 'tomorrow', 'dayafter'，默认为'today'"}
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
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"],
            },
        },
    }
]

# ========== 高德天气执行函数（带行政区划过滤） ==========
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
            
            # 过滤行政区划：优先选择中国大陆的区/县级，排除台湾地区（除非明确输入“新北市”）
            geocodes = geo_data["geocodes"]
            # 如果用户输入包含“区”字，优先匹配级别为“区”的结果
            selected = None
            if "区" in city:
                for g in geocodes:
                    if "区" in g.get("level", "") and "台湾" not in g.get("province", ""):
                        selected = g
                        break
            if not selected:
                # 默认取第一个结果，但排除台湾地区的“新北市”等
                for g in geocodes:
                    if "台湾" in g.get("province", "") and "新北" not in city:
                        continue
                    selected = g
                    break
            if not selected:
                selected = geocodes[0]
            
            adcode = selected["adcode"]
            formatted_address = selected.get("formatted_address", city)
            logger.info(f"[GEO] 用户输入: {city}, 匹配到: {formatted_address} (adcode: {adcode})")
            
            if date_type == "today":
                weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
                params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "base"}
                resp = requests.get(weather_url, params=params, timeout=10)
                data = resp.json()
                if data.get("status") != "1":
                    return f"无法获取 {formatted_address} 的实时天气信息。"
                live = data["lives"][0]
                result = f"{formatted_address} 当前天气：{live['weather']}，气温 {live['temperature']}℃，{live['winddirection']}风 {live['windpower']}级，湿度 {live['humidity']}%。"
                return result
            else:
                weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
                params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "all"}
                resp = requests.get(weather_url, params=params, timeout=10)
                data = resp.json()
                if data.get("status") != "1":
                    return f"无法获取 {formatted_address} 的天气预报信息。"
                forecasts = data["forecasts"][0]["casts"]
                idx_map = {"tomorrow": 1, "dayafter": 2}
                idx = idx_map.get(date_type, 0)
                day_label = {"tomorrow": "明天", "dayafter": "后天"}.get(date_type, "今天")
                if idx >= len(forecasts):
                    return f"无法获取 {formatted_address} {day_label} 的天气信息。"
                fc = forecasts[idx]
                result = f"{formatted_address}{day_label}天气：{fc['dayweather']}，白天温度 {fc['daytemp']}℃，夜间温度 {fc['nighttemp']}℃，{fc['daywind']}风 {fc['daypower']}级。"
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

# ========== 时间辅助函数 ==========
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

def get_relative_date(offset_days: int) -> str:
    target = datetime.now() + timedelta(days=offset_days)
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_str = weekdays[target.weekday()]
    if offset_days == 1:
        label = "明天"
    elif offset_days == 2:
        label = "后天"
    else:
        label = f"{offset_days}天后"
    return f"{label}是{target.month}月{target.day}日，{weekday_str}。"

# ========== 天气辅助函数 ==========
WEATHER_KEYWORDS = {"天气", "温度", "气温"}
EXCLUDE_WORDS = {
    "现在", "请问", "知道", "那里", "这里", "什么", "今天", "明天", "后天", "昨日", "明日",
    "查询", "预报", "风力", "湿度", "气象", "几点", "时间", "日期", "星期几", "几号",
    "怎么", "怎样", "怎么样", "如何", "哪儿", "哪里", "哪", "什么样", "为何", "为什么",
    "是", "的", "了", "吗", "呢", "吧", "啊", "呀", "嘛", "哦", "嗯", "么",
    "一个", "一下", "一些", "这个", "那个", "哪个", "什么样", "如何", "多少",
    "当前", "咋样"
}

def clean_region(raw: str) -> str:
    if not raw:
        return ""
    for w in WEATHER_KEYWORDS:
        raw = raw.replace(w, "")
    sorted_exclude = sorted(EXCLUDE_WORDS, key=len, reverse=True)
    changed = True
    while changed:
        changed = False
        for word in sorted_exclude:
            if word in raw:
                raw = raw.replace(word, "")
                changed = True
    raw = re.sub(r'[^\u4e00-\u9fa5]', '', raw)
    match = re.search(r"([\u4e00-\u9fa5]{2,})", raw)
    return match.group(1) if match else ""

def extract_region_and_date(text: str) -> tuple[Optional[str], Optional[str]]:
    date_map = {"今天": "today", "明天": "tomorrow", "后天": "dayafter", "明日": "tomorrow"}
    date_type = None
    for kw, dt in date_map.items():
        if kw in text:
            date_type = dt
            break
    clean_text = text
    for kw in date_map.keys():
        clean_text = clean_text.replace(kw, "")
    match = re.search(r"([\u4e00-\u9fa5]{2,})", clean_text)
    region = match.group(1) if match else None
    return region, date_type

async def get_temperature_for_date(city: str, date_type: str, temp_type: str) -> str:
    GAODE_API_KEY = os.getenv("GAODE_API_KEY")
    if not GAODE_API_KEY:
        return "天气服务配置错误"
    geo_url = "https://restapi.amap.com/v3/geocode/geo"
    geo_params = {"address": city, "key": GAODE_API_KEY}
    geo_resp = requests.get(geo_url, params=geo_params, timeout=10)
    geo_data = geo_resp.json()
    if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
        return f"未找到城市“{city}”"
    
    # 同样进行过滤
    geocodes = geo_data["geocodes"]
    selected = None
    if "区" in city:
        for g in geocodes:
            if "区" in g.get("level", "") and "台湾" not in g.get("province", ""):
                selected = g
                break
    if not selected:
        for g in geocodes:
            if "台湾" in g.get("province", "") and "新北" not in city:
                continue
            selected = g
            break
    if not selected:
        selected = geocodes[0]
    
    adcode = selected["adcode"]
    formatted_address = selected.get("formatted_address", city)
    logger.info(f"[GEO_TEMP] 用户输入: {city}, 匹配到: {formatted_address}")
    
    weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
    params = {"city": adcode, "key": GAODE_API_KEY, "extensions": "all"}
    resp = requests.get(weather_url, params=params, timeout=10)
    data = resp.json()
    if data.get("status") != "1":
        return f"无法获取 {formatted_address} 的天气信息"
    forecasts = data["forecasts"][0]["casts"]
    idx_map = {"today": 0, "tomorrow": 1, "dayafter": 2}
    idx = idx_map.get(date_type, 0)
    if idx >= len(forecasts):
        return f"无法获取 {formatted_address} 指定日期的天气"
    fc = forecasts[idx]
    day_label = {"today": "今天", "tomorrow": "明天", "dayafter": "后天"}.get(date_type, "今天")
    if temp_type == "max":
        temp = fc.get('daytemp', '?')
        return f"{formatted_address}{day_label}的最高温度是 {temp}℃。"
    else:
        temp = fc.get('nighttemp', '?')
        return f"{formatted_address}{day_label}的最低温度是 {temp}℃。"

# ========== 核心隐式调用（年份、日期、天气） ==========
async def implicit_tool_call(user_msg: str) -> tuple[bool, str | None]:
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    
    # 年份查询（支持明年、后年、前年）
    year_keywords = {
        "今年": 0,
        "明年": 1,
        "后年": 2,
        "前年": -1,
        "大前年": -2
    }
    for kw, offset in year_keywords.items():
        if kw in user_msg and any(q in user_msg for q in ["哪一年", "年份", "哪年"]):
            target_year = now.year + offset
            return True, f"{kw}是{target_year}年。"
    if any(kw in user_msg for kw in ["哪一年", "今年哪一年", "年份"]):
        return True, f"今年是{now.year}年。"
    
    # 月份查询
    if any(kw in user_msg for kw in ["几月", "月份"]):
        return True, f"现在是{now.month}月。"
    
    # 日期/星期/时间查询
    time_keywords = ["几点", "时间", "日期", "星期几", "几号", "周几"]
    if any(kw in user_msg for kw in time_keywords):
        if "明天" in user_msg:
            return True, get_relative_date(1)
        elif "后天" in user_msg:
            return True, get_relative_date(2)
        else:
            if any(kw in user_msg for kw in ["几点", "时间"]):
                hour = now.hour
                am_pm = "上午" if hour < 12 else "下午"
                if hour == 0:
                    hour_12 = 12
                elif hour > 12:
                    hour_12 = hour - 12
                else:
                    hour_12 = hour
                minute = str(now.minute).zfill(2)
                return True, f"现在是{now.year}年{now.month}月{now.day}日 {am_pm}{hour_12}点{minute}分，{weekdays[now.weekday()]}。"
            else:
                return True, f"今天是{now.year}年{now.month}月{now.day}日，{weekdays[now.weekday()]}。"

    # 1. 天气
    if any(kw in user_msg for kw in WEATHER_KEYWORDS):
        temp_type = None
        if "最低温度" in user_msg or "最低气温" in user_msg:
            temp_type = "min"
        elif "最高温度" in user_msg or "最高气温" in user_msg:
            temp_type = "max"
        
        raw_region, date_type = extract_region_and_date(user_msg)
        region = clean_region(raw_region) if raw_region else None
        if not region:
            region = "南京"
        if not date_type:
            date_type = "today"
        
        if temp_type:
            result = await get_temperature_for_date(region, date_type, temp_type)
            if "未找到城市" in result and region != "南京":
                result = await get_temperature_for_date("南京", date_type, temp_type)
            return True, result
        
        result = await execute_tool("get_weather", {"city": region, "date": date_type})
        if "未找到城市" in result and region != "南京":
            result = await execute_tool("get_weather", {"city": "南京", "date": date_type})
        return True, result

    # 2. 搜索
    search_match = re.search(r"搜索(.+)", user_msg)
    if search_match:
        query = search_match.group(1).strip()
        if query:
            result = await execute_tool("web_search", {"query": query})
            return True, result

    return False, None

# ========== 系统提示词 ==========
SYSTEM_PROMPT = (
    "你是小暖，一个面向老年人的语音陪聊助手。"
    "说话亲切、简短、易懂，使用“您”。"
    "多关心老人身体和心情。"
    "每次回复不超过2-3句话，方便语音播放。"
    "涉及天气时，使用系统提供的实时数据，用口语转述。"
    "回答天气问题时，请完整说出所有数据：天气状况、温度、湿度、风力等，不要遗漏。"
    "如果系统提示默认查询南京，请按南京的数据回答。"
    "不要使用括号描述动作或语气。"
)

def build_direct_reply(user_msg: str, tool_result: str) -> str:
    time_indicators = ["几点", "时间", "日期", "星期几", "几号", "周几", "哪一年", "年份", "几月", "月份"]
    if any(kw in user_msg for kw in time_indicators):
        return tool_result
    if any(kw in user_msg for kw in WEATHER_KEYWORDS):
        if "最高温度" in tool_result or "最低温度" in tool_result:
            return tool_result
        return f"好的，{tool_result}。您要注意天气变化，保重身体哦。"
    return tool_result

def validate_and_fix_reply(user_msg: str, ai_reply: str, tool_result: str | None) -> str:
    time_indicators = ["几点", "时间", "日期", "星期几", "几号", "周几", "哪一年", "年份", "几月", "月份"]
    if any(kw in user_msg for kw in time_indicators):
        return ai_reply
    if any(kw in user_msg for kw in WEATHER_KEYWORDS) and tool_result:
        if "错误" not in tool_result and "不可用" not in tool_result and "未找到" not in tool_result:
            has_temp = bool(re.search(r'\d+\s*[℃度]', ai_reply))
            has_humidity = "湿度" in ai_reply
            has_wind = "风" in ai_reply
            if not (has_temp and has_humidity and has_wind):
                return f"好的，{tool_result}。您要注意天气变化，保重身体哦。"
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

app = FastAPI(title="语音陪聊智能体", version="6.6", lifespan=lifespan)
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

# ========== 核心聊天接口（强制历史截断） ==========
@app.post("/chat/text", response_model=ChatResponse)
async def text_chat(request: ChatRequest):
    user_id = request.user_id
    user_msg = request.message

    logger.info(f"[REQUEST] user={user_id}, session={request.session_id}, msg={user_msg[:50]}")

    implicit_triggered, tool_result = await implicit_tool_call(user_msg)

    if request.session_id is None:
        session_id = await create_session(user_id, "新对话")
        logger.info(f"[NEW_SESSION] 创建新会话 {session_id}")
    else:
        session_id = request.session_id

    # 强制只取最近 MAX_HISTORY 条消息（防止 token 爆炸）
    MAX_HISTORY = 10   # 您可以改为 4 或 6
    history = await get_session_messages(session_id, limit=MAX_HISTORY)
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        logger.warning(f"[HISTORY] 会话 {session_id} 历史消息超过 {MAX_HISTORY} 条，已截断至最后 {MAX_HISTORY} 条")
    logger.info(f"[HISTORY] 加载了 {len(history)} 条历史消息")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_msg})

    # 估算 token 数（粗略）
    estimated_tokens = sum(len(m.get("content", "")) for m in messages) // 2
    if estimated_tokens > 10000:
        logger.warning(f"[TOKEN_EST] 当前 messages 估算 token 数 {estimated_tokens}，可能超过限制！")

    final_reply = ""

    if implicit_triggered:
        if tool_result and "错误" not in tool_result and "不可用" not in tool_result and "未找到" not in tool_result:
            final_reply = build_direct_reply(user_msg, tool_result)
            logger.info(f"[IMPLICIT] 直接返回工具结果，未调用模型")
        else:
            messages.append({"role": "assistant", "content": f"【实时信息】{tool_result}"})
            assistant_message = deepseek_service.chat(messages, tools=None, tool_choice="none")
            final_reply = assistant_message.content
            logger.info(f"[MODEL_CALL] 调用了模型（隐式降级）")
    else:
        MAX_TOOL_ROUNDS = 1
        tool_round = 0
        assistant_message = deepseek_service.chat(messages, tools=TOOLS, tool_choice="auto")
        messages.append(assistant_message.model_dump() if hasattr(assistant_message, 'model_dump') else {"role": "assistant", "content": assistant_message.content})
        logger.info(f"[MODEL_CALL] 调用了模型（显式工具）")

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
        final_reply = validate_and_fix_reply(user_msg, final_reply, tool_result if 'tool_result' in locals() else None)

    await save_message(user_id, session_id, "user", user_msg)
    await save_message(user_id, session_id, "assistant", final_reply)

    if len(history) == 0:
        new_title = user_msg[:30] + ("..." if len(user_msg) > 30 else "")
        await database.execute("UPDATE sessions SET title = :title WHERE id = :session_id",
                               values={"title": new_title, "session_id": session_id})

    logger.info(f"[RESPONSE] 回复长度: {len(final_reply)} 字符")
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