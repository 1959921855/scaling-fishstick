from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "OK"}

if __name__ == "__main__":
    import uvicorn
    print("启动服务器...")
    uvicorn.run(app, host="127.0.0.1", port=8000)