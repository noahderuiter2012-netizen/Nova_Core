import os
import secrets
import json
from typing import Optional
from urllib.request import Request as URLRequest, urlopen

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client, Client


app = FastAPI(title="Nova Core")


# =========================================================
# CONFIGURATION
# =========================================================

HF_TOKEN = os.environ.get("HF_TOKEN")
DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")
ACCESS_KEY = os.environ.get("NOVA_ACCESS_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY"
)


# =========================================================
# AI CLIENT
# =========================================================

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_TOKEN
)


# =========================================================
# SUPABASE
# =========================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# =========================================================
# ACCESS CONTROL
# =========================================================

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

    if not secrets.compare_digest(
        session_key,
        ACCESS_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Nova access required."
        )


def require_developer(
    x_developer_key: Optional[str]
):

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


# =========================================================
# MEMORY
# =========================================================

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

            supabase.table(
                "nova_memory"
            ).insert(
                {
                    "user_id": "noah",
                    "memory_key": "name",
                    "memory_value": "Noah"
                }
            ).execute()

    except Exception as error:

        print(
            "Memory identity error:",
            error
        )


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

        print(
            "Memory read error:",
            error
        )

        return []


def save_memory(
    memory_key: str,
    memory_value: str
):

    try:

        supabase.table(
            "nova_memory"
        ).insert(
            {
                "user_id": "noah",
                "memory_key": memory_key,
                "memory_value": memory_value
            }
        ).execute()

        return True

    except Exception as error:

        print(
            "Memory save error:",
            error
        )

        return False


# =========================================================
# PLANNER
# =========================================================

def nova_plan(message: str) -> str:

    text = message.lower()

    if any(
        word in text
        for word in [
            "calculate",
            "what is",
            "how much is",
            "multiply",
            "divide",
            "plus",
            "minus"
        ]
    ):
        return "calculator"

    if any(
        word in text
        for word in [
            "search",
            "look up",
            "latest",
            "news",
            "what happened today"
        ]
    ):
        return "web_search"

    if any(
        word in text
        for word in [
            "remember",
            "don't forget",
            "save this",
            "keep in mind"
        ]
    ):
        return "memory"

    if any(
        word in text
        for word in [
            "timer",
            "countdown"
        ]
    ):
        return "timer"

    return "chat"


def nova_execute(
    plan: str,
    message: str
):

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


# =========================================================
# CALCULATOR
# =========================================================

def nova_calculate(
    expression: str
):

    try:

        allowed = "0123456789+-*/(). "

        cleaned = "".join(
            character
            for character in expression
            if character in allowed
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


# =========================================================
# REQUEST MODELS
# =========================================================

class ChatMessage(BaseModel):
    message: str


class LoginRequest(BaseModel):
    key: str


class VisionMessage(BaseModel):
    message: str
    image: str


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home(request: Request):

    session_key = request.cookies.get(
        "nova_access"
    )

    if not session_key or not ACCESS_KEY:

        return RedirectResponse(
            "/login"
        )

    if not secrets.compare_digest(
        session_key,
        ACCESS_KEY
    ):

        return RedirectResponse(
            "/login"
        )

    return FileResponse(
        "index.html"
    )


# =========================================================
# LOGIN
# =========================================================

@app.get("/login")
def login():

    return FileResponse(
        "login.html"
    )


# =========================================================
# AUTH
# =========================================================

@app.post("/auth")
def authenticate(
    data: LoginRequest
):

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


# =========================================================
# CHAT
# =========================================================

@app.post("/chat")
def chat(
    data: ChatMessage,
    request: Request
):

    require_access(request)

    plan = nova_plan(
        data.message
    )

    tool = nova_execute(
        plan,
        data.message
    )

    if tool == "calculator":

        result = nova_calculate(
            data.message
        )

        return {
            "reply": f"The result is {result}."
        }

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

    ensure_noah_identity()

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: "
        f"{memory['memory_value']}"
        for memory in memories
    )

    system_prompt = f"""
Current task plan:
{plan}

You are Nova, Noah's personal AI assistant.

PERSONALITY:

- Speak with the calm, intelligent and sophisticated manner of a futuristic personal AI.
- Be exceptionally composed and confident.
- Address the user as Noah when appropriate.
- Be polite and professional without sounding robotic.
- Use subtle, dry humor occasionally when appropriate.
- Give concise answers for simple questions.
- Give detailed answers when Noah needs them.
- Be proactive when something useful is obvious.
- Never pretend you completed an action that you did not actually perform.
- Never claim to be the fictional character JARVIS.
- Do not copy exact JARVIS dialogue.

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


# =========================================================
# VISION
# =========================================================

@app.post("/vision")
def vision(
    data: VisionMessage,
    request: Request
):

    require_access(request)

    try:

        response = client.chat.completions.create(
            model="Qwen/Qwen3-VL-30B-A3B-Instruct",
            messages=[
                {
                    "role": "system",
                    "content":
                    """
You are Nova, Noah's personal AI assistant.

You can understand images.

Analyze images carefully.

Only describe details that are actually visible.

If Noah asks a question about the image,
answer the question directly.

Be concise unless Noah asks for more detail.
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
            max_tokens=500
        )

        return {
            "reply":
            response.choices[0].message.content
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


# =========================================================
# VISION MODEL DIAGNOSTIC
# =========================================================

@app.get("/vision-models")
def vision_models(
    request: Request
):

    require_access(request)

    token = os.environ.get(
        "HF_TOKEN"
    )

    if not token:

        raise HTTPException(
            status_code=500,
            detail="HF_TOKEN is not configured."
        )

    request_url = URLRequest(
        "https://router.huggingface.co/v1/models",
        headers={
            "Authorization":
            f"Bearer {token}"
        }
    )

    try:

        with urlopen(
            request_url,
            timeout=20
        ) as response:

            payload = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

        all_models = payload.get(
            "data",
            []
        )

        vision_models_found = []

        vision_keywords = [
            "vision",
            "-vl-",
            "vl/",
            "vl-",
            "gemma-3",
            "gemma3",
            "glm-4.5v",
            "aya-vision"
        ]

        for item in all_models:

            model_id = item.get(
                "id",
                ""
            )

            lower_id = model_id.lower()

            if any(
                keyword in lower_id
                for keyword in vision_keywords
            ):

                vision_models_found.append(
                    model_id
                )

        return {
            "vision_models":
            vision_models_found
        }

    except Exception as error:

        print(
            "Vision model diagnostic error:",
            error
        )

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


# =========================================================
# DEVELOPER LOGIN
# =========================================================

@app.get("/developer-login")
def developer_login():

    return FileResponse(
        "developer-login.html"
    )


# =========================================================
# DEVELOPER PANEL
# =========================================================

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


# =========================================================
# DEVELOPER STATUS
# =========================================================

@app.get("/developer/status")
def developer_status(
    x_developer_key:
    Optional[str] = Header(None)
):

    require_developer(
        x_developer_key
    )

    return {
        "status":
        "authenticated"
    }
