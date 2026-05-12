import gtts
import io
import base64
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self):
        pass

    def speech_to_text(self, audio_bytes: bytes) -> str:
        # 前端已处理语音识别，此处不需要
        return ""

    def text_to_speech(self, text: str) -> bytes:
        """使用 gTTS 合成语音，直接返回 MP3 二进制数据"""
        try:
            tts = gtts.gTTS(text, lang='zh-cn')
            mp3_fp = io.BytesIO()
            tts.write_to_fp(mp3_fp)
            mp3_fp.seek(0)
            return mp3_fp.getvalue()
        except Exception as e:
            logger.error(f"语音合成错误: {e}")
            return b""

    def audio_to_base64(self, audio_bytes: bytes) -> str:
        return base64.b64encode(audio_bytes).decode('utf-8')