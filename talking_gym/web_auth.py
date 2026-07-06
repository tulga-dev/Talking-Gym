"""Account auth for the PWA: email+password and Google Sign-In.

- Passwords: scrypt (stdlib), salt$hash hex.
- Sessions: opaque bearer tokens in auth_tokens table.
- Google: ID token verified against Google's tokeninfo endpoint (aud must
  match GOOGLE_CLIENT_ID). Enabled only when GOOGLE_CLIENT_ID is set.
- Apple Sign-In requires an Apple Developer account (Service ID + key);
  wire it here once those credentials exist.
"""
import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

import httpx
from aiohttp import web

from . import db
from .config import config
from .providers import email as email_provider

log = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _err(status: int, code: str) -> web.Response:
    return web.json_response({"error": code}, status=status)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, h_hex = stored.split("$", 1)
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return secrets.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def new_user_id() -> int:
    return secrets.randbits(62) | (1 << 62)


def issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.create_token(token, user_id)
    return token


def user_from_request(request: web.Request):
    """Bearer token (new) or legacy numeric X-User (early beta accounts)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return db.user_by_token(auth[7:].strip())
    raw = request.headers.get("X-User", "")
    if raw.isdigit():
        return db.get_user(int(raw))
    return None


async def api_auth_register(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    name = str(body.get("name", "")).strip()[:60]
    level = body.get("level", "beginner")
    if level not in ("beginner", "intermediate", "advanced"):
        level = "beginner"
    if not _EMAIL_RE.match(email):
        return _err(400, "bad_email")
    if len(password) < 6:
        return _err(400, "weak_password")
    if not name:
        name = email.split("@")[0][:30]
    if db.user_by_email(email):
        return _err(409, "email_taken")
    target_lang = body.get("target_lang", "en")
    if target_lang not in ("en", "ko", "zh", "ja"):
        target_lang = "en"
    native_lang = body.get("native_lang", "mn")
    if native_lang not in ("mn", "mnt"):
        native_lang = "mn"
    uid = new_user_id()
    db.get_or_create_user(uid, uid, name, channel="pwa")
    db.set_level(uid, level)
    db.set_target_lang(uid, target_lang)
    db.set_native_lang(uid, native_lang)
    db.set_auth(uid, email=email, password_hash=hash_password(password))
    return web.json_response({"token": issue_token(uid)})


async def api_auth_login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    user = db.user_by_email(email)
    if user is None or not user["password_hash"] or not verify_password(password, user["password_hash"]):
        return _err(401, "bad_credentials")
    return web.json_response({"token": issue_token(user["user_id"])})


async def api_auth_google(request: web.Request) -> web.Response:
    if not GOOGLE_CLIENT_ID:
        return _err(501, "google_not_configured")
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    id_token = str(body.get("id_token", ""))
    if not id_token:
        return _err(400, "id_token_required")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get("https://oauth2.googleapis.com/tokeninfo",
                                params={"id_token": id_token})
    if resp.status_code != 200:
        return _err(401, "google_invalid")
    info = resp.json()
    if info.get("aud") != GOOGLE_CLIENT_ID:
        return _err(401, "google_aud_mismatch")
    sub = info.get("sub", "")
    email = str(info.get("email", "")).lower()
    name = str(info.get("name", "") or (email.split("@")[0] if email else "Learner"))[:60]
    user = db.user_by_google_sub(sub) or (db.user_by_email(email) if email else None)
    if user is None:
        uid = new_user_id()
        db.get_or_create_user(uid, uid, name, channel="pwa")
        db.set_auth(uid, email=email or None, google_sub=sub)
    else:
        uid = user["user_id"]
        if not user["google_sub"]:
            db.set_auth(uid, google_sub=sub)
    return web.json_response({"token": issue_token(uid)})


async def api_auth_logout(request: web.Request) -> web.Response:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        db.delete_token(auth[7:].strip())
    return web.json_response({"ok": True})


async def api_auth_forgot(request: web.Request) -> web.Response:
    """Start password recovery. Always returns ok (never leaks which emails
    exist). Emails a reset link when the address has an account and the email
    provider is configured; otherwise returns a hint so the UI can guide the
    user to Google sign-in or support."""
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    email = str(body.get("email", "")).strip().lower()
    if not _EMAIL_RE.match(email):
        return _err(400, "bad_email")
    user = db.user_by_email(email)
    if not email_provider.enabled():
        return web.json_response({"ok": True, "email_configured": False,
                                  "has_google": bool(user and user["google_sub"])})
    if user is not None:
        token = secrets.token_urlsafe(32)
        expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        db.create_reset(token, user["user_id"], expires)
        origin = str(request.url.origin())
        await email_provider.send_reset(email, f"{origin}/app?reset={token}")
    return web.json_response({"ok": True, "email_configured": True})


async def api_auth_reset(request: web.Request) -> web.Response:
    """Complete recovery: set a new password from a valid reset token."""
    try:
        body = await request.json()
    except Exception:
        return _err(400, "bad_json")
    token = str(body.get("token", "")).strip()
    new_password = str(body.get("password", ""))
    if len(new_password) < 6:
        return _err(400, "weak_password")
    user = db.user_by_reset(token)
    if user is None:
        return _err(400, "bad_token")
    db.set_auth(user["user_id"], password_hash=hash_password(new_password))
    db.delete_reset(token)
    return web.json_response({"token": issue_token(user["user_id"])})


async def api_config(request: web.Request) -> web.Response:
    return web.json_response({"google_client_id": GOOGLE_CLIENT_ID,
                              "email_recovery": email_provider.enabled(),
                              "gemini": config.gemini_enabled})


def add_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/config", api_config)
    app.router.add_post("/api/auth/register", api_auth_register)
    app.router.add_post("/api/auth/login", api_auth_login)
    app.router.add_post("/api/auth/google", api_auth_google)
    app.router.add_post("/api/auth/forgot", api_auth_forgot)
    app.router.add_post("/api/auth/reset", api_auth_reset)
    app.router.add_post("/api/auth/logout", api_auth_logout)
