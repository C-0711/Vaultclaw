"""
Authentication routes for 0711 Vault
Standard auth with OAuth support (GitHub, Google)
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
import bcrypt
from jose import jwt
from datetime import datetime, timedelta
import secrets
import httpx
from typing import Optional
from urllib.parse import urlencode
from sqlalchemy import text

from config import settings
from database import get_db

router = APIRouter()


# ===========================================
# SCHEMAS
# ===========================================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str


# ===========================================
# PASSWORD HELPERS
# ===========================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


# ===========================================
# AUTH ENDPOINTS
# ===========================================

@router.post("/register", response_model=TokenResponse)
async def register(request: RegisterRequest, db=Depends(get_db)):
    """Register a new user with email and password."""
    
    result = await db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": request.email}
    )
    if result.fetchone():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    password_hash = hash_password(request.password)
    
    result = await db.execute(
        text("""
            INSERT INTO users (email, auth_hash, display_name)
            VALUES (:email, :password_hash, :display_name)
            RETURNING id
        """),
        {
            "email": request.email,
            "password_hash": password_hash,
            "display_name": request.display_name or request.email.split("@")[0]
        }
    )
    user = result.fetchone()
    await db.commit()
    
    return _create_tokens(str(user.id), request.email)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db=Depends(get_db)):
    """Login with email and password."""
    
    result = await db.execute(
        text("SELECT id, auth_hash FROM users WHERE email = :email AND deleted_at IS NULL"),
        {"email": request.email}
    )
    user = result.fetchone()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not user.auth_hash or not verify_password(request.password, user.auth_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    await db.execute(
        text("UPDATE users SET last_login_at = NOW() WHERE id = :id"),
        {"id": user.id}
    )
    await db.commit()
    
    return _create_tokens(str(user.id), request.email)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """Refresh access token."""
    try:
        payload = jwt.decode(refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return _create_tokens(payload.get("user_id"), payload.get("sub"))
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ===========================================
# OAUTH ENDPOINTS
# ===========================================

@router.get("/oauth/github")
async def github_login():
    """Redirect to GitHub OAuth."""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")
    
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": f"{settings.APP_URL}/auth/oauth/github/callback",
        "scope": "read:user user:email",
        "state": secrets.token_urlsafe(16)
    }
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{urlencode(params)}")


@router.get("/oauth/github/callback")
async def github_callback(code: str, state: str, db=Depends(get_db)):
    """Handle GitHub OAuth callback."""
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={"client_id": settings.GITHUB_CLIENT_ID, "client_secret": settings.GITHUB_CLIENT_SECRET, "code": code},
            headers={"Accept": "application/json"}
        )
        token_data = token_response.json()
        if "access_token" not in token_data:
            raise HTTPException(status_code=400, detail="Failed to get access token")
        
        user_response = await client.get("https://api.github.com/user", headers={"Authorization": f"Bearer {token_data['access_token']}"})
        github_user = user_response.json()
        
        emails_response = await client.get("https://api.github.com/user/emails", headers={"Authorization": f"Bearer {token_data['access_token']}"})
        emails = emails_response.json()
        primary_email = next((e["email"] for e in emails if e["primary"]), None)
        if not primary_email:
            raise HTTPException(status_code=400, detail="No email found")
    
    user_id = await _find_or_create_oauth_user(db, "github", str(github_user["id"]), primary_email, github_user.get("name") or github_user.get("login"))
    tokens = _create_tokens(user_id, primary_email)
    return RedirectResponse(f"{settings.APP_URL}/oauth-callback?token={tokens.access_token}&refresh={tokens.refresh_token}")


@router.get("/oauth/google")
async def google_login():
    """Redirect to Google OAuth."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.APP_URL}/auth/oauth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": secrets.token_urlsafe(16)
    }
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")


@router.get("/oauth/google/callback")
async def google_callback(code: str, state: str, db=Depends(get_db)):
    """Handle Google OAuth callback."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={"client_id": settings.GOOGLE_CLIENT_ID, "client_secret": settings.GOOGLE_CLIENT_SECRET, "code": code, "grant_type": "authorization_code", "redirect_uri": f"{settings.APP_URL}/auth/oauth/google/callback"}
        )
        token_data = token_response.json()
        if "access_token" not in token_data:
            raise HTTPException(status_code=400, detail="Failed to get access token")
        
        user_response = await client.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {token_data['access_token']}"})
        google_user = user_response.json()
    
    user_id = await _find_or_create_oauth_user(db, "google", google_user["id"], google_user["email"], google_user.get("name"))
    tokens = _create_tokens(user_id, google_user["email"])
    return RedirectResponse(f"{settings.APP_URL}/oauth-callback?token={tokens.access_token}&refresh={tokens.refresh_token}")


# ===========================================
# HELPERS
# ===========================================

async def _find_or_create_oauth_user(db, provider: str, provider_id: str, email: str, display_name: str) -> str:
    """Find existing user or create new one for OAuth."""
    
    result = await db.execute(
        text("SELECT user_id FROM oauth_accounts WHERE provider = :provider AND provider_user_id = :provider_id"),
        {"provider": provider, "provider_id": provider_id}
    )
    oauth_account = result.fetchone()
    if oauth_account:
        return str(oauth_account.user_id)
    
    result = await db.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email})
    user = result.fetchone()
    
    if user:
        user_id = user.id
    else:
        result = await db.execute(
            text("INSERT INTO users (email, display_name) VALUES (:email, :display_name) RETURNING id"),
            {"email": email, "display_name": display_name}
        )
        user_id = result.fetchone().id
    
    await db.execute(
        text("INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email) VALUES (:user_id, :provider, :provider_id, :email)"),
        {"user_id": user_id, "provider": provider, "provider_id": provider_id, "email": email}
    )
    await db.commit()
    return str(user_id)


def _create_tokens(user_id: str, email: str) -> TokenResponse:
    """Create access and refresh tokens."""
    now = datetime.utcnow()
    
    access_token = jwt.encode(
        {"sub": email, "user_id": user_id, "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), "type": "access"},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )
    
    refresh_token = jwt.encode(
        {"sub": email, "user_id": user_id, "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS), "type": "refresh"},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
    )
    
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, user_id=user_id)
