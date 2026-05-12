import gtts
import io
import base64
import logging
from pydub import AudioSegment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self):
        # 不需要初始化 TTS 引擎
        pass

    def speech_to_text(self, audio_bytes: bytes) -> str:
        # 前端已处理语音识别，后端无需实现
        return ""

    def text_to_speech(self, text: str) -> bytes:
        """使用 gTTS 将文本转为 MP3 并返回 bytes"""
        try:
            # 生成 MP3 音频
            tts = gtts.gTTS(text, lang='zh-cn')
            mp3_fp = io.BytesIO()
            tts.write_to_fp(mp3_fp)
            mp3_fp.seek(0)
            # 为了兼容前端已有的 WAV 解码，将 MP3 转为 WAV（可选，前端也支持 MP3）
            # 但您的前端代码中使用 new Audio("data:audio/wav;base64,...")，所以需要 WAV
            # 因此我们将 MP3 转成 WAV
            audio = AudioSegment.from_mp3(mp3_fp)
            wav_fp = io.BytesIO()
            audio.export(wav_fp, format="wav")
            return wav_fp.getvalue()
        except Exception as e:
            logger.error(f"语音合成错误: {e}")
            return b""

    def audio_to_base64(self, audio_bytes: bytes) -> str:
        return base64.b64encode(audio_bytes).decode('utf-8')