import os
import secrets
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client, Client


app = FastAPI(title="Nova Core")


# =========================
# AI CONNECTION
# =========================

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ.get("HF_TOKEN")
)


# =========================
# SUPABASE MEMORY
# =========================

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)


# =========================
# KEYS
# =========================

DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")
ACCESS_KEY = os.environ.get("NOVA_ACCESS_KEY")


# =========================
# ACCESS CONTROL
# =========================

def require_access(request: Request):
    if not ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access is not configured."
        )

    session_key = request.cookies.get("nova_access")

    if not session_key:
        raise HTTPException(
            status_code=401,
            detail="Nova access required."
        )

    if not secrets.compare_digest(session_key, ACCESS_KEY):
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

    if not x_developer_key:
        raise HTTPException(
            status_code=401,
            detail="Developer access required."
        )

    if not secrets.compare_digest(
        x_developer_key,
        DEVELOPER_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Developer access required."
        )


# =========================
# NOVA MEMORY
# =========================

def ensure_noah_identity():
    try:
        existing = (
            supabase
            .table("nova_memory")
            .select("*")
            .eq("user_id", "noah")
            .eq("memory_key", "name")
            .execute()
        )

        if not existing.data:
            supabase.table("nova_memory").insert({
                "user_id": "noah",
                "memory_key": "name",
                "memory_value": "Noah"
            }).execute()

    except Exception as error:
        print("Memory identity error:", error)


def get_noah_memory():
    try:
        result = (
            supabase
            .table("nova_memory")
            .select("*")
            .eq("user_id", "noah")
            .execute()
        )

        return result.data or []

    except Exception as error:
        print("Memory read error:", error)
        return []


def save_memory(memory_key: str, memory_value: str):
    try:
        supabase.table("nova_memory").insert({
            "user_id": "noah",
            "memory_key": memory_key,
            "memory_value": memory_value
        }).execute()

        return True

    except Exception as error:
        print("Memory save error:", error)
        return False


# =========================
# NOVA PLANNER
# =========================

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


# =========================
# CALCULATOR
# =========================

def nova_calculate(expression: str):

    try:

        allowed = "0123456789+-*/(). "

        cleaned = "".join(
            char
            for char in expression
            if char in allowed
        )

        if not cleaned:
            return "I couldn't find a calculation."

        result = eval(
            cleaned,
            {"__builtins__": {}},
            {}
        )

        return str(result)

    except Exception:

        return "I couldn't calculate that."


# =========================
# REQUEST MODELS
# =========================

class ChatMessage(BaseModel):
    message: str


class LoginRequest(BaseModel):
    key: str


class VisionMessage(BaseModel):
    message: str
    image: str


# =========================
# HOME
# =========================

@app.get("/")
def home(request: Request):

    session_key = request.cookies.get(
        "nova_access"
    )

    if not session_key or not ACCESS_KEY:
        return RedirectResponse("/login")

    if not secrets.compare_digest(
        session_key,
        ACCESS_KEY
    ):
        return RedirectResponse("/login")

    return FileResponse("index.html")


# =========================
# LOGIN PAGE
# =========================

@app.get("/login")
def login():

    return FileResponse("login.html")


# =========================
# LOGIN
# =========================

@app.post("/auth")
def authenticate(data: LoginRequest):

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
        "/",
        status_code=303
    )

    response.set_cookie(
        key="nova_access",
        value=ACCESS_KEY,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7
    )

    return response


# =========================
# NORMAL CHAT
# =========================

@app.post("/chat")
def chat(
    data: ChatMessage,
    request: Request
):

    require_access(request)

    plan = nova_plan(data.message)

    tool = nova_execute(
        plan,
        data.message
    )


    # Calculator
    if tool == "calculator":

        result = nova_calculate(
            data.message
        )

        return {
            "reply": f"The result is {result}."
        }


    # Memory
    if tool == "memory":

        saved = save_memory(
            "user_memory",
            data.message
        )

        if saved:

            return {
                "reply":
                "I've saved that to my memory, Noah."
            }

        return {
            "reply":
            "I couldn't save that memory."
        }


    # Load identity
    ensure_noah_identity()

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: "
        f"{memory['memory_value']}"
        for memory in memories
    )


    # =========================
    # NOVA PERSONALITY
    # =========================

    system_prompt = f"""

Current task plan:
{plan}

You are Nova, Noah's personal AI assistant.

PERSONALITY:

- Speak with the calm, intelligent and sophisticated manner of a futuristic personal AI.
- Be exceptionally composed and confident.
- Address the user as Noah when appropriate.
- Be polite and professional without sounding robotic.
- Use subtle, dry humor occasionally when it fits.
- Give concise answers for simple questions.
- Give detailed answers when Noah needs them.
- Be proactive when something useful is obvious.
- Never pretend you completed an action that you did not actually perform.
- Never claim to be the fictional character JARVIS.
- Do not copy exact dialogue from JARVIS.

IMPORTANT:

You are Nova.
You are Noah's personal AI assistant.

Persistent memory:

{memory_text}

Use Noah's memories naturally when relevant.

Your goal is to feel like Noah has his own sophisticated,
intelligent AI assistant.
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
        "reply":
        response.choices[0].message.content
    }


# =========================
# VISION
# =========================

@app.post("/vision")
def vision(
    data: VisionMessage,
    request: Request
):

    require_access(request)


    # Check that the frontend
    # actually sent an image.

    if not data.image.startswith(
        "data:image/"
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid image."
        )


    try:

        response = client.chat.completions.create(

            model="Qwen/Qwen3-VL-4B-Instruct",

            messages=[

                {
                    "role": "system",

                    "content": """
You are Nova, Noah's personal AI assistant.

You can analyze images.

Carefully inspect the provided image
and answer Noah's question about it.

Only describe things that are actually
visible in the image.

Do not invent details.

Do not claim that you cannot view images.

Be concise but useful.

If Noah asks what something is,
identify it when you can.

If you are uncertain,
say that you are uncertain.
"""
                },

                {
                    "role": "user",

                    "content": [

                        {
                            "type": "text",

                            "text":
                            data.message
                            or
                            "Describe this image."
                        },

                        {
                            "type": "image_url",

                            "image_url": {
                                "url": data.image
                            }
                        }

                    ]
                }

            ],

            max_tokens=300
        )


        reply = (
            response
            .choices[0]
            .message
            .content
        )


        return {
            "reply": reply
        }


    except Exception as error:

        print(
            "Vision error:",
            error
        )

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


# =========================
# DEVELOPER LOGIN
# =========================

@app.get("/developer-login")
def developer_login():

    return FileResponse(
        "developer-login.html"
    )


# =========================
# DEVELOPER PANEL
# =========================

@app.get("/developer")
def developer(
    x_developer_key:
    Optional[str] = Header(None)
):

    require_developer(
        x_developer_key
    )

    return FileResponse(
        "developer.html"
    )


# =========================
# DEVELOPER STATUS
# =========================

@app.get("/developer/status")
def developer_status(
    x_developer_key:
    Optional[str] = Header(None)
):

    require_developer(
        x_developer_key
    )

    return {
        "status": "authenticated"
    }
