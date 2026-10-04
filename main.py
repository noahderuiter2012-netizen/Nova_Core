import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="Nova Core")

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ.get("HF_TOKEN")
)


class ChatMessage(BaseModel):
    message: str


@app.get("/")
def home():
    return FileResponse("index.html")


@app.post("/chat")
def chat(data: ChatMessage):

    response = client.chat.completions.create(
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Nova, a helpful personal AI assistant. "
                    "Answer clearly, accurately, and concisely."
                )
            },
            {
                "role": "user",
                "content": data.message
            }
        ],
        max_tokens=300
    )

    return {
        "reply": response.choices[0].message.content
    }
