import os
import re
import time
import uuid
import secrets

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from supabase import create_client

app = FastAPI(title="Nova Core")

HF_TOKEN = os.getenv("HF_TOKEN")
NOVA_ACCESS_KEY = os.getenv("NOVA_ACCESS_KEY")
NOVA_DEVELOPER_KEY = os.getenv("NOVA_DEVELOPER_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_TOKEN
)

supabase = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_SERVICE_ROLE_KEY
        )
        print("Supabase connected.")
    except Exception as error:
        print("Supabase connection error:", error)


# ============================================================
# MODELS
# ============================================================

class ChatMessage(BaseModel):
    message: str


class LoginRequest(BaseModel):
    key: str


class VisionMessage(BaseModel):
    message: str
    image: str


class ConfirmationRequest(BaseModel):
    confirmation_id: str


# ============================================================
# CONVERSATION MEMORY
# ============================================================

conversation_memory = {}
MAX_CONVERSATION_MESSAGES = 12


def get_session_id(request: Request):
    session_id = request.cookies.get("nova_session")

    if session_id:
        return session_id

    return str(uuid.uuid4())


def get_conversation(session_id):
    return conversation_memory.get(session_id, [])


def add_conversation_message(
    session_id,
    role,
    content
):
    if session_id not in conversation_memory:
        conversation_memory[session_id] = []

    conversation_memory[session_id].append({
        "role": role,
        "content": content
    })

    conversation_memory[session_id] = (
        conversation_memory[session_id]
        [-MAX_CONVERSATION_MESSAGES:]
    )


def clear_conversation(session_id):
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
    session_id,
    action,
    target
):
    confirmation_id = secrets.token_urlsafe(24)

    pending_confirmations[confirmation_id] = {
        "session_id": session_id,
        "action": action,
        "target": target,
        "created_at": time.time()
    }

    return confirmation_id


def get_confirmation(
    confirmation_id,
    session_id
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
    confirmation_id
):
    pending_confirmations.pop(
        confirmation_id,
        None
    )


# ============================================================
# AUTH
# ============================================================

def require_access(request: Request):

    access_cookie = request.cookies.get(
        "nova_access"
    )

    if not access_cookie:
        raise HTTPException(
            status_code=401,
            detail="Nova access required."
        )

    if not NOVA_ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access key is not configured."
        )

    if not secrets.compare_digest(
        access_cookie,
        NOVA_ACCESS_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Nova access."
        )


def require_developer(request: Request):

    developer_key = request.headers.get(
        "X-Developer-Key"
    )

    if not developer_key:
        raise HTTPException(
            status_code=401,
            detail="Developer access required."
        )

    if not NOVA_DEVELOPER_KEY:
        raise HTTPException(
            status_code=500,
            detail="Developer key is not configured."
        )

    if not secrets.compare_digest(
        developer_key,
        NOVA_DEVELOPER_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid developer key."
        )


# ============================================================
# SUPABASE MEMORY
# ============================================================

def save_memory(
    memory_key,
    memory_value
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

            row_id = existing.data[0]["id"]

            (
                supabase
                .table("nova_memory")
                .update({
                    "memory_value": memory_value
                })
                .eq("id", row_id)
                .execute()
            )

        else:

            (
                supabase
                .table("nova_memory")
                .insert({
                    "user_id": "noah",
                    "memory_key": memory_key,
                    "memory_value": memory_value
                })
                .execute()
            )

        return True

    except Exception as error:

        print(
            "Memory save error:",
            error
        )

        return False


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

        print(
            "Memory read error:",
            error
        )

        return []


def ensure_noah_identity():

    memories = get_noah_memory()

    if not any(
        memory.get("memory_key") == "name"
        for memory in memories
    ):
        save_memory(
            "name",
            "Noah"
        )


# ============================================================
# MEMORY EXTRACTION
# ============================================================

def extract_memory(message):

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
            r"i love (.+)",
            "likes"
        ),
        (
            r"i dislike (.+)",
            "dislikes"
        ),
        (
            r"i don't like (.+)",
            "dislikes"
        )
    ]

    for pattern, key in patterns:

        match = re.match(
            pattern,
            message.strip(),
            re.IGNORECASE
        )

        if match:

            value = match.group(1).strip()

            if value:
                return key, value

    return None


def is_memory_request(message):

    lower = message.lower()

    phrases = [
        "remember that",
        "remember this",
        "remember my",
        "remember i",
        "save that",
        "save this",
        "save my",
        "don't forget",
        "do not forget"
    ]

    return any(
        phrase in lower
        for phrase in phrases
    )


# ============================================================
# PERMISSIONS
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


def get_action_permission(action):

    return ACTION_PERMISSIONS.get(
        action,
        "blocked"
    )


def permission_allows_automatic(action):

    return (
        get_action_permission(action)
        == "low"
    )


def permission_requires_confirmation(action):

    return (
        get_action_permission(action)
        == "high"
    )


def permission_blocks(action):

    return (
        get_action_permission(action)
        == "blocked"
    )


# ============================================================
# STRUCTURED PLANNER
# ============================================================

def nova_plan(message):

    text = message.strip()
    lower = text.lower()

    if is_memory_request(message):

        return {
            "type": "memory",
            "action": None,
            "target": None,
            "permission": None
        }

    # Website BEFORE generic open-app detection

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

        if (
            "disable" in lower
            or "turn off" in lower
        ):
            action = "disable_security"
        else:
            action = "change_security"

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


def nova_execute(
    plan,
    message
):

    if not plan:
        return None

    if plan["type"] == "memory":
        return "memory"

    if plan["type"] == "chat":
        return None

    if plan["type"] == "action":

        action = plan["action"]

        permission = get_action_permission(
            action
        )

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
# ROOT / LOGIN
# ============================================================

@app.get("/")
def root(request: Request):

    access_cookie = request.cookies.get(
        "nova_access"
    )

    if not access_cookie:
        return RedirectResponse("/login")

    if NOVA_ACCESS_KEY:

        if not secrets.compare_digest(
            access_cookie,
            NOVA_ACCESS_KEY
        ):
            return RedirectResponse("/login")

    return FileResponse("index.html")


@app.get("/login")
def login_page():
    return FileResponse("login.html")


@app.post("/auth")
def authenticate(data: LoginRequest):

    if not NOVA_ACCESS_KEY:
        raise HTTPException(
            status_code=500,
            detail="Nova access key is not configured."
        )

    if not secrets.compare_digest(
        data.key,
        NOVA_ACCESS_KEY
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
        value=NOVA_ACCESS_KEY,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800
    )

    return response


# ============================================================
# DEVELOPER
# ============================================================

@app.get("/developer-login")
def developer_login():
    return FileResponse(
        "developer-login.html"
    )


@app.get("/developer")
def developer_page(request: Request):

    require_developer(request)

    return FileResponse(
        "developer.html"
    )


@app.get("/developer/status")
def developer_status(request: Request):

    require_developer(request)

    return {
        "status": "developer access granted",
        "nova": "online"
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "online",
        "nova": "Nova Core"
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
def memory_status(request: Request):

    require_access(request)

    return {
        "memories": get_noah_memory()
    }


# ============================================================
# CONVERSATION
# ============================================================

@app.post("/conversation/clear")
def conversation_clear(request: Request):

    require_access(request)

    session_id = get_session_id(request)

    clear_conversation(
        session_id
    )

    return {
        "status": "cleared"
    }


@app.get("/conversation/status")
def conversation_status(request: Request):

    require_access(request)

    session_id = get_session_id(request)

    conversation = get_conversation(
        session_id
    )

    return {
        "session_id": session_id,
        "messages": len(conversation)
    }


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

    session_id = get_session_id(
        request
    )

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

    # Always re-check the permission.

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

    # Consume the confirmation.

    remove_confirmation(
        data.confirmation_id
    )

    # No real external action is executed yet.

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


    return None

    


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

    plan = nova_plan(data.message)

    execution = nova_execute(
        plan,
        data.message
    )


    # ========================================================
    # MEMORY REQUEST
    # ========================================================

    if plan["type"] == "memory":

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
            "reply": reply,
            "plan": plan
        }


    # ========================================================
    # BLOCKED ACTION
    # ========================================================

    if (
        execution
        and execution.get("status")
        == "blocked"
    ):

        reply = (
            f"I can't perform "
            f"'{execution['action']}'. "
            f"That action is blocked by "
            f"Nova's security policy."
        )

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
            "reply": reply,
            "plan": plan,
            "execution": execution
        }


    # ========================================================
    # CONFIRMATION REQUIRED
    # ========================================================

    if (
        execution
        and execution.get("status")
        == "confirmation_required"
    ):

        confirmation_id = create_confirmation(
            session_id,
            execution["action"],
            execution["target"]
        )

        reply = (
            f"That action requires your confirmation, Noah.\n\n"
            f"Action: {execution['action']}\n"
            f"Target: {execution['target']}\n\n"
            f"Confirmation ID: {confirmation_id}\n\n"
            f"No action has been executed."
        )

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
            "reply": reply,
            "plan": plan,
            "execution": execution,
            "confirmation_id": confirmation_id
        }


    # ========================================================
    # LOW-RISK ACTION
    # ========================================================

    if (
        execution
        and execution.get("status")
        == "allowed"
    ):

        reply = (
            f"That action is permitted, Noah. "
            f"Requested action: "
            f"{execution['action']}."
        )

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
            "reply": reply,
            "plan": plan,
            "execution": execution
        }


    # ========================================================
    # AI CHAT
    # ========================================================

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
- Be proactive when useful.
- Never invent personal information.
- Accuracy always takes priority over personality.
- Never pretend you completed an action.
- Never claim to be the fictional character JARVIS.
- Do not copy exact JARVIS dialogue.

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
- Never turn assumptions into facts.
- Never add fictional details to sound personal.
- Never claim "I remember" unless the information
  actually exists in the conversation or memory.
- If information is unavailable, say you don't know.

ACTION SAFETY:

- Never claim an action was completed unless
  a real tool actually performed it.
- Never bypass the permission system.
- Never treat high-risk actions as low-risk.
- Never treat blocked actions as allowed.

You are Nova.
You are Noah's personal AI assistant.
"""

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

    try:

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
            "reply": reply,
            "plan": plan
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
# VISION MODELS
# ============================================================

@app.get("/vision-models")
def vision_models(request: Request):

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

    session_id = get_session_id(request)

    conversation = get_conversation(
        session_id
    )

    memories = get_noah_memory()

    memory_text = "\n".join(
        f"- {memory['memory_key']}: "
        f"{memory['memory_value']}"
        for memory in memories
    )

    system_prompt = f"""
You are Nova, Noah's personal AI assistant.

You are analyzing an image for Noah.

Be accurate and honest.

Do not invent details that are not visible.

If something cannot be determined from the image,
say so.

Use a calm, intelligent and sophisticated tone.

You may address the user as Noah.

Never claim to have performed an action unless
a real tool actually performed it.

Long-term memory:

{memory_text}
"""

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
 
         
        
            
    
    ."

        )
        )
