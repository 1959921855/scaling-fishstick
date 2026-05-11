FROM python:3.11-slim

# 安装语音合成和音频处理所需系统库
RUN apt-get update && apt-get install -y \
    espeak \
    ffmpeg \
    gcc \
    g++ \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建数据持久化目录
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "-m", "app.main"]