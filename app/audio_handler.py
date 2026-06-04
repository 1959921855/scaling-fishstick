import edge_tts
import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import asyncio
import websockets
from datetime import datetime, timezone, timedelta

class AudioHandler:
    def __init__(self):
        self.xf_appid = os.getenv("XF_APPID")
        self.xf_api_key = os.getenv("XF_API_KEY")
        self.xf_api_secret = os.getenv("XF_API_SECRET")
        # 讯飞 WebSocket 地址
        self.xf_url = "wss://iat-api.xfyun.cn/v2/iat"

    async def get_voices(self):
        voices = await edge_tts.list_voices(proxy=None)
        return [
            {"name": v["ShortName"], "display": f"{v['Locale']} - {v['FriendlyName']}"}
            for v in voices if v["ShortName"].startswith("zh-CN")
        ]

    async def text_to_speech(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural"):
        communicate = edge_tts.Communicate(text, voice, proxy=None)
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
        return audio_bytes

    def audio_to_base64(self, audio_bytes):
        return base64.b64encode(audio_bytes).decode('utf-8')

    def _create_url(self):
        """生成鉴权 URL"""
        host = "iat-api.xfyun.cn"
        path = "/v2/iat"
        # 获取北京时间
        tz = timezone(timedelta(hours=8))
        date = datetime.now(tz).strftime("%a, %d %b %Y %H:%M:%S %Z")
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature = base64.b64encode(
            hmac.new(self.xf_api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
        ).decode()
        authorization_origin = f'api_key="{self.xf_api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        return f"{self.xf_url}?authorization={authorization}&date={date}&host={host}"

    async def _send_audio(self, audio_data: bytes):
        """异步 WebSocket 识别"""
        url = self._create_url()
        async with websockets.connect(url) as websocket:
            # 发送参数
            frame = {
                "common": {"app_id": self.xf_appid},
                "business": {
                    "language": "zh_cn",
                    "domain": "iat",
                    "accent": "mandarin",
                    "vad_eos": 3000,
                    "dwa": "wpgs"   # 开启动态修正
                },
                "data": {
                    "status": 0,
                    "format": "audio/L16;rate=16000",
                    "encoding": "raw",
                    "audio": base64.b64encode(audio_data).decode()
                }
            }
            await websocket.send(json.dumps(frame))

            # 发送结束标志
            end_frame = {
                "data": {"status": 2}
            }
            await websocket.send(json.dumps(end_frame))

            # 接收结果
            final_text = ""
            async for msg in websocket:
                result = json.loads(msg)
                if result.get("code") != 0:
                    print(f"讯飞识别出错: {result}")
                    break
                # 提取文本
                if "data" in result and "result" in result["data"]:
                    ws = result["data"]["result"].get("ws", [])
                    for w in ws:
                        cw = w.get("cw", [])
                        for c in cw:
                            final_text += c.get("w", "")
                if result["data"].get("status") == 2:
                    break
            return final_text

    def speech_to_text(self, audio_bytes: bytes) -> str:
        """同步调用 WebSocket 识别"""
        if not audio_bytes:
            return ""
        try:
            return asyncio.run(self._send_audio(audio_bytes))
        except Exception as e:
            print(f"讯飞识别异常: {e}")
            return ""