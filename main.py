import os
import secrets
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="Nova Core")

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ.get("HF_TOKEN")
)

DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")
ACCESS_KEY = os.environ.get("NOVA_ACCESS_KEY")


def require_access(x_nova_key: Optional[str]):
    if not ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access is not configured."
        )

    if not x_nova_key or not secrets.compare_digest(
        x_nova_key,
        ACCESS_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Nova access required."
        )


def require_developer(x_developer_key: Optional[str]):
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


class ChatMessage(BaseModel):
    message: str


class AccessRequest(BaseModel):
    key: str


@app.get("/login")
def login_page():
    return FileResponse("login.html")


@app.post("/auth")
def authenticate(data: AccessRequest):

    if not ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access is not configured."
        )

    if not secrets.compare_digest(data.key, ACCESS_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid access key."
        )

    return {
        "authenticated": True
    }


@app.get("/")
def home(
    x_nova_key: Optional[str] = Header(default=None)
):
    require_access(x_nova_key)
    return FileResponse("index.html")


@app.get("/developer-login")
def developer_login():
    return FileResponse("developer-login.html")


@app.get("/developer")
def developer_page(
    x_developer_key: Optional[str] = Header(default=None)
):
    require_developer(x_developer_key)
    return FileResponse("developer.html")


@app.post("/chat")
def chat(
    data: ChatMessage,
    x_nova_key: Optional[str] = Header(default=None)
):

    require_access(x_nova_key)

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


@app.get("/developer/status")
def developer_status(
    x_developer_key: Optional[str] = Header(default=None)
):

    require_developer(x_developer_key)

    return {
        "developer_mode": True,
        "message": "Developer access granted."
    }
