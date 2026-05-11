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
            raise ValueError("DEEPSEEK_API_KEY not found in environment variables")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com/v1"
        )
        
        # 存储对话历史
        self.conversations: Dict[str, List[Dict]] = {}
        
    def get_conversation_history(self, user_id: str) -> List[Dict]:
        """获取用户对话历史"""
        if user_id not in self.conversations:
            self.conversations[user_id] = []
        return self.conversations[user_id]
    
    def save_conversation(self, user_id: str, role: str, content: str):
        """保存对话"""
        message = {"role": role, "content": content}
        self.conversations[user_id].append(message)
        
        # 限制历史长度（可选）
        if len(self.conversations[user_id]) > 20:
            self.conversations[user_id] = self.conversations[user_id][-20:]
    
    async def chat(self, message: str, user_id: str = "default_user") -> str:
        """与DeepSeek模型对话"""
        try:
            # 获取对话历史
            history = self.get_conversation_history(user_id)
            
            # 构建消息列表
            messages = [
                {"role": "system", "content": "你是一个友善、热情的语音陪聊助手。你的回答应该自然、有趣，适合语音对话。保持对话流畅，适当使用语气词让对话更自然。"}
            ]
            
            # 添加历史消息
            messages.extend(history)
            
            # 添加新消息
            messages.append({"role": "user", "content": message})
            
            # 调用DeepSeek API
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.7,
                max_tokens=500,
                stream=False
            )
            
            ai_response = response.choices[0].message.content
            
            # 保存对话
            self.save_conversation(user_id, "user", message)
            self.save_conversation(user_id, "assistant", ai_response)
            
            return ai_response
            
        except Exception as e:
            logger.error(f"Error calling DeepSeek API: {str(e)}")
            return "抱歉，我现在遇到了一些技术问题，请稍后再试。"