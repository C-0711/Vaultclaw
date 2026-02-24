"""
0711 Vault - Security Headers Middleware
Adds security headers to all responses.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to protect against common web vulnerabilities.
    """
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'DENY'
        
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        # XSS Protection (legacy browsers)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # Referrer Policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Permissions Policy (disable unnecessary browser features)
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        
        # Content Security Policy (allow only necessary sources)
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob: https://storage.0711.io; "
            "connect-src 'self' https://api.vault.0711.io https://api-vault.0711.io; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        
        return response
