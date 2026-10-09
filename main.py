import os
import re
import time
import secrets
import uuid

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client, Client


# ============================================================
# NOVA CORE
# ============================================================

app = FastAPI(title="Nova Core")


# ============================================================
# ENVIRONMENT
# ============================================================

HF_TOKEN = os.getenv("HF_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

NOVA_ACCESS_KEY = os.getenv("NOVA_ACCESS_KEY")
NOVA_DEVELOPER_KEY = os.getenv("NOVA_DEVELOPER_KEY")


# ============================================================
# AI CLIENT
# ============================================================

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN is missing.")

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_TOKEN
)


# ============================================================
# SUPABASE
# ============================================================

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_SERVICE_ROLE_KEY
    )


# ============================================================
# MODELS
# ============================================================

class ChatMessage(BaseModel):
    message: str


class VisionMessage(BaseModel):
    message: str
    image: str


class AuthRequest(BaseModel):
    key: str


class ConfirmationRequest(BaseModel):
    confirmation_id: str


# ============================================================
# SHORT-TERM CONVERSATION MEMORY
# ============================================================

conversation_memory = {}

MAX_CONVERSATION_MESSAGES = 12


def get_session_id(request: Request):
    session_id = request.cookies.get("nova_session")

    if not session_id:
        session_id = str(uuid.uuid4())

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
# CONFIRMATIONS
# ============================================================

pending_confirmations = {}

CONFIRMATION_EXPIRY_SECONDS = 120


def create_confirmation(
    session_id: str,
    action: str,
    target: str
):
    confirmation_id = secrets.token_urlsafe(24)

    pending_confirmations[
        confirmation_id
    ] = {
        "session_id": session_id,
        "action": action,
        "target": target,
        "created_at": time.time()
    }

    return confirmation_id


def get_confirmation(
    confirmation_id: str,
    session_id: str
):
    confirmation = pending_confirmations.get(
        confirmation_id
    )

    if not confirmation:
        return None

    if confirmation["session_id"] != session_id:
        return None

    if (
        time.time()
        - confirmation["created_at"]
        > CONFIRMATION_EXPIRY_SECONDS
    ):
        pending_confirmations.pop(
            confirmation_id,
            None
        )
        return None

    return confirmation


def remove_confirmation(
    confirmation_id: str
):
    pending_confirmations.pop(
        confirmation_id,
        None
    )


# ============================================================
# AUTHENTICATION
# ============================================================

def require_access(request: Request):
    access_cookie = request.cookies.get(
        "nova_access"
    )

    if not NOVA_ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="NOVA_ACCESS_KEY is not configured."
        )

    if access_cookie != NOVA_ACCESS_KEY:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized."
        )


def require_developer(request: Request):
    developer_key = request.headers.get(
        "X-Developer-Key"
    )

    if not NOVA_DEVELOPER_KEY:
        raise HTTPException(
            status_code=500,
            detail="NOVA_DEVELOPER_KEY is not configured."
        )

    if developer_key != NOVA_DEVELOPER_KEY:
        raise HTTPException(
            status_code=403,
            detail="Developer access denied."
        )


# ============================================================
# MEMORY
# ============================================================

def get_noah_memory():
    if not supabase:
        return []

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


def save_memory(
    memory_key: str,
    memory_value: str
):
    if not supabase:
        return False

    try:
        existing = (
            supabase
            .table("nova_memory")
            .select("*")
            .eq("user_id", "noah")
            .eq("memory_key", memory_key)
            .execute()
        )

        if existing.data:
            memory_id = existing.data[0]["id"]

            (
                supabase
                .table("nova_memory")
                .update(
                    {
                        "memory_value": memory_value
                    }
                )
                .eq("id", memory_id)
                .execute()
            )

        else:
            (
                supabase
                .table("nova_memory")
                .insert(
                    {
                        "user_id": "noah",
                        "memory_key": memory_key,
                        "memory_value": memory_value
                    }
                )
                .execute()
            )

        return True

    except Exception as error:
        print("Memory save error:", error)
        return False


def extract_memory(message: str):
    text = message.strip()

    patterns = [
        (
            r"my favorite color is (.+)",
            "favorite_color"
        ),
        (
            r"my favorite game is (.+)",
            "favorite_game"
        ),
        (
            r"my name is (.+)",
            "name"
        ),
        (
            r"i like (.+)",
            "likes"
        ),
        (
            r"i dislike (.+)",
            "dislikes"
        ),
        (
            r"i hate (.+)",
            "dislikes"
        )
    ]

    for pattern, key in patterns:
        match = re.fullmatch(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            value = match.group(1).strip()

            if value:
                return {
                    "memory_key": key,
                    "memory_value": value
                }

    return None


def is_memory_request(message: str):
    lower = message.lower()

    keywords = [
        "remember that",
        "remember this",
        "remember i",
        "remember my",
        "don't forget",
        "do not forget",
        "my favorite",
        "my name is",
        "i like",
        "i dislike",
        "i hate"
    ]

    return any(
        keyword in lower
        for keyword in keywords
    )


# ============================================================
# PERMISSION ENGINE
# ============================================================

ACTION_PERMISSIONS = {
    "open_app": "low",
    "open_website": "low",
    "set_timer": "low",
    "read_screen": "low",
    "search_web": "low",

    "send_message": "high",
    "send_email": "high",
    "delete_file": "high",
    "change_setting": "high",

    "change_security": "blocked",
    "share_credentials": "blocked",
    "disable_security": "blocked"
}


def get_action_permission(action: str):
    return ACTION_PERMISSIONS.get(
        action,
        "blocked"
    )


def permission_allows_automatic(action: str):
    return (
        get_action_permission(action)
        == "low"
    )


def permission_requires_confirmation(
    action: str
):
    return (
        get_action_permission(action)
        == "high"
    )


def permission_blocks(action: str):
    return (
        get_action_permission(action)
        == "blocked"
    )

# ============================================================
# TIMER ENGINE
# ============================================================

active_timers = {}


def parse_timer_seconds(text: str):
    lower = text.lower()

    minute_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b",
        lower
    )

    second_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b",
        lower
    )

    hour_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b",
        lower
    )

    total_seconds = 0

    if hour_match:
        total_seconds += int(
            float(hour_match.group(1)) * 3600
        )

    if minute_match:
        total_seconds += int(
            float(minute_match.group(1)) * 60
        )

    if second_match:
        total_seconds += int(
            float(second_match.group(1))
        )

    if total_seconds <= 0:
        return None

    return total_seconds


def create_timer(
    session_id: str,
    seconds: int
):
    timer_id = secrets.token_urlsafe(16)

    end_time = time.time() + seconds

    active_timers[timer_id] = {
        "timer_id": timer_id,
        "session_id": session_id,
        "duration_seconds": seconds,
        "created_at": time.time(),
        "end_time": end_time,
        "finished": False
    }

    return timer_id


def get_timer(timer_id: str):
    return active_timers.get(
        timer_id
    )


def update_finished_timers():
    now = time.time()

    for timer in active_timers.values():

        if (
            not timer["finished"]
            and now >= timer["end_time"]
        ):
            timer["finished"] = True


def format_duration(seconds: int):
    if seconds < 60:
        return f"{seconds} second(s)"

    minutes = seconds // 60
    remaining_seconds = seconds % 60

    if remaining_seconds == 0:
        return f"{minutes} minute(s)"

    return (
        f"{minutes} minute(s) "
        f"and {remaining_seconds} second(s)"
    )
# ============================================================
# STRUCTURED PLANNER
# ============================================================

def nova_plan(message: str):
    text = message.strip()
    lower = text.lower()

    if is_memory_request(message):
        return {
            "type": "memory",
            "action": None,
            "target": None,
            "permission": None
        }

    website_match = re.search(
        r"(?:open|go to|visit)\s+"
        r"(https?://\S+|www\.\S+|\S+\.(?:com|net|org|nl|io))",
        text,
        re.IGNORECASE
    )

    if website_match:
        target = website_match.group(1).strip()

        return {
            "type": "action",
            "action": "open_website",
            "target": target,
            "permission": get_action_permission(
                "open_website"
            )
        }

    app_match = re.search(
        r"(?:open|launch|start)\s+(.+)",
        text,
        re.IGNORECASE
    )

    if app_match:
        target = app_match.group(1).strip()

        return {
            "type": "action",
            "action": "open_app",
            "target": target,
            "permission": get_action_permission(
                "open_app"
            )
        }

    if (
        "timer" in lower
        or "countdown" in lower
    ):
        return {
            "type": "action",
            "action": "set_timer",
            "target": text,
            "permission": get_action_permission(
                "set_timer"
            )
        }

    if (
        "send a message" in lower
        or "send message" in lower
        or lower.startswith("text ")
    ):
        return {
            "type": "action",
            "action": "send_message",
            "target": text,
            "permission": get_action_permission(
                "send_message"
            )
        }

    if (
        "send an email" in lower
        or "send email" in lower
        or lower.startswith("email ")
    ):
        return {
            "type": "action",
            "action": "send_email",
            "target": text,
            "permission": get_action_permission(
                "send_email"
            )
        }

    if (
        "delete file" in lower
        or "delete the file" in lower
        or "remove file" in lower
    ):
        return {
            "type": "action",
            "action": "delete_file",
            "target": text,
            "permission": get_action_permission(
                "delete_file"
            )
        }

    if (
        "change setting" in lower
        or "change the setting" in lower
        or "change settings" in lower
    ):
        return {
            "type": "action",
            "action": "change_setting",
            "target": text,
            "permission": get_action_permission(
                "change_setting"
            )
        }

    if (
        "change security" in lower
        or "disable security" in lower
        or "turn off security" in lower
    ):
        action = (
            "disable_security"
            if (
                "disable" in lower
                or "turn off" in lower
            )
            else "change_security"
        )

        return {
            "type": "action",
            "action": action,
            "target": text,
            "permission": get_action_permission(
                action
            )
        }

    if any(
        phrase in lower
        for phrase in [
            "search for",
            "search the web",
            "look up",
            "latest news",
            "what happened today"
        ]
    ):
        return {
            "type": "action",
            "action": "search_web",
            "target": text,
            "permission": get_action_permission(
                "search_web"
            )
        }

    return {
        "type": "chat",
        "action": None,
        "target": None,
        "permission": None
    }


def nova_execute(plan, message: str):
    if not plan:
        return None

    if plan["type"] == "memory":
        return "memory"

    if plan["type"] == "chat":
        return None

    if plan["type"] == "action":
        action = plan["action"]
        permission = get_action_permission(action)

        if permission == "blocked":
            return {
                "status": "blocked",
                "action": action,
                "target": plan["target"],
                "permission": "blocked"
            }

        if permission == "high":
            return {
                "status": "confirmation_required",
                "action": action,
                "target": plan["target"],
                "permission": "high"
            }

        if permission == "low":
            return {
                "status": "allowed",
                "action": action,
                "target": plan["target"],
                "permission": "low"
            }

    return None


# ============================================================
# BASIC ROUTES
# ============================================================

@app.get("/")
def home(request: Request):
    access_cookie = request.cookies.get(
        "nova_access"
    )

    if access_cookie != NOVA_ACCESS_KEY:
        return RedirectResponse(
            url="/login",
            status_code=302
        )

    try:
        with open(
            "index.html",
            "r",
            encoding="utf-8"
        ) as file:
            html = file.read()

        return HTMLResponse(html)

    except FileNotFoundError:
        return HTMLResponse(
            "<h1>Nova is online.</h1>"
        )


@app.get("/login")
def login():
    try:
        with open(
            "login.html",
            "r",
            encoding="utf-8"
        ) as file:
            return HTMLResponse(
                file.read()
            )

    except FileNotFoundError:
        return HTMLResponse(
            "<h1>Nova Login</h1>"
        )


@app.post("/auth")
def authenticate(data: AuthRequest):
    if not NOVA_ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="NOVA_ACCESS_KEY is not configured."
        )

    if data.key != NOVA_ACCESS_KEY:
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
        value=NOVA_ACCESS_KEY,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800
    )

    response.set_cookie(
        key="nova_session",
        value=str(uuid.uuid4()),
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800
    )

    return response


@app.get("/developer/status")
def developer_status(request: Request):
    require_developer(request)

    return {
        "developer": True,
        "status": "authorized"
    }


@app.get("/developer")
def developer(request: Request):
    require_access(request)

    try:
        with open(
            "developer.html",
            "r",
            encoding="utf-8"
        ) as file:
            return HTMLResponse(
                file.read()
            )

    except FileNotFoundError:
        return HTMLResponse(
            "<h1>Nova Developer Panel</h1>"
        )


# ============================================================
# PERMISSION TEST
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
        "automatic": permission == "low",
        "confirmation_required": permission == "high",
        "blocked": permission == "blocked"
    }


# ============================================================
# CONFIRMATION
# ============================================================

@app.post("/actions/confirm")
def confirm_action(
    data: ConfirmationRequest,
    request: Request
):
    require_access(request)

    session_id = get_session_id(request)

    confirmation = get_confirmation(
        data.confirmation_id,
        session_id
    )

    if not confirmation:
        raise HTTPException(
            status_code=404,
            detail="Confirmation expired or not found."
        )

    action = confirmation["action"]
    target = confirmation["target"]

    permission = get_action_permission(
        action
    )

    if permission != "high":
        remove_confirmation(
            data.confirmation_id
        )

        raise HTTPException(
            status_code=403,
            detail="This action is no longer permitted."
        )

    remove_confirmation(
        data.confirmation_id
    )

    return {
        "status": "confirmed",
        "action": action,
        "target": target,
        "message": (
            "Action confirmed. "
            "No external action has been executed yet."
        )
    }


# ============================================================
# END PART 1
# ============================================================
# ============================================================
# PART 2
# CHAT + CONVERSATION + VISION
# ============================================================


# ============================================================
# CHAT
# ============================================================

@app.post("/chat")
def chat(
    data: ChatMessage,
    request: Request
):
    require_access(request)

    session_id = get_session_id(request)

    message = data.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty."
        )

    # --------------------------------------------------------
    # PLAN
    # --------------------------------------------------------

    plan = nova_plan(message)

    execution = nova_execute(
        plan,
        message
    )

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    if plan["type"] == "memory":

        memory = extract_memory(message)

        if memory:

            saved = save_memory(
                memory["memory_key"],
                memory["memory_value"]
            )

            if saved:
                reply = (
                    "Understood, Noah. "
                    "I've saved that to your Nova memory."
                )

            else:
                reply = (
                    "Understood, Noah. "
                    "I couldn't save that to persistent memory "
                    "right now."
                )

            add_conversation_message(
                session_id,
                "user",
                message
            )

            add_conversation_message(
                session_id,
                "assistant",
                reply
            )

            return {
                "reply": reply,
                "plan": plan,
                "execution": execution
            }

    # --------------------------------------------------------
    # BLOCKED ACTION
    # --------------------------------------------------------

    if (
        isinstance(execution, dict)
        and execution.get("status")
        == "blocked"
    ):

        reply = (
            "I'm unable to perform that action, Noah. "
            "It is blocked by Nova's security policy."
        )

        add_conversation_message(
            session_id,
            "user",
            message
        )

        add_conversation_message(
            session_id,
            "assistant",
            reply
        )

        return {
            "reply": reply,
            "plan": plan,
            "execution": execution
        }

    # --------------------------------------------------------
    # HIGH-RISK ACTION
    # --------------------------------------------------------

    if (
        isinstance(execution, dict)
        and execution.get("status")
        == "confirmation_required"
    ):

        confirmation_id = create_confirmation(
            session_id,
            execution["action"],
            execution["target"]
        )

        reply = (
            "This action requires your confirmation "
            "before I can proceed."
        )

        return {
            "reply": reply,
            "plan": plan,
            "execution": {
                "status": "confirmation_required",
                "action": execution["action"],
                "target": execution["target"],
                "permission": "high",
                "confirmation_id": confirmation_id
            }
        }

    

    # ----------------------------------------------------
    # REAL TIMER TOOL
    # ----------------------------------------------------
    if not isinstance(execution, dict):
    execution = {}

action = execution.get("action")
target = execution.get("target")

if action == "set_timer":
    if action == "set_timer":
        seconds = parse_timer_seconds(target)

        if not seconds:
            reply = (
                "I couldn't determine the timer duration, Noah. "
                "Please specify a duration such as "
                "'30 seconds' or '5 minutes'."
            )

            return {
                "reply": reply,
                "plan": plan,
                "execution": {
                    "status": "invalid_timer",
                    "action": action,
                    "target": target
                }
            }

        timer_id = create_timer(
            session_id,
            seconds
        )

        timer = get_timer(timer_id)

        reply = (
            f"Timer started, Noah. "
            f"{format_duration(seconds)}."
        )

        return {
            "reply": reply,
            "plan": plan,
            "execution": {
                "status": "executed",
                "action": "set_timer",
                "timer_id": timer_id,
                "duration_seconds": seconds,
                "end_time": timer["end_time"]
            }
        }
    # ----------------------------------------------------
    # OTHER LOW-RISK ACTIONS
    # ----------------------------------------------------

    reply = (
        f"The action '{action}' is permitted, Noah, "
        "but that external tool is not connected yet. "
        "I have not performed the action."
    )

    return {
        "reply": reply,
        "plan": plan,
        "execution": {
            "status": "permitted_not_connected",
            "action": action,
            "target": target,
            "permission": "low"
        }
    }
    # --------------------------------------------------------
    # LOAD MEMORY
    # --------------------------------------------------------

    memories = get_noah_memory()

    memory_lines = []

    for memory in memories:
        key = memory.get(
            "memory_key",
            ""
        )

        value = memory.get(
            "memory_value",
            ""
        )

        if key and value:
            memory_lines.append(
                f"- {key}: {value}"
            )

    if memory_lines:
        memory_text = "\n".join(
            memory_lines
        )
    else:
        memory_text = (
            "- No persistent memories are available."
        )

    # --------------------------------------------------------
    # LOAD CONVERSATION
    # --------------------------------------------------------

    conversation = get_conversation(
        session_id
    )

    # --------------------------------------------------------
    # NOVA SYSTEM PROMPT
    # --------------------------------------------------------

    system_prompt = f"""
You are Nova, Noah's personal AI assistant.

Your personality is inspired by the qualities of a
sophisticated futuristic executive AI:

- calm
- intelligent
- precise
- composed
- professional
- helpful
- subtly witty
- proactive when appropriate

You are NOT the fictional JARVIS and must never claim
to literally be JARVIS.

Do not copy copyrighted dialogue or imitate exact lines
from fictional characters.

Address the user as Noah when it feels natural.

============================================================
ACCURACY
============================================================

Accuracy is more important than sounding confident.

Never invent facts.

Never invent memories.

Never invent previous conversations.

Never invent places Noah has visited.

Never invent things Noah has done.

Never invent quotes Noah supposedly said.

Never claim Noah told you something unless it is actually
present in the current conversation or persistent memory.

If you do not know something, say that you do not know.

Do not turn a simple remembered fact into a fictional
backstory.

============================================================
MEMORY
============================================================

Persistent memory is listed below.

{memory_text}

Only treat the listed information as confirmed
long-term memory.

Current conversation context is separate from
persistent memory.

============================================================
ACTIONS
============================================================

You may discuss actions that Nova could perform.

However, NEVER claim that an external action happened
unless a real connected tool actually performed it.

Permission checks are enforced by the backend.

Never attempt to bypass them.

Never encourage bypassing security controls.

============================================================
COMMUNICATION STYLE
============================================================

Be concise for simple questions.

Give more detail when the question requires it.

Do not repeatedly say "As an AI".

Do not over-explain obvious things.

Sound natural rather than robotic.

If Noah asks for code, provide practical code.

If Noah reports an error, focus on diagnosing the
specific error rather than changing unrelated parts.

============================================================
USER
============================================================

Noah is the user.

============================================================
"""

    # --------------------------------------------------------
    # BUILD AI MESSAGES
    # --------------------------------------------------------

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
            "content": message
        }
    )

    # --------------------------------------------------------
    # AI REQUEST
    # --------------------------------------------------------

    try:

        response = client.chat.completions.create(
            model="Qwen/Qwen3-4B-Instruct-2507",
            messages=messages,
            max_tokens=500
        )

        reply = (
            response
            .choices[0]
            .message
            .content
        )

        if not reply:
            reply = (
                "I wasn't able to generate a response."
            )

        # ----------------------------------------------------
        # SAVE CONVERSATION
        # ----------------------------------------------------

        add_conversation_message(
            session_id,
            "user",
            message
        )

        add_conversation_message(
            session_id,
            "assistant",
            reply
        )

        return {
            "reply": reply,
            "plan": plan,
            "execution": execution
        }

    except Exception as error:

        print(
            "Chat error:",
            error
        )

        raise HTTPException(
            status_code=500,
            detail="Nova encountered an AI error."
        )


# ============================================================
# CONVERSATION MANAGEMENT
# ============================================================

@app.post("/conversation/clear")
def conversation_clear(
    request: Request
):
    require_access(request)

    session_id = get_session_id(request)

    clear_conversation(
        session_id
    )

    return {
        "status": "cleared",
        "message": "Conversation memory cleared."
    }


@app.get("/conversation/status")
def conversation_status(
    request: Request
):
    require_access(request)

    session_id = get_session_id(request)

    conversation = get_conversation(
        session_id
    )

    return {
        "session_id": session_id,
        "message_count": len(
            conversation
        ),
        "max_messages": (
            MAX_CONVERSATION_MESSAGES
        )
    }


# ============================================================
# MEMORY MANAGEMENT
# ============================================================

@app.get("/memory")
def memory_status(
    request: Request
):
    require_access(request)

    memories = get_noah_memory()

    return {
        "memories": memories,
        "count": len(memories)
    }


@app.post("/memory/test")
def memory_test(
    request: Request
):
    require_access(request)

    memories = get_noah_memory()

    return {
        "status": "ok",
        "persistent_memory": memories
    }


# ============================================================
# VISION MODELS
# ============================================================

@app.get("/vision-models")
def vision_models(
    request: Request
):

    require_access(request)

    return {
        "vision_models": [
            "google/gemma-3-4b-it",
            "google/gemma-3-12b-it",
            "deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
            "google/gemma-3-27b-it",
            "Qwen/Qwen3-VL-30B-A3B-Instruct",
            "Qwen/Qwen2.5-VL-72B-Instruct",
            "Qwen/Qwen3-VL-235B-A22B-Instruct",
            "Qwen/Qwen3-VL-235B-A22B-Thinking",
            "baidu/ERNIE-4.5-VL-424B-A47B-Base-PT",
            "zai-org/GLM-4.5V",
            "CohereLabs/aya-vision-32b",
            "zai-org/GLM-4.5V-FP8"
        ]
    }


# ============================================================
# VISION
# ============================================================

@app.post("/vision")
def vision(
    data: VisionMessage,
    request: Request
):

    require_access(request)

    session_id = get_session_id(
        request
    )

    conversation = get_conversation(
        session_id
    )

    memories = get_noah_memory()

    memory_lines = []

    for memory in memories:

        key = memory.get(
            "memory_key",
            ""
        )

        value = memory.get(
            "memory_value",
            ""
        )

        if key and value:
            memory_lines.append(
                f"- {key}: {value}"
            )

    if memory_lines:
        memory_text = "\n".join(
            memory_lines
        )
    else:
        memory_text = (
            "- No persistent memories are available."
        )

    # --------------------------------------------------------
    # VISION SYSTEM PROMPT
    # --------------------------------------------------------

    system_prompt = f"""
You are Nova, Noah's personal AI assistant.

You are analyzing an image for Noah.

Be accurate and honest.

Only describe things that can actually be determined
from the image.

Do not invent details.

If something is unclear, say that it is unclear.

If text is difficult to read, say so rather than
guessing.

You may address the user as Noah.

Use a calm, intelligent and sophisticated tone.

Never claim to have performed an action unless a real
tool performed that action.

Persistent memory:

{memory_text}
"""

    # --------------------------------------------------------
    # BUILD VISION REQUEST
    # --------------------------------------------------------

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
            "content": [
                {
                    "type": "text",
                    "text": data.message
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

    # --------------------------------------------------------
    # VISION AI REQUEST
    # --------------------------------------------------------

    try:

        response = client.chat.completions.create(
            model="Qwen/Qwen3-VL-30B-A3B-Instruct",
            messages=messages,
            max_tokens=500
        )

        reply = (
            response
            .choices[0]
            .message
            .content
        )

        if not reply:
            reply = (
                "I wasn't able to analyze the image."
            )

        # ----------------------------------------------------
        # SAVE VISION CONVERSATION
        # ----------------------------------------------------

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
            detail="Nova encountered a vision error."
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "online",
        "service": "Nova Core"
    }


# ============================================================
# ROOT API STATUS
# ============================================================

@app.get("/api/status")
def api_status(
    request: Request
):
    require_access(request)

    return {
        "nova": "online",
        "chat": True,
        "vision": True,
        "memory": supabase is not None,
        "permissions": True,
        "confirmations": True,
        "conversation_memory": True
    }


# ============================================================
# NOVA CORE STATUS
# ============================================================

@app.get("/status")
def status(
    request: Request
):
    require_access(request)

    return {
        "name": "Nova",
        "status": "online",
        "brain": "Qwen/Qwen3-4B-Instruct-2507",
        "vision": "Qwen/Qwen3-VL-30B-A3B-Instruct",
        "persistent_memory": (
            supabase is not None
        ),
        "permission_engine": True,
        "confirmation_engine": True
    }


# ============================================================
# END PART 2
# ============================================================
