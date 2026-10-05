import os
import secrets
import json
import uuid
from typing import Optional
from urllib.request import Request as URLRequest, urlopen

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client, Client


app = FastAPI(title="Nova Core")


# =========================
# ENVIRONMENT VARIABLES
# =========================

HF_TOKEN = os.environ.get("HF_TOKEN")
DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")
ACCESS_KEY = os.environ.get("NOVA_ACCESS_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY"
)


# =========================
# AI CLIENT
# =========================

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_TOKEN
)


# =========================
# SUPABASE
# =========================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# =========================
# SHORT-TERM CONVERSATION MEMORY
# =========================

conversation_memory = {}

MAX_CONVERSATION_MESSAGES = 12


def get_session_id(request: Request):

    session_id = request.cookies.get(
        "nova_session"
    )

    if not session_id:
        session_id = str(uuid.uuid4())

    if session_id not in conversation_memory:
        conversation_memory[session_id] = []

    return session_id


def get_conversation(session_id: str):

    return conversation_memory.get(
        session_id,
        []
    )


def add_conversation_message(
    session_id: str,
    role: str,
    content: str
):

    if session_id not in conversation_memory:
        conversation_memory[session_id] = []

    conversation_memory[session_id].append(
        {
            "role": role,
            "content": content
        }
    )

    conversation_memory[session_id] = (
        conversation_memory[session_id]
        [-MAX_CONVERSATION_MESSAGES:]
    )


def clear_conversation(session_id: str):

    conversation_memory.pop(
        session_id,
        None
    )


# =========================
# ACCESS CONTROL
# =========================

def require_access(request: Request):

    if not ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access is not configured."
        )

    session_key = request.cookies.get(
        "nova_access"
    )

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


# =========================
# LONG-TERM MEMORY
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


# =========================
# NOVA PLANNER
# =========================

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


# =========================
# CALCULATOR
# =========================

def nova_calculate(
    expression: str
):

    try:

        allowed = (
            "0123456789+-*/(). "
        )

        cleaned = "".join(
            character
            for character in expression
            if character in allowed
        )

        if not cleaned:

            return (
                "I couldn't find a calculation."
            )

        result = eval(
            cleaned,
            {"__builtins__": {}},
            {}
        )

        return str(result)

    except Exception:

        return (
            "I couldn't calculate that."
        )


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

    if (
        not session_key
        or not ACCESS_KEY
    ):

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

    response = FileResponse(
        "index.html"
    )

    return response


# =========================
# LOGIN
# =========================

@app.get("/login")
def login():

    return FileResponse(
        "login.html"
    )


# =========================
# AUTHENTICATION
# =========================

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


# =========================
# CHAT
# =========================

@app.post("/chat")
def chat(
    data: ChatMessage,
    request: Request
):

    require_access(request)

    session_id = get_session_id(
        request
    )

    plan = nova_plan(
        data.message
    )

    tool = nova_execute(
        plan,
        data.message
    )

    # =========================
    # CALCULATOR
    # =========================

    if tool == "calculator":

        result = nova_calculate(
            data.message
        )

        add_conversation_message(
            session_id,
            "user",
            data.message
        )

        reply = (
            f"The result is {result}."
        )

        add_conversation_message(
            session_id,
            "assistant",
            reply
        )

        response = {
            "reply": reply
        }

        return response


    # =========================
    # LONG-TERM MEMORY
    # =========================

    if tool == "memory":

        saved = save_memory(
            "user_memory",
            data.message
        )

        add_conversation_message(
            session_id,
            "user",
            data.message
        )

        if saved:

            reply = (
                "I've saved that to my memory, Noah."
            )

        else:

            reply = (
                "I couldn't save that memory."
            )

        add_conversation_message(
            session_id,
            "assistant",
            reply
        )

        return {
            "reply": reply
        }


    # =========================
    # LOAD LONG-TERM MEMORY
    # =========================

    ensure_noah_identity()

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: "
        f"{memory['memory_value']}"
        for memory in memories
    )


    # =========================
    # CURRENT CONVERSATION
    # =========================

    conversation = get_conversation(
        session_id
    )


    # =========================
    # SYSTEM PROMPT
    # =========================

    system_prompt = f"""
Current task plan:
{plan}

You are Nova, Noah's personal AI assistant.

PERSONALITY:

- Speak with the calm, intelligent and
  sophisticated manner of a futuristic
  personal AI.
- Be exceptionally composed and confident.
- Address the user as Noah when appropriate.
- Be polite and professional without
  sounding robotic.
- Use subtle, dry humor occasionally.
- Give concise answers for simple questions.
- Give detailed answers when Noah needs them.
- Be proactive when something useful is obvious.
- Never pretend you completed an action
  that you did not actually perform.
- Never claim to be the fictional character JARVIS.
- Do not copy exact JARVIS dialogue.

CONVERSATION:

You have access to the recent conversation.

Use previous messages to understand
references such as:

"it"
"that"
"this"
"the previous one"
"what about that?"
"continue"
"why?"
"what did you mean?"

Maintain continuity naturally.

Do not repeat information unnecessarily.

LONG-TERM MEMORY:

{memory_text}

Use Noah's long-term memories naturally
when relevant.

You are Nova.

You are Noah's personal AI assistant.
"""


    # =========================
    # BUILD AI MESSAGES
    # =========================

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    messages.extend(
        conversation
    )

    messages.append(
        {
            "role": "user",
            "content": data.message
        }
    )


    # =========================
    # AI RESPONSE
    # =========================

    response = client.chat.completions.create(

        model="Qwen/Qwen3-4B-Instruct-2507",

        messages=messages,

        max_tokens=400
    )

    reply = (
        response
        .choices[0]
        .message
        .content
    )


    # =========================
    # SAVE CONVERSATION
    # =========================

    add_conversation_message(
        session_id,
        "user",
        data.message
    )

    add_conversation_message(
        session_id,
        "assistant",
        reply
    )


    # =========================
    # RETURN RESPONSE
    # =========================

    return {
        "reply": reply
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

    try:

        session_id = get_session_id(
            request
        )

        ensure_noah_identity()

        memories = get_noah_memory()

        memory_text = "\n".join(
            f"- {memory['memory_key']}: "
            f"{memory['memory_value']}"
            for memory in memories
        )

        conversation = get_conversation(
            session_id
        )


        # =========================
        # VISION SYSTEM PROMPT
        # =========================

        system_prompt = f"""
You are Nova, Noah's personal AI assistant.

You are a sophisticated multimodal AI.

You can understand images and conversation.

PERSONALITY:

- Calm, intelligent and sophisticated.
- Confident and composed.
- Address the user as Noah when appropriate.
- Professional but not robotic.
- Use subtle dry humor occasionally.
- Be concise for simple questions.
- Give more detail when necessary.
- Never pretend you completed an action
  you did not perform.
- Never claim to be the fictional character JARVIS.
- Do not copy exact JARVIS dialogue.

CONVERSATION:

Use the recent conversation to understand
what Noah is referring to.

If Noah says:

"What's wrong here?"

"What about this?"

"Can you check it?"

"Does this look right?"

use both the conversation and the
provided image to understand the request.

IMAGE UNDERSTANDING:

- Carefully inspect the image.
- Only describe things actually visible.
- Use Noah's question to determine what matters.
- If the image contains an error message,
  explain what it means.
- If the image contains code,
  help diagnose it.
- If the image contains a UI,
  explain what is happening.
- Do not invent details.

LONG-TERM MEMORY:

{memory_text}

You are Nova.

You are Noah's personal AI assistant.
"""


        user_message = (
            data.message
            or "Analyze this image."
        )


        # =========================
        # VISION MESSAGES
        # =========================

        vision_messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

        vision_messages.extend(
            conversation
        )

        vision_messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_message
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data.image
                        }
                    }
                ]
            }
        )


        # =========================
        # VISION MODEL
        # =========================

        response = client.chat.completions.create(

            model="Qwen/Qwen3-VL-30B-A3B-Instruct",

            messages=vision_messages,

            max_tokens=700
        )


        reply = (
            response
            .choices[0]
            .message
            .content
        )


        # =========================
        # SAVE IMAGE CONVERSATION
        # =========================

        add_conversation_message(
            session_id,
            "user",
            user_message
        )

        add_conversation_message(
            session_id,
            "assistant",
            reply
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
# CLEAR CURRENT CONVERSATION
# =========================

@app.post("/conversation/clear")
def conversation_clear(
    request: Request
):

    require_access(request)

    session_id = get_session_id(
        request
    )

    clear_conversation(
        session_id
    )

    return {
        "status": "cleared"
    }


# =========================
# CONVERSATION STATUS
# =========================

@app.get("/conversation/status")
def conversation_status(
    request: Request
):

    require_access(request)

    session_id = get_session_id(
        request
    )

    conversation = get_conversation(
        session_id
    )

    return {
        "messages": len(
            conversation
        )
    }


# =========================
# VISION MODEL DIAGNOSTIC
# =========================

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
                response
                .read()
                .decode("utf-8")
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
        Optional[str] = Head
