import logging
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioUtils:
    def __init__(self):
        logger.info("音频工具初始化")
    
    def speech_to_text(self, audio_bytes: bytes) -> str:
        return "测试文本"
    
    def text_to_speech(self, text: str) -> bytes:
        return b""
    
    def audio_to_base64(self, audio_bytes: bytes) -> str:
        return base64.b64encode(audio_bytes).decode('utf-8')