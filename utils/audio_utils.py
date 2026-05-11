import speech_recognition as sr
import pyttsx3
import io
import base64
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioUtils:
    def __init__(self):
        # 初始化语音识别器
        self.recognizer = sr.Recognizer()
        
        # 初始化文本转语音引擎
        self.tts_engine = pyttsx3.init()
        # 配置语音参数
        self.tts_engine.setProperty('rate', 150)  # 语速
        self.tts_engine.setProperty('volume', 0.9)  # 音量
        
        # 获取可用语音
        voices = self.tts_engine.getProperty('voices')
        if voices:
            self.tts_engine.setProperty('voice', voices[0].id)
    
    def speech_to_text(self, audio_bytes: bytes) -> str:
        """将音频字节转换为文本"""
        try:
            # 将字节转换为AudioData对象
            audio_data = sr.AudioData(audio_bytes, 16000, 2)
            # 使用Google语音识别（免费）
            text = self.recognizer.recognize_google(audio_data, language='zh-CN')
            return text
        except sr.UnknownValueError:
            logger.warning("无法识别音频")
            return ""
        except sr.RequestError as e:
            logger.error(f"语音识别服务错误: {e}")
            return ""
        except Exception as e:
            logger.error(f"语音转文本错误: {e}")
            return ""
    
    def text_to_speech(self, text: str) -> bytes:
        """将文本转换为音频字节"""
        try:
            # 创建内存缓冲区
            audio_buffer = io.BytesIO()
            
            # 保存音频到临时文件
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                tmp_path = tmp_file.name
            
            # 生成音频文件
            self.tts_engine.save_to_file(text, tmp_path)
            self.tts_engine.runAndWait()
            
            # 读取音频文件
            with open(tmp_path, 'rb') as f:
                audio_bytes = f.read()
            
            # 清理临时文件
            import os
            os.unlink(tmp_path)
            
            return audio_bytes
            
        except Exception as e:
            logger.error(f"文本转语音错误: {e}")
            return b""
    
    def audio_to_base64(self, audio_bytes: bytes) -> str:
        """将音频字节转换为base64字符串"""
        return base64.b64encode(audio_bytes).decode('utf-8')
    
    def base64_to_audio(self, base64_str: str) -> bytes:
        """将base64字符串转换为音频字节"""
        return base64.b64decode(base64_str)