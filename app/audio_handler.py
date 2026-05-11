import speech_recognition as sr
import pyttsx3
import tempfile
import os
import base64
import logging
import re
import emoji  # 用于移除表情符号

from pydub import AudioSegment
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty('rate', 170)
        self.tts_engine.setProperty('volume', 0.9)
        # 尝试设置中文语音
        voices = self.tts_engine.getProperty('voices')
        for voice in voices:
            if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower():
                self.tts_engine.setProperty('voice', voice.id)
                break

    def _convert_to_wav(self, audio_bytes: bytes) -> bytes:
        """将音频格式转换为 WAV (16k, 单声道)"""
        try:
            audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
            audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)
            wav_io = io.BytesIO()
            audio.export(wav_io, format="wav")
            return wav_io.getvalue()
        except Exception as e:
            logger.error(f"音频转换失败: {e}")
            return audio_bytes

    def _clean_text_for_tts(self, text: str) -> str:
        """
        清理文本，移除表情符号、特殊符号，保留中文字符、字母、数字和基本标点
        """
        if not text:
            return ""

        # 1. 移除所有 emoji 表情符号
        text = emoji.replace_emoji(text, replace='')  # 直接删除表情符号

        # 2. 移除不需要的特殊符号（* # _ ~ | ` 等），可保留句号、逗号、问号、感叹号、分号、冒号、括号
        #   注意：保留中文和英文句点。英文句点 . 需要保留（表示句子结束）
        allowed_punctuation = r'\.\,\!\?\;\:\"\'\（\）\(\)\【\】\[\]\《\》\「\」\、\。\？\！\；\：\“\”\‘\’'
        pattern = r'[^a-zA-Z0-9\u4e00-\u9fff\s' + allowed_punctuation + r']'
        text = re.sub(pattern, '', text)

        # 3. 将多个连续空格/空白替换为一个空格
        text = re.sub(r'\s+', ' ', text).strip()

        # 4. 可选：将英文句点 . 后添加空格（如果需要），但 pyttsx3 会自然停顿
        #   不需要额外处理

        logger.info(f"清理后的合成文本: {text}")
        return text

    def speech_to_text(self, audio_bytes: bytes) -> str:
        """语音识别（仅用于 /chat/voice 端点，前端直接识别时基本不用）"""
        try:
            wav_bytes = self._convert_to_wav(audio_bytes)
            audio_data = sr.AudioData(wav_bytes, 16000, 2)
            # 注意：Google 识别在国内可能不可用，建议前端识别
            text = self.recognizer.recognize_google(audio_data, language='zh-CN')
            logger.info(f"语音识别结果: {text}")
            return text
        except Exception as e:
            logger.error(f"语音识别错误: {e}")
            return ""

    def text_to_speech(self, text: str) -> bytes:
        """文本转语音，先清理文本再合成"""
        # 清理文本，移除表情符号和特殊符号
        clean_text = self._clean_text_for_tts(text)
        if not clean_text:
            logger.warning("清理后文本为空，跳过语音合成")
            return b""

        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name
            self.tts_engine.save_to_file(clean_text, tmp_path)
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