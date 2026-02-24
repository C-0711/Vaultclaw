"""
0711 Vault - Account Management
Email verification, password reset, DSGVO compliance
"""

from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import secrets
import hashlib
import json
import logging
import os
import httpx
import zipfile
import io

logger = logging.getLogger("vault.account")

router = APIRouter(prefix="/account", tags=["Account"])

# Email service config
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "0711 Vault <noreply@0711.io>")
APP_URL = os.getenv("APP_URL", "https://app.vault.0711.io")


# ===========================================
# SCHEMAS
# ===========================================

class EmailVerifyRequest(BaseModel):
    token: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password_hash: str
    new_salt: str
    new_encrypted_master_key: str

class AccountDeleteRequest(BaseModel):
    confirm_email: str
    reason: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    old_auth_hash: str
    new_auth_hash: str
    new_salt: str
    new_encrypted_master_key: str

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None


# ===========================================
# DEPENDENCIES
# ===========================================

async def get_db_pool():
    from main import db_pool
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db_pool

async def get_redis():
    from main import redis_client
    return redis_client

async def get_current_user(request: Request):
    from main import get_current_user as auth_user
    return await auth_user(request.headers.get("authorization"))


# ===========================================
# EMAIL HELPERS
# ===========================================

async def send_email(to: str, subject: str, html: str):
    """Send email via Resend."""
    if not RESEND_API_KEY:
        logger.warning(f"Email not sent (no API key): {subject} to {to}")
        return False
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": html
            }
        )
        
        if response.status_code == 200:
            logger.info(f"Email sent: {subject} to {to}")
            return True
        else:
            logger.error(f"Email failed: {response.text}")
            return False


def verification_email_html(token: str) -> str:
    verify_url = f"{APP_URL}/verify-email?token={token}"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #000; color: #fff; padding: 40px; }}
            .container {{ max-width: 500px; margin: 0 auto; background: #111; border-radius: 16px; padding: 40px; }}
            .logo {{ font-size: 24px; font-weight: bold; margin-bottom: 24px; }}
            .logo span {{ background: linear-gradient(135deg, #10b981, #059669); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            h1 {{ font-size: 24px; margin-bottom: 16px; }}
            p {{ color: #a3a3a3; line-height: 1.6; }}
            .btn {{ display: inline-block; background: #10b981; color: #fff; padding: 14px 28px; border-radius: 12px; text-decoration: none; font-weight: 600; margin: 24px 0; }}
            .footer {{ margin-top: 32px; padding-top: 24px; border-top: 1px solid #333; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo"><span>0711</span> Vault</div>
            <h1>E-Mail bestätigen</h1>
            <p>Klicke auf den Button unten, um deine E-Mail-Adresse zu bestätigen und deinen Vault zu aktivieren.</p>
            <a href="{verify_url}" class="btn">E-Mail bestätigen →</a>
            <p>Oder kopiere diesen Link:<br><code style="color: #10b981;">{verify_url}</code></p>
            <div class="footer">
                <p>Dieser Link ist 24 Stunden gültig.</p>
                <p>© 2026 0711.io GmbH • Stuttgart, Germany</p>
            </div>
        </div>
    </body>
    </html>
    """


def password_reset_email_html(token: str) -> str:
    reset_url = f"{APP_URL}/reset-password?token={token}"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #000; color: #fff; padding: 40px; }}
            .container {{ max-width: 500px; margin: 0 auto; background: #111; border-radius: 16px; padding: 40px; }}
            .logo {{ font-size: 24px; font-weight: bold; margin-bottom: 24px; }}
            .logo span {{ background: linear-gradient(135deg, #10b981, #059669); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
            h1 {{ font-size: 24px; margin-bottom: 16px; }}
            p {{ color: #a3a3a3; line-height: 1.6; }}
            .btn {{ display: inline-block; background: #10b981; color: #fff; padding: 14px 28px; border-radius: 12px; text-decoration: none; font-weight: 600; margin: 24px 0; }}
            .warning {{ background: #7f1d1d; border: 1px solid #991b1b; border-radius: 8px; padding: 12px; margin: 16px 0; }}
            .footer {{ margin-top: 32px; padding-top: 24px; border-top: 1px solid #333; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo"><span>0711</span> Vault</div>
            <h1>Passwort zurücksetzen</h1>
            <p>Jemand hat eine Passwortzurücksetzung für deinen 0711 Vault Account angefordert.</p>
            <a href="{reset_url}" class="btn">Neues Passwort setzen →</a>
            <div class="warning">
                <strong style="color: #fca5a5;">⚠️ Wichtig:</strong>
                <p style="color: #fca5a5; margin: 8px 0 0 0; font-size: 14px;">
                    Bei einem Passwort-Reset wird dein Master-Key neu generiert. 
                    Du benötigst deinen Recovery Key, um deine Daten wiederherzustellen!
                </p>
            </div>
            <p>Falls du dies nicht angefordert hast, ignoriere diese E-Mail.</p>
            <div class="footer">
                <p>Dieser Link ist 1 Stunde gültig.</p>
                <p>© 2026 0711.io GmbH • Stuttgart, Germany</p>
            </div>
        </div>
    </body>
    </html>
    """


# ===========================================
# EMAIL VERIFICATION
# ===========================================

@router.post("/send-verification")
async def send_verification_email(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis)
):
    """Send email verification link."""
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT email, email_verified_at FROM users WHERE id = $1",
            user_id
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user['email_verified_at']:
            return {"message": "Email already verified"}
        
        # Generate token
        token = secrets.token_urlsafe(32)
        
        # Store in Redis (24h expiry)
        if redis:
            await redis.setex(
                f"email_verify:{token}",
                86400,
                json.dumps({"user_id": user_id, "email": user['email']})
            )
        
        # Send email
        background_tasks.add_task(
            send_email,
            user['email'],
            "Bestätige deine E-Mail – 0711 Vault",
            verification_email_html(token)
        )
        
        return {"message": "Verification email sent"}


@router.post("/verify-email")
async def verify_email(
    request: EmailVerifyRequest,
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis)
):
    """Verify email with token."""
    if not redis:
        raise HTTPException(status_code=503, detail="Service unavailable")
    
    # Get token data
    data = await redis.get(f"email_verify:{request.token}")
    if not data:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    
    token_data = json.loads(data)
    user_id = token_data['user_id']
    
    # Update user
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET email_verified_at = NOW()
            WHERE id = $1 AND email_verified_at IS NULL
        """, user_id)
    
    # Delete token
    await redis.delete(f"email_verify:{request.token}")
    
    return {"message": "Email verified successfully"}


# ===========================================
# PASSWORD RESET
# ===========================================

@router.post("/request-password-reset")
async def request_password_reset(
    request: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis)
):
    """Request password reset link."""
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, email FROM users WHERE email = $1",
            request.email
        )
    
    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If an account exists, a reset link will be sent"}
    
    # Generate token
    token = secrets.token_urlsafe(32)
    
    # Store in Redis (1h expiry)
    if redis:
        await redis.setex(
            f"password_reset:{token}",
            3600,
            json.dumps({"user_id": str(user['id']), "email": user['email']})
        )
    
    # Send email
    background_tasks.add_task(
        send_email,
        user['email'],
        "Passwort zurücksetzen – 0711 Vault",
        password_reset_email_html(token)
    )
    
    return {"message": "If an account exists, a reset link will be sent"}


@router.post("/reset-password")
async def reset_password(
    request: PasswordResetConfirm,
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis)
):
    """Reset password with token."""
    if not redis:
        raise HTTPException(status_code=503, detail="Service unavailable")
    
    # Get token data
    data = await redis.get(f"password_reset:{request.token}")
    if not data:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    
    token_data = json.loads(data)
    user_id = token_data['user_id']
    
    # Update user
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users 
            SET auth_hash = $1, salt = $2, encrypted_master_key = $3, updated_at = NOW()
            WHERE id = $4
        """, request.new_password_hash, request.new_salt, 
            request.new_encrypted_master_key, uuid.UUID(user_id))
        
        # Log password change
        await conn.execute("""
            INSERT INTO audit_log (user_id, action, resource_type)
            VALUES ($1, 'password.reset', 'user')
        """, uuid.UUID(user_id))
    
    # Delete token
    await redis.delete(f"password_reset:{request.token}")
    
    # Invalidate all sessions (optional but recommended)
    # This would require storing session tokens in Redis with user prefix
    
    return {"message": "Password reset successfully"}


# ===========================================
# PASSWORD CHANGE (LOGGED IN)
# ===========================================

@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Change password while logged in."""
    async with db_pool.acquire() as conn:
        # Verify old password
        user = await conn.fetchrow(
            "SELECT auth_hash FROM users WHERE id = $1",
            current_user
        )
        
        if not user or user['auth_hash'] != request.old_auth_hash:
            raise HTTPException(status_code=401, detail="Invalid current password")
        
        # Update password
        await conn.execute("""
            UPDATE users 
            SET auth_hash = $1, salt = $2, encrypted_master_key = $3, updated_at = NOW()
            WHERE id = $4
        """, request.new_auth_hash, request.new_salt, 
            request.new_encrypted_master_key, current_user)
        
        await conn.execute("""
            INSERT INTO audit_log (user_id, action, resource_type)
            VALUES ($1, 'password.change', 'user')
        """, current_user)
    
    return {"message": "Password changed successfully"}


# ===========================================
# DSGVO: DATA EXPORT
# ===========================================

@router.post("/export-data")
async def request_data_export(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """
    Request full data export (DSGVO Art. 20 - Data Portability).
    Creates a downloadable archive with all user data.
    """
    # Queue export job
    background_tasks.add_task(generate_data_export, user_id, db_pool)
    
    return {
        "message": "Data export started. You will receive an email when ready.",
        "estimated_time": "5-30 minutes depending on data size"
    }


async def generate_data_export(user_id: str, db_pool):
    """Background task to generate data export."""
    logger.info(f"Starting data export for user {user_id}")
    
    try:
        async with db_pool.acquire() as conn:
            # Get user info
            user = await conn.fetchrow("""
                SELECT email, created_at, last_login, email_verified_at
                FROM users WHERE id = $1
            """, user_id)
            
            # Get all vault items
            items = await conn.fetch("""
                SELECT id, item_type, encrypted_metadata, file_size, mime_type,
                       storage_key, captured_at, created_at, processing_status
                FROM vault_items
                WHERE user_id = $1 AND deleted_at IS NULL
            """, user_id)
            
            # Get albums
            albums = await conn.fetch("""
                SELECT id, encrypted_name, encrypted_description, created_at
                FROM albums
                WHERE user_id = $1 AND deleted_at IS NULL
            """, user_id)
            
            # Get share links
            shares = await conn.fetch("""
                SELECT id, share_token, created_at, expires_at, download_count
                FROM share_links
                WHERE user_id = $1
            """, user_id)
            
            # Get audit log
            audit = await conn.fetch("""
                SELECT action, resource_type, created_at
                FROM audit_log
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT 1000
            """, user_id)
        
        # Create export JSON
        export_data = {
            "exported_at": datetime.utcnow().isoformat(),
            "user": {
                "id": user_id,
                "email": user['email'],
                "created_at": user['created_at'].isoformat() if user['created_at'] else None,
                "last_login": user['last_login'].isoformat() if user['last_login'] else None,
                "email_verified": user['email_verified_at'] is not None
            },
            "items": [
                {
                    "id": str(item['id']),
                    "type": item['item_type'],
                    "metadata": item['encrypted_metadata'],
                    "file_size": item['file_size'],
                    "mime_type": item['mime_type'],
                    "captured_at": item['captured_at'].isoformat() if item['captured_at'] else None,
                    "created_at": item['created_at'].isoformat() if item['created_at'] else None
                }
                for item in items
            ],
            "albums": [
                {
                    "id": str(album['id']),
                    "name": album['encrypted_name'],
                    "description": album['encrypted_description'],
                    "created_at": album['created_at'].isoformat() if album['created_at'] else None
                }
                for album in albums
            ],
            "share_links": [
                {
                    "id": str(share['id']),
                    "created_at": share['created_at'].isoformat() if share['created_at'] else None,
                    "expires_at": share['expires_at'].isoformat() if share['expires_at'] else None,
                    "downloads": share['download_count']
                }
                for share in shares
            ],
            "activity_log": [
                {
                    "action": log['action'],
                    "resource": log['resource_type'],
                    "timestamp": log['created_at'].isoformat() if log['created_at'] else None
                }
                for log in audit
            ]
        }
        
        # TODO: Create ZIP file with JSON + actual files
        # TODO: Upload to storage and create download link
        # TODO: Send email with download link
        
        logger.info(f"Data export completed for user {user_id}: {len(items)} items")
        
    except Exception as e:
        logger.error(f"Data export failed for user {user_id}: {e}")


@router.get("/export-status")
async def get_export_status(
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Check status of data export."""
    # TODO: Check export job status
    return {"status": "not_implemented"}


# ===========================================
# DSGVO: ACCOUNT DELETION
# ===========================================

@router.post("/request-deletion")
async def request_account_deletion(
    request: AccountDeleteRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """
    Request account deletion (DSGVO Art. 17 - Right to Erasure).
    14-day cooling period before actual deletion.
    """
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT email FROM users WHERE id = $1",
            user_id
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Verify email confirmation
        if user['email'].lower() != request.confirm_email.lower():
            raise HTTPException(status_code=400, detail="Email confirmation does not match")
        
        # Set deletion scheduled
        deletion_date = datetime.utcnow() + timedelta(days=14)
        
        await conn.execute("""
            UPDATE users 
            SET deletion_requested_at = NOW(),
                deletion_scheduled_at = $1,
                deletion_reason = $2
            WHERE id = $3
        """, deletion_date, request.reason, user_id)
        
        await conn.execute("""
            INSERT INTO audit_log (user_id, action, resource_type)
            VALUES ($1, 'account.deletion_requested', 'user')
        """, user_id)
    
    # Send confirmation email
    background_tasks.add_task(
        send_email,
        user['email'],
        "Kontolöschung angefordert – 0711 Vault",
        f"""
        <html>
        <body style="font-family: sans-serif; background: #000; color: #fff; padding: 40px;">
            <h1>Kontolöschung angefordert</h1>
            <p>Du hast die Löschung deines 0711 Vault Accounts angefordert.</p>
            <p><strong>Löschung geplant für:</strong> {deletion_date.strftime('%d.%m.%Y')}</p>
            <p>Du hast 14 Tage Zeit, die Löschung abzubrechen, indem du dich einloggst.</p>
            <p>Nach der Löschung werden alle deine Daten unwiderruflich entfernt.</p>
        </body>
        </html>
        """
    )
    
    return {
        "message": "Account deletion scheduled",
        "deletion_date": deletion_date.isoformat(),
        "cancellation_possible_until": deletion_date.isoformat()
    }


@router.post("/cancel-deletion")
async def cancel_account_deletion(
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Cancel pending account deletion."""
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE users 
            SET deletion_requested_at = NULL,
                deletion_scheduled_at = NULL,
                deletion_reason = NULL
            WHERE id = $1 AND deletion_scheduled_at > NOW()
        """, user_id)
        
        if result == "UPDATE 0":
            raise HTTPException(status_code=400, detail="No pending deletion or already processed")
        
        await conn.execute("""
            INSERT INTO audit_log (user_id, action, resource_type)
            VALUES ($1, 'account.deletion_cancelled', 'user')
        """, user_id)
    
    return {"message": "Account deletion cancelled"}


# ===========================================
# PROFILE
# ===========================================

@router.get("/profile")
async def get_profile(
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Get user profile."""
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("""
            SELECT email, created_at, last_login, email_verified_at,
                   display_name, language, timezone,
                   deletion_requested_at, deletion_scheduled_at
            FROM users WHERE id = $1
        """, user_id)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get storage usage
        usage = await conn.fetchrow("""
            SELECT COALESCE(SUM(file_size), 0) as used_bytes,
                   COUNT(*) as file_count
            FROM vault_items
            WHERE user_id = $1 AND deleted_at IS NULL
        """, user_id)
        
        # Get quota (from subscription or default)
        quota = await conn.fetchrow("""
            SELECT quota_bytes, plan_name FROM storage_quotas
            WHERE user_id = $1
        """, user_id)
        
        return {
            "email": user['email'],
            "display_name": user['display_name'],
            "language": user['language'] or 'de',
            "timezone": user['timezone'] or 'Europe/Berlin',
            "email_verified": user['email_verified_at'] is not None,
            "created_at": user['created_at'].isoformat() if user['created_at'] else None,
            "last_login": user['last_login'].isoformat() if user['last_login'] else None,
            "deletion_pending": user['deletion_scheduled_at'] is not None,
            "deletion_date": user['deletion_scheduled_at'].isoformat() if user['deletion_scheduled_at'] else None,
            "storage": {
                "used_bytes": usage['used_bytes'],
                "quota_bytes": quota['quota_bytes'] if quota else 5368709120,  # 5GB default
                "file_count": usage['file_count'],
                "plan": quota['plan_name'] if quota else 'free'
            }
        }


@router.put("/profile")
async def update_profile(
    profile: UpdateProfileRequest,
    user_id: str = Depends(get_current_user),
    db_pool = Depends(get_db_pool)
):
    """Update user profile."""
    async with db_pool.acquire() as conn:
        updates = []
        params = [user_id]
        idx = 2
        
        if profile.display_name is not None:
            updates.append(f"display_name = ${idx}")
            params.append(profile.display_name)
            idx += 1
        
        if profile.language is not None:
            updates.append(f"language = ${idx}")
            params.append(profile.language)
            idx += 1
        
        if profile.timezone is not None:
            updates.append(f"timezone = ${idx}")
            params.append(profile.timezone)
            idx += 1
        
        if updates:
            await conn.execute(f"""
                UPDATE users SET {', '.join(updates)}, updated_at = NOW()
                WHERE id = $1
            """, *params)
    
    return {"message": "Profile updated"}
