"""
Authentication dependency for 0711 Vault API
Redis-first token lookup (matches main.py token generation),
with JWT decode as fallback.
"""

from fastapi import Header, HTTPException
import os

# JWT fallback imports (may not be installed)
try:
    from jose import jwt, JWTError
    HAS_JOSE = True
except ImportError:
    HAS_JOSE = False

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"


async def get_current_user(authorization: str = Header(None)) -> str:
    """
    Validate Bearer token and return user_id.
    Strategy: Redis lookup first (main.py stores token:<random> -> user_id),
    then JWT decode as fallback.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.split(" ", 1)[1]

    # --- Strategy 1: Redis token lookup (primary) ---
    try:
        from database import get_redis
        redis = get_redis()
        if redis:
            user_id = await redis.get(f"token:{token}")
            if user_id:
                # Redis returns bytes or str depending on decode_responses
                if isinstance(user_id, bytes):
                    user_id = user_id.decode("utf-8")
                return user_id
    except Exception as e:
        print(f"[auth] Redis lookup failed: {e}")

    # --- Strategy 2: JWT decode (fallback) ---
    if HAS_JOSE:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = payload.get("user_id")
            if user_id:
                return user_id
        except JWTError:
            pass

    raise HTTPException(status_code=401, detail="Invalid or expired token")
