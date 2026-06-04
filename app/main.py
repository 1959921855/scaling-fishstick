from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from openai import OpenAI
from dotenv import load_dotenv
import uvicorn
import os
import logging
import json
import re
import asyncio
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

from app.audio_handler import AudioHandler
from app.database import (
    database, init_db, save_message, get_session_messages, get_user_sessions,
    create_session, delete_session, delete_message_by_id, get_session_last_message
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 全局 HTTP 客户端
http_client: Optional[httpx.AsyncClient] = None

search_cache = {}

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

# ==================== 模型服务（带重试，错误返回前端） ====================
class ModelService:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            logger.error("❌ DEEPSEEK_API_KEY 环境变量未设置！")
            raise ValueError("DEEPSEEK_API_KEY not found")
        logger.info(f"✅ DeepSeek API Key 已加载: {self.api_key[:8]}****")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com/v1",
            timeout=20.0
        )

    def chat(self, messages, tools=None, tool_choice="auto"):
        last_exception = None
        for attempt in range(2):
            try:
                logger.info(f"🔵 DeepSeek 调用尝试 {attempt+1}，消息数: {len(messages)}，工具数: {len(tools) if tools else 0}")
                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    temperature=0.7,
                    max_tokens=500,
                    tools=tools,
                    tool_choice=tool_choice
                )
                logger.info("✅ DeepSeek 调用成功")
                return response.choices[0].message
            except Exception as e:
                last_exception = e
                logger.error(f"❌ DeepSeek 调用失败 (尝试 {attempt+1}): 类型={type(e).__name__}, 详情={str(e)}")
                if attempt == 0:
                    import time
                    time.sleep(1)
        # 将错误详情返回给前端
        error_detail = f"{type(last_exception).__name__}: {str(last_exception)}" if last_exception else "未知错误"
        logger.error(f"🚨 DeepSeek 全部重试失败: {error_detail}")
        class Dummy:
            def __init__(self, msg):
                self.content = f"抱歉，我现在遇到网络问题，无法回答。\n\n【调试信息】\n{msg}"
                self.tool_calls = None
        return Dummy(error_detail)

model_service = ModelService()

# ==================== 工具定义 ====================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定地点的天气信息。未指定城市时默认查询南京。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市或区域名称"},
                    "date": {"type": "string", "description": "日期：'today'、'tomorrow'、'dayafter'，默认'today'"}
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息，获取最新资料",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "搜索最新新闻资讯",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "新闻关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": "查询股票或大盘指数实时行情，支持A股、港股、美股",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码或名称"},
                    "date": {"type": "string", "description": "历史日期，如'昨天'、'前天'"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前时间、日期、星期几",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# ==================== 搜索（Tavily，带重试） ====================
async def web_search(query: str, max_results: int = 5) -> List[tuple]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.error("❌ TAVILY_API_KEY 未设置")
        return [("搜索服务未配置", "请联系管理员设置 Tavily API Key", "")]

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(2):
        try:
            logger.info(f"🔍 Tavily 搜索请求: {query[:30]}...")
            response = await http_client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            results = []
            for item in data.get("results", []):
                title = item.get("title", "")
                content = item.get("content", "")
                url_link = item.get("url", "")
                if content:
                    results.append((title, content, url_link))
            logger.info(f"✅ Tavily 返回 {len(results)} 条结果")
            return results if results else [("未找到相关信息", "请换个关键词试试", "")]
        except Exception as e:
            logger.error(f"❌ Tavily 失败 (尝试 {attempt+1}): {e}")
            if attempt == 0:
                await asyncio.sleep(1)
    logger.error("🚨 Tavily 搜索全部失败")
    return [("搜索失败", "暂时无法完成搜索，请稍后再试", "")]

# ==================== 天气（高德，带重试） ====================
async def get_weather_tool(city: str, date_type: str = "today") -> str:
    if not city or not city.strip():
        city = "南京"
    logger.info(f"🌤️ 天气查询城市: {city}")
    GAODE_API_KEY = os.getenv("GAODE_API_KEY")
    if not GAODE_API_KEY:
        logger.error("❌ GAODE_API_KEY 未设置")
        return "天气服务配置错误。"

    async def request_geo():
        url = "https://restapi.amap.com/v3/geocode/geo"
        return await http_client.get(url, params={"address": city, "key": GAODE_API_KEY})

    async def request_weather(adcode, ext):
        url = "https://restapi.amap.com/v3/weather/weatherInfo"
        params = {"city": adcode, "key": GAODE_API_KEY, "extensions": ext}
        return await http_client.get(url, params=params)

    try:
        # 地理编码（重试）
        geo_resp = None
        for attempt in range(2):
            try:
                logger.info(f"📍 地理编码尝试 {attempt+1}")
                geo_resp = await request_geo()
                break
            except Exception as e:
                logger.error(f"❌ 地理编码失败 (尝试 {attempt+1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(0.5)
        if geo_resp is None:
            return "天气查询超时，请稍后再试。"

        geo_data = geo_resp.json()
        if geo_data.get("status") != "1" or not geo_data.get("geocodes"):
            return f"未找到城市“{city}”，请尝试输入完整城市名。"
        geocode = geo_data["geocodes"][0]
        adcode = geocode["adcode"]
        location_name = geocode.get("formatted_address", city)

        # 天气查询（重试）
        ext = "base" if date_type == "today" else "all"
        weather_resp = None
        for attempt in range(2):
            try:
                logger.info(f"🌦️ 天气请求尝试 {attempt+1}")
                weather_resp = await request_weather(adcode, ext)
                break
            except Exception as e:
                logger.error(f"❌ 天气请求失败 (尝试 {attempt+1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(0.5)
        if weather_resp is None:
            return "天气信息获取超时，请稍后再试。"

        data = weather_resp.json()
        if data.get("status") != "1":
            return f"无法获取 {location_name} 天气。"
        
        if date_type == "today":
            live = data["lives"][0]
            return f"{location_name} 当前天气：{live['weather']}，气温 {live['temperature']}℃，{live['winddirection']}风 {live['windpower']}级，湿度 {live['humidity']}%。"
        else:
            forecasts = data["forecasts"][0]["casts"]
            idx_map = {"tomorrow": 1, "dayafter": 2}
            idx = idx_map.get(date_type, 0)
            day_label = {"tomorrow": "明天", "dayafter": "后天"}.get(date_type, "今天")
            if idx >= len(forecasts):
                return f"无法获取 {location_name} {day_label} 天气。"
            fc = forecasts[idx]
            return f"{location_name}{day_label}天气：{fc['dayweather']}，白天{fc['daytemp']}℃，夜间{fc['nighttemp']}℃，{fc['daywind']}风{fc['daypower']}级。"
    except Exception as e:
        logger.error(f"❌ 天气异常: {e}")
        return "天气服务暂时不可用。"

# ==================== 股票（已有重试） ====================
NAME_TO_CODE = {
    "工商银行": "sh601398", "建设银行": "sh601939", "农业银行": "sh601288",
    "中国银行": "sh601988", "招商银行": "sh600036", "交通银行": "sh601328",
    "邮储银行": "sh601658", "兴业银行": "sh601166", "浦发银行": "sh600000",
    "民生银行": "sh600016", "中信银行": "sh601998", "光大银行": "sh601818",
    "平安银行": "sz000001", "宁波银行": "sz002142", "江苏银行": "sh600919",
    "中国平安": "sh601318", "中国人寿": "sh601628", "中国太保": "sh601601",
    "新华保险": "sh601336", "中信证券": "sh600030", "华泰证券": "sh601688",
    "国泰君安": "sh601211", "海通证券": "sh600837", "贵州茅台": "sh600519",
    "五粮液": "sz000858", "泸州老窖": "sz000568", "洋河股份": "sz002304",
    "山西汾酒": "sh600809", "宁德时代": "sz300750", "比亚迪": "sz002594",
    "美的集团": "sz000333", "格力电器": "sz000651", "海康威视": "sz002415",
    "立讯精密": "sz002475", "京东方A": "sz000725", "中芯国际": "sh688981",
    "腾讯": "hk00700", "阿里巴巴": "hk09988", "美团": "hk03690",
    "中国石油": "sh601857", "中国石化": "sh600028", "中国神华": "sh601088",
    "中国建筑": "sh601668", "万华化学": "sh600309", "伊利股份": "sh600887",
    "海尔智家": "sh600690", "三一重工": "sh600031", "恒瑞医药": "sh600276",
    "迈瑞医疗": "sz300760", "药明康德": "sh603259", "茅台": "sh600519",
    "招商银": "sh600036", "工商银": "sh601398", "中国银": "sh601988",
    "建设银": "sh601939", "农业银": "sh601288", "交通银": "sh601328",
    "平安": "sz000001", "宁王": "sz300750", "迪王": "sz002594",
    "上证指数": "sh000001", "深证成指": "sz399001", "创业板指": "sz399006",
    "上证": "sh000001", "深证": "sz399001", "创业板": "sz399006",
}

def clean_stock_symbol(raw: str) -> str:
    if not raw: return ""
    cleaned = re.sub(r'昨天|前天|今日|明日|股票|股价|行情|查一下|的|了|吗|呢', '', raw).strip()
    if not cleaned: return raw
    if cleaned in NAME_TO_CODE: return cleaned
    for full_name in NAME_TO_CODE.keys():
        if full_name.startswith(cleaned): return full_name
    return cleaned

async def get_stock_quote(symbol: str, date: Optional[str] = None) -> str:
    if not symbol: return "请提供股票代码或名称"
    logger.info(f"📈 股票查询: {symbol}")
    original_symbol = symbol
    symbol = clean_stock_symbol(symbol)
    if symbol in NAME_TO_CODE: code = NAME_TO_CODE[symbol]
    else:
        code = symbol
        if symbol.isdigit() and len(symbol) == 6:
            if symbol.startswith('6'): code = f"sh{symbol}"
            else: code = f"sz{symbol}"
        elif symbol.isdigit() and len(symbol) == 5: code = f"hk{symbol}"
        else: code = symbol.upper()
    target_date = None
    if date:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if date == "昨天": target_date = today - timedelta(days=1)
        elif date == "前天": target_date = today - timedelta(days=2)
        else:
            try: target_date = datetime.strptime(date, "%Y-%m-%d").date()
            except: pass

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

    if code in ['sh000001', 'sz399001', 'sz399006']:
        if target_date: return f"📈 {original_symbol} 历史数据暂不支持，以下为实时行情：\n"
        url = f"https://hq.sinajs.cn/list={code}"
        try:
            resp = await http_client.get(url, headers=headers)
            text = resp.text
            if len(text) < 10: return f"未找到 {original_symbol} 的指数数据。"
            parts = text.split('"')
            if len(parts) < 2: return f"无法解析 {original_symbol} 数据。"
            fields = parts[1].split(',')
            if len(fields) < 10: return f"数据不完整，请稍后再试。"
            name = fields[0]
            cur = float(fields[3])
            yest = float(fields[2])
            chg = cur - yest
            chgp = (chg / yest) * 100 if yest else 0
            return f"{name}\n最新 {cur:.2f} 点\n涨跌 {chg:+.2f} ({chgp:+.2f}%)"
        except Exception as e:
            logger.error(f"指数查询异常: {e}")
            return "指数查询失败。"

    if target_date and YFINANCE_AVAILABLE:
        try:
            def query_history():
                yf_code = code
                if code.startswith('sh'): yf_code = code[2:] + ".SS"
                elif code.startswith('sz'): yf_code = code[2:] + ".SZ"
                elif code.startswith('hk'): yf_code = code[2:] + ".HK"
                ticker = yf.Ticker(yf_code)
                hist = ticker.history(start=target_date - timedelta(days=1), end=target_date + timedelta(days=1))
                if not hist.empty:
                    for idx, row in hist.iterrows():
                        if idx.date() == target_date:
                            return row['Close'], row['Open'], True
                return None, None, False

            close, open_price, ok = await asyncio.to_thread(query_history)
            if ok:
                change = close - open_price
                change_percent = (change / open_price) * 100 if open_price else 0
                return f"{symbol if symbol in NAME_TO_CODE else code} {target_date.strftime('%Y-%m-%d')}\n收盘价 {close:.2f} 元\n涨跌 {change:+.2f} ({change_percent:+.2f}%)"
            else:
                return f"未找到 {original_symbol} 在 {target_date.strftime('%Y-%m-%d')} 的交易数据（可能休市）。"
        except Exception as e:
            logger.error(f"历史股票查询异常: {e}")

    url = f"https://hq.sinajs.cn/list={code}"
    try:
        resp = await http_client.get(url, headers=headers)
        text = resp.text
        if len(text) < 10: return f"未找到股票 '{original_symbol}'，请检查代码或名称。"
        parts = text.split('"')
        if len(parts) < 2: return f"无法解析股票 '{original_symbol}' 数据。"
        fields = parts[1].split(',')
        if len(fields) < 10: return f"数据不完整，请稍后再试。"
        name = fields[0]
        cur = float(fields[3])
        yest = float(fields[2])
        chg = cur - yest
        chgp = (chg / yest) * 100 if yest else 0
        volume = int(float(fields[8])) if len(fields) > 8 else 0
        if code.startswith(('sh', 'sz')):
            return f"{name}\n最新价 {cur:.2f} 元\n涨跌 {chg:+.2f} ({chgp:+.2f}%)\n成交量 {volume} 手"
        elif code.startswith('hk'):
            return f"{name}\n最新价 {cur:.2f} 港元\n涨跌 {chg:+.2f} ({chgp:+.2f}%)"
        else:
            return f"{name}\n最新价 {cur:.2f} 美元\n涨跌 {chg:+.2f} ({chgp:+.2f}%)"
    except Exception as e:
        logger.error(f"股票查询异常: {e}")
        return "股票查询失败，请稍后再试。"

# ==================== 时间工具 ====================
def get_current_time() -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    weekdays = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]
    h = now.hour; ap = "上午" if h<12 else "下午"; h12 = h if h<=12 else h-12
    if h12==0: h12=12
    return f"现在是{now.year}年{now.month}月{now.day}日 {ap}{h12}点{now.minute:02d}分，{weekdays[now.weekday()]}"

# ==================== 工具分发（带日志） ====================
async def execute_tool(tool_name: str, arguments: dict) -> str:
    logger.info(f"🛠️ 执行工具: {tool_name}, 参数: {arguments}")
    try:
        if tool_name == "get_weather":
            city = arguments.get("city", "")
            if not city.strip(): city = "南京"
            return await get_weather_tool(city, arguments.get("date", "today"))
        elif tool_name == "web_search":
            query = arguments.get("query", "")
            if not query: return "请提供搜索词"
            results = await web_search(query)
            if not results: return "未找到相关信息。"
            lines = [f"🔍 搜索“{query}”结果："]
            for i, (t, b, l) in enumerate(results[:5], 1):
                lines.append(f"{i}. {t}")
                lines.append(f"   {b[:120]}...")
            return "\n".join(lines)
        elif tool_name == "search_news":
            query = arguments.get("query", "")
            if not query: return "请提供新闻关键词"
            results = await web_search(f"{query} 新闻", max_results=6)
            if not results: return "未找到相关新闻。"
            lines = [f"📰 关于“{query}”的新闻："]
            for i, (t, b, l) in enumerate(results[:6], 1):
                lines.append(f"{i}. {t}")
                cb = re.sub(r'[\\*]', '', b)[:120].strip()
                if cb: lines.append(f"   {cb}")
            return "\n".join(lines)
        elif tool_name == "get_stock":
            return await get_stock_quote(arguments.get("symbol"), arguments.get("date"))
        elif tool_name == "get_current_time":
            return get_current_time()
        return "未知工具"
    except Exception as e:
        logger.error(f"❌ 工具执行异常: {e}")
        return "服务暂时不可用，请稍后重试"

# ==================== 系统提示词 ====================
SYSTEM_PROMPT = (
    "你是小暖，一个亲切、耐心的老年人语音陪伴助手。"
    "请用简单易懂的口语和老人交流，使用“您”，回复尽量简短（2-3句话），方便语音播放。"
    "当需要实时信息（天气、新闻、股价、时间等）时，请调用提供的工具函数，并根据返回结果生成自然回答。"
    "如果用户没有指明城市，天气默认查询南京。"
    "不要使用任何 Markdown 符号。"
)

# ==================== 应用生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, audio_handler
    logger.info("🚀 正在启动服务...")
    init_db()
    await database.connect()
    audio_handler = AudioHandler()
    http_client = httpx.AsyncClient(timeout=15.0)
    logger.info("✅ 全局 httpx 连接池已创建，服务已启动")
    yield
    await http_client.aclose()
    await database.disconnect()
    logger.info("🛑 服务关闭")

app = FastAPI(title="小暖智能体", version="24.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ---------- 修改模板路径，使用绝对路径 ----------
templates_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=templates_dir)

# ==================== 会话管理 ====================
@app.post("/session/new")
async def new_session(request: NewSessionRequest):
    sid = await create_session(request.user_id, request.title)
    return {"session_id": sid, "title": request.title}

@app.get("/sessions/{user_id}")
async def list_sessions(user_id: str):
    sessions = await get_user_sessions(user_id)
    for s in sessions:
        last = await get_session_last_message(s["id"])
        s["preview"] = (last["content"][:50]+"...") if last and len(last["content"])>50 else (last["content"] if last else "暂无消息")
    return {"sessions": sessions}

@app.get("/session/{session_id}/messages")
async def get_messages(session_id: int):
    return {"messages": await get_session_messages(session_id)}

@app.delete("/session/{session_id}")
async def del_session(session_id: int, user_id: str = "default_user"):
    if not await delete_session(session_id, user_id):
        raise HTTPException(404, "Session not found")
    return {"status": "deleted"}

@app.delete("/conversation/message/{message_id}")
async def delete_message_api(message_id: int, user_id: str = "default_user"):
    if not await delete_message_by_id(message_id, user_id):
        raise HTTPException(404, "Message not found")
    return {"status": "deleted"}

@app.get("/voices")
async def get_voices():
    return {"voices": await audio_handler.get_voices()}

# ==================== 核心对话（错误详情返回前端） ====================
@app.post("/chat/text", response_model=ChatResponse)
async def text_chat(request: ChatRequest):
    user_id = request.user_id
    user_msg = request.message
    session_id = request.session_id
    logger.info(f"📩 收到消息: {user_msg[:60]}...")

    if user_msg.strip() in ["继续","下一条","更多","next"] and session_id and session_id in search_cache:
        cache = search_cache[session_id]
        if cache["current_index"] < len(cache["chunks"]):
            chunk = cache["chunks"][cache["current_index"]]
            cache["current_index"] += 1
            reply = chunk
            if cache["current_index"] < len(cache["chunks"]):
                reply += "\n\n（还有更多，回复“继续”查看下一段）"
            else:
                del search_cache[session_id]
            return ChatResponse(response=reply, session_id=session_id)
        else:
            del search_cache[session_id]
            return ChatResponse(response="没有更多内容了。", session_id=session_id)

    if session_id is None:
        session_id = await create_session(user_id, "新对话")

    history = await get_session_messages(session_id, limit=6)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_msg})

    final_reply = ""
    try:
        assistant_message = model_service.chat(messages, tools=TOOLS, tool_choice="auto")
        if hasattr(assistant_message, 'tool_calls') and assistant_message.tool_calls:
            logger.info(f"🔧 模型要求调用工具: {[tc.function.name for tc in assistant_message.tool_calls]}")
            messages.append(assistant_message)
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                try:
                    tool_result = await asyncio.wait_for(
                        execute_tool(tool_name, arguments),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    tool_result = "服务超时，请稍后再试。"
                    logger.error(f"⏱️ 工具 {tool_name} 超时")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            final_message = model_service.chat(messages, tools=TOOLS, tool_choice="auto")
            final_reply = final_message.content if final_message and final_message.content else "抱歉，我无法处理您的请求。"
        else:
            final_reply = assistant_message.content if assistant_message else "抱歉，我无法处理。"
    except Exception as e:
        logger.error(f"❌ 对话异常: {e}", exc_info=True)
        final_reply = f"系统异常：{str(e)}"

    await save_message(user_id, session_id, "user", user_msg)
    await save_message(user_id, session_id, "assistant", final_reply)

    if len(history) == 0:
        new_title = user_msg[:30] + ("..." if len(user_msg)>30 else "")
        await database.execute("UPDATE sessions SET title = :title WHERE id = :session_id",
                               {"title": new_title, "session_id": session_id})

    return ChatResponse(response=final_reply, session_id=session_id)

# ==================== TTS 和语音 ====================
@app.post("/chat/text-to-speech")
async def text_to_speech_only(req: TextToSpeechRequest):
    audio_bytes = await audio_handler.text_to_speech(req.text, req.voice)
    b64 = audio_handler.audio_to_base64(audio_bytes) if audio_bytes else ""
    return {"audio_response": b64}

@app.post("/chat/voice")
async def voice_chat(audio: UploadFile = File(...), user_id: str = Form("default_user"), session_id: Optional[int] = Form(None)):
    audio_bytes = await audio.read()
    user_text = audio_handler.speech_to_text(audio_bytes)
    if not user_text:
        return JSONResponse(status_code=400, content={"error": "无法识别语音"})
    chat_req = ChatRequest(message=user_text, user_id=user_id, session_id=session_id)
    chat_res = await text_chat(chat_req)
    audio_out = await audio_handler.text_to_speech(chat_res.response)
    b64 = audio_handler.audio_to_base64(audio_out) if audio_out else ""
    return {"recognized_text": user_text, "ai_response": chat_res.response, "audio_base64": b64, "session_id": chat_res.session_id}

@app.get("/web", response_class=HTMLResponse)
async def web_chat(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)