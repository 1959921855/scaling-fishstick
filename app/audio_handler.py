import edge_tts
import base64
import hashlib
import hmac
import json
import time
import os
import tempfile
import requests
from datetime import datetime, timezone, timedelta

class AudioHandler:
    def __init__(self):
        # 讯飞配置
        self.xf_appid = os.getenv("XF_APPID")
        self.xf_api_key = os.getenv("XF_API_KEY")
        self.xf_api_secret = os.getenv("XF_API_SECRET")

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

    def _get_xf_url(self):
        """生成讯飞鉴权URL"""
        host = "ws-api.xfyun.cn"
        path = "/v2/iat"
        # 北京时间
        tz = timezone(timedelta(hours=8))
        date = datetime.now(tz).strftime("%a, %d %b %Y %H:%M:%S %Z")
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature = base64.b64encode(
            hmac.new(
                self.xf_api_secret.encode(),
                signature_origin.encode(),
                hashlib.sha256
            ).digest()
        ).decode()
        authorization_origin = f'api_key="{self.xf_api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        return f"ws://{host}{path}?authorization={authorization}&date={date}&host={host}"

    def speech_to_text(self, audio_bytes: bytes) -> str:
        """使用讯飞语音听写将音频转为文字"""
        if not audio_bytes:
            return ""

        # 将音频写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp_path = tmp.name

        try:
            # 读取音频数据
            with open(tmp_path, "rb") as f:
                audio_data = f.read()

            # 讯飞接口要求 base64
            audio_base64 = base64.b64encode(audio_data).decode()

            # 构建请求参数
            params = {
                "common": {"app_id": self.xf_appid},
                "business": {
                    "language": "zh_cn",
                    "domain": "iat",
                    "accent": "mandarin",
                    "vad_eos": 3000,
                },
                "data": {
                    "audio": audio_base64,
                    "status": 2,
                    "format": "audio/L16;rate=16000",
                    "encoding": "raw",
                },
            }

            # 获取鉴权URL
            url = self._get_xf_url()
            # 发送请求
            resp = requests.post(
                url.replace("ws://", "https://"),
                json=params,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 0:
                    # 拼接所有识别文本
                    texts = []
                    for item in result.get("data", {}).get("result", []):
                        ws = item.get("ws", [])
                        for w in ws:
                            cw = w.get("cw", [])
                            for c in cw:
                                texts.append(c.get("w", ""))
                    return "".join(texts)
                else:
                    print(f"讯飞识别失败: {result}")
                    return ""
            else:
                print(f"讯飞请求失败: {resp.status_code}")
                return ""

        except Exception as e:
            print(f"讯飞识别异常: {e}")
            return ""
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)