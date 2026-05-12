import pyttsx3
import tempfile
import os
import base64
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self):
        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty('rate', 170)
        self.tts_engine.setProperty('volume', 0.9)

        voices = self.tts_engine.getProperty('voices')
        for voice in voices:
            if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower():
                self.tts_engine.setProperty('voice', voice.id)
                break

    def speech_to_text(self, audio_bytes: bytes) -> str:
        # 前端已处理语音识别，此函数不被实际使用，保留空实现避免报错
        return ""

    def text_to_speech(self, text: str) -> bytes:
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name
            self.tts_engine.save_to_file(text, tmp_path)
            self.tts_engine.runAndWait()
            with open(tmp_path, 'rb') as f:
                audio_bytes = f.read()
            os.unlink(tmp_path)
            return audio_bytes
        except Exception as e:
            logger.error(f"语音合成错误: {e}")
            return b""

    def audio_to_base64(self, audio_bytes: bytes) -> str:
        return base64.b64encode(audio_bytes).decode('utf-8')