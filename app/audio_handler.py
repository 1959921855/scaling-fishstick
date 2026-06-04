import edge_tts
import base64
import hashlib
import hmac
import json
import os
import asyncio
import websockets
from datetime import datetime, timezone, timedelta
from email.utils import formatdate
import time as _time

class AudioHandler:
    def __init__(self):
        self.xf_appid = os.getenv("XF_APPID")
        self.xf_api_key = os.getenv("XF_API_KEY")
        self.xf_api_secret = os.getenv("XF_API_SECRET")
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
        # 生成符合 RFC1123 的 GMT 时间
        now = datetime.utcnow()
        stamp = _time.mktime(now.timetuple())
        date = formatdate(stamp, usegmt=True)

        host = "iat-api.xfyun.cn"
        path = "/v2/iat"
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature = base64.b64encode(
            hmac.new(self.xf_api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
        ).decode()
        authorization_origin = f'api_key="{self.xf_api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        return f"{self.xf_url}?authorization={authorization}&date={date}&host={host}"

    async def _send_audio(self, audio_data: bytes):
        url = self._create_url()
        async with websockets.connect(url) as ws:
            # 第一帧：参数
            frame = {
                "common": {"app_id": self.xf_appid},
                "business": {
                    "language": "zh_cn",
                    "domain": "iat",
                    "accent": "mandarin",
                    "vad_eos": 3000
                },
                "data": {
                    "status": 0,
                    "format": "audio/L16;rate=16000",
                    "encoding": "raw",
                    "audio": base64.b64encode(audio_data).decode()
                }
            }
            await ws.send(json.dumps(frame))

            # 第二帧：结束标志
            end_frame = {
                "data": {"status": 2}
            }
            await ws.send(json.dumps(end_frame))

            # 接收结果
            final_text = ""
            async for msg in ws:
                result = json.loads(msg)
                if result.get("code") != 0:
                    print(f"讯飞识别出错: {result}")
                    break
                data = result.get("data", {})
                if data.get("status") == 2:
                    if "result" in data:
                        ws_list = data["result"].get("ws", [])
                        for w in ws_list:
                            cw = w.get("cw", [])
                            for c in cw:
                                final_text += c.get("w", "")
                    break
            return final_text

    async def speech_to_text(self, audio_bytes: bytes) -> str:
        """异步语音识别，返回文字"""
        if not audio_bytes:
            return ""
        try:
            return await self._send_audio(audio_bytes)
        except Exception as e:
            print(f"讯飞识别异常: {e}")
            return ""