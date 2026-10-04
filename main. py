from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Nova Core")


class ChatMessage(BaseModel):
    message: str


@app.get("/")
def home():
    return FileResponse("index.html")


@app.post("/chat")
def chat(data: ChatMessage):
    return {
        "reply": f"Nova received: {data.message}"
    }
