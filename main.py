import os
import secrets

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="Nova Core")

# -------------------------
# AI
# -------------------------

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ.get("HF_TOKEN")
)

# -------------------------
# Developer authentication
# -------------------------

DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")


def require_developer(x_developer_key: str | None):

    if not DEVELOPER_KEY:
        raise HTTPException(
            status_code=500,
            detail="Developer authentication is not configured."
        )

    if not x_developer_key or not secrets.compare_digest(
        x_developer_key,
        DEVELOPER_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Developer access required."
        )


# -------------------------
# Models
# -------------------------

class ChatMessage(BaseModel):
    message: str


# -------------------------
# Website
# -------------------------

@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/developer")
def developer_page(
    x_developer_key: str | None = Header(default=None)
):
    require_developer
    @app.get("/developer-login")
def developer_login():
    return FileResponse("developer-login.html")
    @app.post("/chat")
def chat(data: ChatMessage):

    response = client.chat.completions.create(
        model="Qwen/Qwen3-4B-Instruct-2507",
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
