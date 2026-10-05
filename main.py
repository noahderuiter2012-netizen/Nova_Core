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


# =========================================================
# NOVA CORE
# =========================================================

app = FastAPI(title="Nova Core")


# =========================================================
# HUGGING FACE
# =========================================================

HF_TOKEN = os.environ.get("HF_TOKEN")

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_TOKEN
)


# =========================================================
# SUPABASE
# =========================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY"
)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY
)


# =========================================================
# SECURITY KEYS
# =========================================================

DEVELOPER_KEY = os.environ.get(
    "NOVA_DEVELOPER_KEY"
)

ACCESS_KEY = os.environ.get(
    "NOVA_ACCESS_KEY"
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
            detail="Developer authentication is not configured
