import os
import secrets
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client, Client

app = FastAPI(title="Nova Core")

# AI
client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ.get("HF_TOKEN")
)

# Supabase memory
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

# Authentication
DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")
ACCESS_KEY = os.environ.get("NOVA_ACCESS_KEY")


def require_access(request: Request):
    if not ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access is not configured."
        )

    session_key = request.cookies.get("nova_access")

    if not session_key or not secrets.compare_digest(
        session_key,
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


def get_noah_memory():
    result = (
        supabase
        .table("nova_memory")
        .select("memory_key, memory_value")
        .eq("user_id", "noah")
        .execute()
    )

    return result.data


def ensure_noah_identity():
    existing = get_noah_memory()

    for memory in existing:
        if (
            memory["memory_key"] == "name"
            and memory["memory_value"] == "Noah"
        ):
            return

    supabase.table("nova_memory").insert({
        "user_id": "noah",
        "memory_key": "name",
        "memory_value": "Noah"
    }).execute()


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

    if not secrets.compare_digest(
        data.key,
        ACCESS_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid access key."
        )

    response = RedirectResponse(
        url="/",
        status_code=303
    )

    response.set_cookie(
        key="nova_access",
        value=ACCESS_KEY,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800
    )

    return response


@app.get("/")
def home(request: Request):

    try:
        require_access(request)
        return FileResponse("index.html")

    except HTTPException:
        return RedirectResponse(
            url="/login",
            status_code=303
        )


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
    request: Request
):

    require_access(request)

    # Make sure Nova knows who Noah is
    ensure_noah_identity()

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: {memory['memory_value']}"
        for memory in memories
    )

    system_prompt = f"""
You are Nova, Noah's personal AI assistant.

You know that the person you are talking to is Noah.

Persistent memory:
{memory_text}

Use this memory naturally when relevant.
Call the user Noah when appropriate.

Answer clearly, accurately, and helpfully.
"""

    response = client.chat.completions.create(
        model="Qwen/Qwen3-4B-Instruct-2507",
        messages=[
            {
                "role": "system",
                "content": system_prompt
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
