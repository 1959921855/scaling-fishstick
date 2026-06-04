import edge_tts
import base64

class AudioHandler:
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

    # 语音识别已由前端/安卓原生处理，不再需要后端识别
    def speech_to_text(self, audio_bytes):
        return ""