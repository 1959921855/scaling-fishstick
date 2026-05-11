from openai import OpenAI
import os
from typing import List, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DeepSeekService:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not found")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com/v1"
        )
        
        self.conversations: Dict[str, List[Dict]] = {}
    
    async def chat(self, message: str, user_id: str = "default_user") -> str:
        try:
            messages = [
                {"role": "system", "content": "你是一个友善的语音陪聊助手。"},
                {"role": "user", "content": message}
            ]
            
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error: {str(e)}")
            return "抱歉，遇到技术问题。"