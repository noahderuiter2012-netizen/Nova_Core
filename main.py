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

def nova_plan(message: str) -> str:
    text = message.lower()

    if any(word in text for word in [
        "calculate",
        "what is",
        "how much is",
        "multiply",
        "divide",
        "plus",
        "minus"
    ]):
        return "calculator"

    if any(word in text for word in [
        "search",
        "look up",
        "latest",
        "news",
        "what happened today"
    ]):
        return "web_search"

    if any(word in text for word in [
        "remember",
        "don't forget",
        "save this",
        "keep in mind"
    ]):
        return "memory"

    if any(word in text for word in [
        "timer",
        "countdown"
    ]):
        return "timer"

        return "chat"


def nova_execute(plan: str, message: str):
    if plan == "chat":
        return None

    if plan == "calculator":
        return "calculator"

    if plan == "web_search":
        return "web_search"

    if plan == "memory":
        return "memory"

    if plan == "timer":
        return "timer"

    return None


def nova_calculate(expression: str):
    try:
        allowed = "0123456789+-*/(). "
        cleaned = "".join(
            char for char in expression
            if char in allowed
        )

        if not cleaned:
            return "I couldn't find a calculation."

        result = eval(cleaned, {"__builtins__": {}}, {})
        return str(result)

    except Exception:
        return "I couldn't calculate that."

class ChatMessage(BaseModel):
    message: str


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
    plan = nova_plan(data.message)
    tool = nova_execute(plan, data.message)
    # Make sure Nova knows who Noah is
    ensure_noah_identity()

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: {memory['memory_value']}"
        for memory in memories
    )

    system_prompt = f"""
    Current task plan:
{plan}

Use this plan to decide how to handle Noah's request.
You are Nova, Noah's personal AI assistant.

PERSONALITY:
- Speak with the calm, intelligent, sophisticated manner of a futuristic personal AI.
- Be exceptionally composed and confident.
- Address the user as Noah when appropriate.
- Be polite and professional without sounding robotic.
- Use subtle, dry humor occasionally when it fits the conversation.
- Give concise answers for simple questions and detailed answers when Noah needs them.
- Be proactive: if something useful is obvious from Noah's request, mention it.
- Never pretend you completed an action that you did not actually perform.
- Do not constantly say "Certainly" or use repetitive catchphrases.
- Never claim to be the fictional character JARVIS or copy its exact dialogue.

Persistent memory:
{memory_text}

Use Noah's memories naturally when relevant.

Your goal is to feel like Noah has his own sophisticated, intelligent AI assistant.
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
