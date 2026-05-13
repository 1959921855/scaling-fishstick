import edge_tts
import asyncio
import io
import base64
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self):
        self.voice_cache = None   # 缓存音色列表

    async def get_voices(self):
        """获取可用音色列表（仅中文女性/男性）"""
        if self.voice_cache is None:
            voices = await edge_tts.list_voices()
            # 筛选中文音色 (zh-CN)
            chinese_voices = [v for v in voices if v['Locale'].startswith('zh-CN')]
            # 整理为简单列表
            self.voice_cache = [
                {'name': v['ShortName'], 'gender': v['Gender'], 'display': f"{v['ShortName']} ({v['Gender']})"}
                for v in chinese_voices
            ]
        return self.voice_cache

    async def text_to_speech(self, text: str, voice: str = 'zh-CN-XiaoxiaoNeural') -> bytes:
        """指定音色合成语音，返回 mp3 字节"""
        try:
            communicate = edge_tts.Communicate(text, voice)
            mp3_data = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk['type'] == 'audio':
                    mp3_data.write(chunk['data'])
            mp3_data.seek(0)
            return mp3_data.getvalue()
        except Exception as e:
            logger.error(f"Edge TTS 错误: {e}")
            return b""

    def speech_to_text(self, audio_bytes: bytes) -> str:
        # 前端已处理，此处不需要
        return ""

    def audio_to_base64(self, audio_bytes: bytes) -> str:
        return base64.b64encode(audio_bytes).decode('utf-8')