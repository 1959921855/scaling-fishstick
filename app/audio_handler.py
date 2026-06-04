import edge_tts
import base64
import tempfile
import os
from openai import OpenAI

class AudioHandler:
    def __init__(self):
        # 初始化 OpenAI 客户端（用于 Whisper 语音识别）
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        """使用 Whisper 将语音转为文字"""
        if not audio_bytes:
            return ""

        # 将音频字节写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as audio_file:
                transcript = self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="zh"
                )
            return transcript.text
        except Exception as e:
            print(f"Whisper 识别失败: {e}")
            return ""
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)