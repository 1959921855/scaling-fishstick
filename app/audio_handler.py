import edge_tts
import base64
import hashlib
import json
import os
import tempfile
import time
import requests

class AudioHandler:
    def __init__(self):
        self.xf_appid = os.getenv("XF_APPID")
        self.xf_api_key = os.getenv("XF_API_KEY")
        self.xf_api_secret = os.getenv("XF_API_SECRET")
        self.xf_url = "https://api.xfyun.cn/v1/service/v1/iat"

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

    def speech_to_text(self, audio_bytes: bytes) -> str:
        """使用讯飞语音听写 REST API 将音频转为文字"""
        if not audio_bytes:
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as f:
                audio_data = f.read()

            # 只保留必要参数，去除 result_level 和 punc
            params = {
                "engine_type": "iat",
                "aue": "raw",
                "auf": "audio/L16;rate=16000",
                "language": "zh_cn",
                "accent": "mandarin",
                "vad_eos": 3000,
            }
            # 确保 JSON 紧凑格式，无空格
            param_json = json.dumps(params, separators=(',', ':'), ensure_ascii=False)
            param_base64 = base64.b64encode(param_json.encode()).decode()

            cur_time = str(int(time.time()))
            check_sum = hashlib.md5(
                (self.xf_appid + cur_time + self.xf_api_secret).encode()
            ).hexdigest()

            headers = {
                "X-Appid": self.xf_appid,
                "X-CurTime": cur_time,
                "X-Param": param_base64,
                "X-CheckSum": check_sum,
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            }

            resp = requests.post(
                self.xf_url,
                data=audio_data,
                headers=headers,
                timeout=10,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == "0":
                    texts = []
                    data_section = result.get("data", {})
                    if data_section:
                        result_list = data_section.get("result", [])
                        for item in result_list:
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