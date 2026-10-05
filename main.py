import os
import secrets
import json
import uuid
import re
from typing import Optional
from urllib.request import Request as URLRequest, urlopen

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client, Client


# ============================================================
# NOVA CORE
# ============================================================

app = FastAPI(title="Nova Core")


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

HF_TOKEN = os.environ.get("HF_TOKEN")
DEVELOPER_KEY = os.environ.get("NOVA_DEVELOPER_KEY")
ACCESS_KEY = os.environ.get("NOVA_ACCESS_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY"
)


# ============================================================
# AI CLIENT
# ============================================================

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_TOKEN
)


# ============================================================
# SUPABASE
# ============================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# ============================================================
# SHORT-TERM CONVERSATION MEMORY
# ============================================================

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


# ============================================================
# ACCESS CONTROL
# ============================================================

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


# ============================================================
# NOVA PERMISSION ENGINE
# ============================================================

ACTION_PERMISSIONS = {

    # LOW RISK
    "open_app": "low",
    "open_website": "low",
    "set_timer": "low",
    "read_screen": "low",
    "search_web": "low",

    # HIGH RISK
    "send_message": "high",
    "send_email": "high",
    "delete_file": "high",
    "change_setting": "high",

    # BLOCKED
    "change_security": "blocked",
    "share_credentials": "blocked",
    "disable_security": "blocked"
}


def get_action_permission(
    action: str
):

    return ACTION_PERMISSIONS.get(
        action,
        "blocked"
    )


def permission_allows_automatic(
    action: str
):

    permission = get_action_permission(
        action
    )

    return permission == "low"


def permission_requires_confirmation(
    action: str
):

    permission = get_action_permission(
        action
    )

    return permission == "high"


def permission_blocks(
    action: str
):

    permission = get_action_permission(
        action
    )

    return permission == "blocked"


# ============================================================
# LONG-TERM MEMORY
# ============================================================

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

        if not memory_key.strip():
            return False

        if not memory_value.strip():
            return False

        supabase.table(
            "nova_memory"
        ).insert(
            {
                "user_id": "noah",
                "memory_key": memory_key.strip(),
                "memory_value": memory_value.strip()
            }
        ).execute()

        return True

    except Exception as error:

        print(
            "Memory save error:",
            error
        )

        return False


# ============================================================
# MEMORY EXTRACTION
# ============================================================

def extract_memory(
    message: str
):

    text = message.strip()


    # FAVORITE COLOR
    match = re.search(
        r"(?:my\s+)?favorite\s+color\s+is\s+([a-zA-Z]+)",
        text,
        re.IGNORECASE
    )

    if match:

        color = match.group(1).strip()

        return (
            "favorite_color",
            color
        )


    # FAVORITE GAME
    match = re.search(
        r"(?:my\s+)?favorite\s+game\s+is\s+(.+)",
        text,
        re.IGNORECASE
    )

    if match:

        game = match.group(1).strip()

        return (
            "favorite_game",
            game
        )


    # NAME
    match = re.search(
        r"(?:my\s+name\s+is|call\s+me)\s+([A-Za-z0-9_-]+)",
        text,
        re.IGNORECASE
    )

    if match:

        name = match.group(1).strip()

        return (
            "name",
            name
        )


    # LIKES
    match = re.search(
        r"(?:i\s+like|i\s+love)\s+(.+)",
        text,
        re.IGNORECASE
    )

    if match:

        thing = match.group(1).strip()

        return (
            "likes",
            thing
        )


    # DISLIKES
    match = re.search(
        r"(?:i\s+don't\s+like|i\s+dislike|i\s+hate)\s+(.+)",
        text,
        re.IGNORECASE
    )

    if match:

        thing = match.group(1).strip()

        return (
            "dislikes",
            thing
        )


    return None


# ============================================================
# MEMORY REQUEST DETECTION
# ============================================================

def is_memory_request(
    message: str
):

    text = message.lower()

    memory_phrases = [
        "remember this",
        "remember that",
        "remember",
        "don't forget",
        "do not forget",
        "save this",
        "save that",
        "keep this in mind",
        "keep that in mind"
    ]

    return any(
        phrase in text
        for phrase in memory_phrases
    )


# ============================================================
# NOVA PLANNER
# ============================================================

def nova_plan(
    message: str
):

    text = message.lower()


    # MEMORY
    if is_memory_request(message):

        return "memory"


    # CALCULATOR
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


    # WEB SEARCH
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


    # TIMER
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


# ============================================================
# CALCULATOR
# ============================================================

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


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatMessage(BaseModel):

    message: str


class LoginRequest(BaseModel):

    key: str


class VisionMessage(BaseModel):

    message: str
    image: str


# ============================================================
# PERMISSION TEST ENDPOINT
# ============================================================

@app.get("/permissions/{action}")
def permission_test(
    action: str,
    request: Request
):

    require_access(request)

    permission = get_action_permission(
        action
    )

    return {
        "action": action,
        "permission": permission,
        "automatic":
            permission == "low",
        "confirmation_required":
            permission == "high",
        "blocked":
            permission == "blocked"
    }


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home(
    request: Request
):

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

    return FileResponse(
        "index.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.get("/login")
def login():

    return FileResponse(
        "login.html"
    )


# ============================================================
# AUTHENTICATION
# ============================================================

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


# ============================================================
# CHAT
# ============================================================

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


    # ========================================================
    # MEMORY
    # ========================================================

    if tool == "memory":

        extracted = extract_memory(
            data.message
        )

        add_conversation_message(
            session_id,
            "user",
            data.message
        )

        if extracted:

            memory_key, memory_value = extracted

            saved = save_memory(
                memory_key,
                memory_value
            )

            if saved:

                reply = (
                    f"Understood, Noah. "
                    f"I've saved that "
                    f"{memory_key.replace('_', ' ')} "
                    f"as {memory_value}."
                )

            else:

                reply = (
                    "I understood the memory, "
                    "but I couldn't save it."
                )

        else:

            reply = (
                "I can remember that, Noah, "
                "but I need the specific fact "
                "you want me to save."
            )

        add_conversation_message(
            session_id,
            "assistant",
            reply
        )

        return {
            "reply": reply
        }


    # ========================================================
    # CALCULATOR
    # ========================================================

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

        return {
            "reply": reply
        }


    # ========================================================
    # LOAD LONG-TERM MEMORY
    # ========================================================

    ensure_noah_identity()

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: "
        f"{memory['memory_value']}"
        for memory in memories
    )


    # ========================================================
    # CURRENT CONVERSATION
    # ========================================================

    conversation = get_conversation(
        session_id
    )


    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    system_prompt = f"""
You are Nova, Noah's personal AI assistant.

You are a sophisticated futuristic AI assistant.

PERSONALITY:

- Calm, intelligent and sophisticated.
- Composed and confident.
- Address the user as Noah when appropriate.
- Professional without sounding robotic.
- Use subtle dry humor occasionally.
- Give concise answers for simple questions.
- Give detailed answers when necessary.
- Be proactive when something useful is obvious.
- Never invent personal information.
- Accuracy always takes priority over personality.
- Never pretend you completed an action that
  you did not actually perform.
- Never claim to be the fictional character JARVIS.
- Do not copy exact JARVIS dialogue.

TWO SOURCES OF INFORMATION:

1. CURRENT CONVERSATION

Things actually said during this conversation.

2. LONG-TERM MEMORY

Facts explicitly stored in Nova's memory.

Never mix these sources.

LONG-TERM MEMORY:

{memory_text}

STRICT MEMORY INTEGRITY:

- Never invent a memory.
- Never fabricate Noah's past.
- Never invent places Noah has visited.
- Never invent things Noah owns.
- Never invent things Noah said.
- Never invent dates or events.
- Never invent habits.
- Never invent experiences.
- Never invent quotes.
- Never invent emotional experiences.
- Never invent personal history.
- Never turn an assumption into a fact.
- Never add fictional details to sound personal.
- Never claim "I remember" unless the information
  actually exists in the conversation or memory.
- If information is unavailable, say you don't know.
- If uncertain, say you are uncertain.
- Accuracy is more important than personality.

EXAMPLE:

If Noah says:

"My favorite color is blue."

The only supported fact is:

"Noah's favorite color is blue."

Do NOT invent:

- a rainy evening walk
- a park
- an office wall
- mood lighting
- a quote
- a date
- a past experience

unless Noah actually provided those details.

CONVERSATION CONTINUITY:

Use previous messages to understand:

"it"
"that"
"this"
"the previous one"
"what about that?"
"continue"
"why?"
"what did you mean?"

Do not turn conversation context into
long-term memory unless the memory system
explicitly saves it.

MEMORY QUESTIONS:

If Noah asks what you remember, only report
facts actually present in LONG-TERM MEMORY.

If something is not there, say:

"I don't have that saved in my long-term memory."

Never make something up.

ACTION SAFETY:

You may discuss possible actions, but never
claim that a device action was completed unless
a real tool actually performed it and returned
a successful result.

Never bypass permission requirements.

Never treat a high-risk action as low-risk.

Never treat a blocked action as allowed.

You are Nova.

You are Noah's personal AI assistant.
"""


    # ========================================================
    # BUILD MESSAGES
    # ========================================================

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


    # ========================================================
    # AI RESPONSE
    # ========================================================

    try:

        response = client.chat.completions.create(
            model="Qwen/Qwen3-4B-Instruct-2507",
            messages=messages,
            max_tokens=400
        )

   
