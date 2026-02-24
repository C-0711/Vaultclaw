"""
SendGrid Email Service for 0711-Vault
Handles password reset emails and verification emails.
"""
import os
import logging
from typing import Optional
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

logger = logging.getLogger(__name__)

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "vault@0711.io")
FROM_NAME = os.getenv("FROM_NAME", "0711-Vault")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://app.vault.0711.io")


class EmailService:
    """Email service using SendGrid."""
    
    def __init__(self):
        self.api_key = SENDGRID_API_KEY
        self.from_email = FROM_EMAIL
        self.from_name = FROM_NAME
        self._client = None
    
    @property
    def client(self) -> Optional[SendGridAPIClient]:
        """Lazy-load SendGrid client."""
        if not self.api_key:
            logger.warning("SENDGRID_API_KEY not configured - emails disabled")
            return None
        if not self._client:
            self._client = SendGridAPIClient(self.api_key)
        return self._client
    
    def send_password_reset(self, to_email: str, reset_token: str, reset_url: str = None) -> bool:
        """
        Send password reset email.
        
        Args:
            to_email: Recipient email address
            reset_token: Password reset token
            reset_url: Base URL for reset (token will be appended)
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.client:
            logger.info(f"Email disabled - would send reset to {to_email}")
            return False
        
        full_reset_url = f"{reset_url}?token={reset_token}&email={to_email}" if reset_url else f"{FRONTEND_URL}/reset-password?token={reset_token}&email={to_email}"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f9fafb; padding: 30px; border-radius: 0 0 8px 8px; }}
                .button {{ display: inline-block; background: #667eea; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0; }}
                .warning {{ background: #fef3c7; border: 1px solid #f59e0b; padding: 12px; border-radius: 6px; margin: 20px 0; }}
                .footer {{ text-align: center; color: #6b7280; font-size: 12px; margin-top: 30px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔐 Password Reset</h1>
                </div>
                <div class="content">
                    <p>Hello,</p>
                    <p>We received a request to reset your password for your 0711-Vault account.</p>
                    
                    <div style="text-align: center;">
                        <a href="{full_reset_url}" class="button">Reset Password</a>
                    </div>
                    
                    <div class="warning">
                        <strong>⚠️ Important:</strong> Due to our zero-knowledge encryption, resetting your password 
                        will result in <strong>loss of access to previously encrypted data</strong>. 
                        Only proceed if you understand this.
                    </div>
                    
                    <p>This link expires in 1 hour.</p>
                    <p>If you didn't request this, you can safely ignore this email.</p>
                    
                    <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">
                        Or copy this URL: {full_reset_url}
                    </p>
                </div>
                <div class="footer">
                    <p>0711-Vault — Your privacy-first personal vault</p>
                    <p>© 2026 0711.io — Digital Sovereignty</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message = Mail(
            from_email=Email(self.from_email, self.from_name),
            to_emails=To(to_email),
            subject="Reset your 0711-Vault password",
            html_content=Content("text/html", html_content)
        )
        
        try:
            response = self.client.send(message)
            logger.info(f"Password reset email sent to {to_email}, status: {response.status_code}")
            return response.status_code in (200, 201, 202)
        except Exception as e:
            logger.error(f"Failed to send password reset email: {e}")
            return False
    
    def send_verification_email(self, to_email: str, verification_token: str) -> bool:
        """Send email verification email."""
        if not self.client:
            logger.info(f"Email disabled - would send verification to {to_email}")
            return False
        
        verify_url = f"{FRONTEND_URL}/verify-email?token={verification_token}&email={to_email}"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f9fafb; padding: 30px; border-radius: 0 0 8px 8px; }}
                .button {{ display: inline-block; background: #10b981; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0; }}
                .footer {{ text-align: center; color: #6b7280; font-size: 12px; margin-top: 30px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>✉️ Verify Your Email</h1>
                </div>
                <div class="content">
                    <p>Welcome to 0711-Vault!</p>
                    <p>Please verify your email address to complete your registration.</p>
                    
                    <div style="text-align: center;">
                        <a href="{verify_url}" class="button">Verify Email</a>
                    </div>
                    
                    <p>This link expires in 24 hours.</p>
                    <p>If you didn't create an account, you can safely ignore this email.</p>
                    
                    <p style="color: #6b7280; font-size: 12px; margin-top: 30px;">
                        Or copy this URL: {verify_url}
                    </p>
                </div>
                <div class="footer">
                    <p>0711-Vault — Your privacy-first personal vault</p>
                    <p>© 2026 0711.io — Digital Sovereignty</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message = Mail(
            from_email=Email(self.from_email, self.from_name),
            to_emails=To(to_email),
            subject="Verify your 0711-Vault email",
            html_content=Content("text/html", html_content)
        )
        
        try:
            response = self.client.send(message)
            logger.info(f"Verification email sent to {to_email}, status: {response.status_code}")
            return response.status_code in (200, 201, 202)
        except Exception as e:
            logger.error(f"Failed to send verification email: {e}")
            return False


# Global instance for import
email_service = EmailService()

# ===========================================
# INVITATION EMAILS
# ===========================================

async def send_invite_email(email: str, invite_token: str, tenant_id: str):
    """Send invitation email to new user."""
    import os
    import httpx
    
    sendgrid_key = os.getenv('SENDGRID_API_KEY')
    if not sendgrid_key:
        print(f"[EMAIL] SendGrid not configured - would invite {email} for tenant {tenant_id}")
        print(f"[EMAIL] Invite link: https://{tenant_id}.0711.io/accept-invite?token={invite_token}")
        return
    
    invite_url = f"https://{tenant_id}.0711.io/accept-invite?token={invite_token}"
    
    tenant_names = {
        'bosch': 'Bosch',
        'lightnet': 'Lightnet', 
        'isolde': 'Isolde',
        'bette': 'Bette'
    }
    tenant_name = tenant_names.get(tenant_id, tenant_id.title())
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #0a0a0a; color: white; padding: 40px;">
        <div style="max-width: 500px; margin: 0 auto; background: #1a1a1a; border-radius: 12px; padding: 32px;">
            <h1 style="margin: 0 0 24px 0;">Willkommen bei {tenant_name}</h1>
            <p style="color: #a0a0a0;">
                Sie wurden eingeladen, dem {tenant_name} Workspace auf 0711 Studio beizutreten.
            </p>
            <a href="{invite_url}" 
               style="display: inline-block; background: #3b82f6; color: white; 
                      padding: 12px 24px; border-radius: 8px; text-decoration: none; 
                      margin: 24px 0;">
                Einladung annehmen
            </a>
            <p style="color: #666; font-size: 12px;">
                Dieser Link ist 7 Tage gültig. Falls Sie diese Einladung nicht erwartet haben,
                können Sie diese E-Mail ignorieren.
            </p>
        </div>
    </body>
    </html>
    """
    
    async with httpx.AsyncClient() as client:
        await client.post(
            'https://api.sendgrid.com/v3/mail/send',
            headers={
                'Authorization': f'Bearer {sendgrid_key}',
                'Content-Type': 'application/json'
            },
            json={
                'personalizations': [{'to': [{'email': email}]}],
                'from': {'email': 'noreply@0711.io', 'name': '0711 Studio'},
                'subject': f'Einladung zu {tenant_name} auf 0711 Studio',
                'content': [{'type': 'text/html', 'value': html_content}]
            }
        )
    
    print(f"[EMAIL] Invitation sent to {email} for tenant {tenant_id}")
