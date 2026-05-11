from fastapi import FastAPI
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "active", "message": "语音助手简化版运行中"}

if __name__ == "__main__":
    import uvicorn
    logger.info("启动服务器...")
    uvicorn.run(app, host="0.0.0.0", port=8000)