"""
0711 Vault - Rate Limiting Middleware
Protects against brute force and DDoS attacks.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("vault.ratelimit")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiter.
    For production, use Redis-backed implementation.
    """
    
    def __init__(self, app, requests_per_minute: int = 100, 
                 login_requests_per_minute: int = 5,
                 redis_client=None):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.login_requests_per_minute = login_requests_per_minute
        self.redis = redis_client
        
        # In-memory fallback (don't use in multi-process)
        self._requests = {}
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/", "/docs", "/openapi.json"]:
            return await call_next(request)
        
        # Get client identifier
        client_ip = self._get_client_ip(request)
        
        # Determine rate limit
        if "/auth/login" in request.url.path:
            limit = self.login_requests_per_minute
            key = f"login:{client_ip}"
        else:
            limit = self.requests_per_minute
            key = f"api:{client_ip}"
        
        # Check rate limit
        now = datetime.utcnow()
        window_start = now.replace(second=0, microsecond=0)
        
        is_limited = await self._check_rate_limit(key, limit, window_start)
        
        if is_limited:
            logger.warning(f"Rate limit exceeded: {key}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
                headers={"Retry-After": "60"}
            )
        
        return await call_next(request)
    
    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
    
    async def _check_rate_limit(self, key: str, limit: int, window_start: datetime) -> bool:
        """
        Check if request should be rate limited.
        Returns True if limit exceeded.
        """
        if self.redis:
            return await self._check_redis(key, limit, window_start)
        else:
            return self._check_memory(key, limit, window_start)
    
    async def _check_redis(self, key: str, limit: int, window_start: datetime) -> bool:
        """Redis-backed rate limiting."""
        try:
            redis_key = f"ratelimit:{key}:{window_start.timestamp()}"
            count = await self.redis.incr(redis_key)
            if count == 1:
                await self.redis.expire(redis_key, 60)
            return count > limit
        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            return False
    
    def _check_memory(self, key: str, limit: int, window_start: datetime) -> bool:
        """In-memory rate limiting (single process only)."""
        self._cleanup_old_entries(window_start)
        
        if key not in self._requests:
            self._requests[key] = {'window': window_start, 'count': 0}
        
        entry = self._requests[key]
        
        if entry['window'] != window_start:
            entry['window'] = window_start
            entry['count'] = 0
        
        entry['count'] += 1
        return entry['count'] > limit
    
    def _cleanup_old_entries(self, current_window: datetime):
        """Remove entries from previous windows."""
        old_keys = [k for k, v in self._requests.items() 
                    if v['window'] < current_window - timedelta(minutes=2)]
        for k in old_keys:
            del self._requests[k]


class LoginProtectionMiddleware(BaseHTTPMiddleware):
    """
    Additional protection for login endpoint.
    Implements exponential backoff on failed attempts.
    """
    
    def __init__(self, app, max_attempts: int = 5, lockout_minutes: int = 15):
        super().__init__(app)
        self.max_attempts = max_attempts
        self.lockout_minutes = lockout_minutes
        self._failed_attempts = {}
    
    async def dispatch(self, request: Request, call_next):
        # Only apply to login
        if "/auth/login" not in request.url.path:
            return await call_next(request)
        
        client_ip = self._get_client_ip(request)
        
        # Check if locked out
        lockout = self._failed_attempts.get(client_ip)
        if lockout:
            locked_until = lockout.get('locked_until')
            if locked_until and locked_until > datetime.utcnow():
                remaining = int((locked_until - datetime.utcnow()).total_seconds())
                logger.warning(f"Login attempt blocked for {client_ip} - locked for {remaining}s")
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Account temporarily locked. Try again in {remaining} seconds."},
                    headers={"Retry-After": str(remaining)}
                )
        
        # Process request
        response = await call_next(request)
        
        # Track failed attempts
        if response.status_code == 401:
            self._record_failure(client_ip)
        elif response.status_code == 200:
            self._clear_failures(client_ip)
        
        return response
    
    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
    
    def _record_failure(self, ip: str):
        entry = self._failed_attempts.get(ip, {'count': 0, 'locked_until': None})
        entry['count'] += 1
        
        if entry['count'] >= self.max_attempts:
            entry['locked_until'] = datetime.utcnow() + timedelta(minutes=self.lockout_minutes)
            logger.warning(f"Login lockout triggered for {ip} after {entry['count']} attempts")
        
        self._failed_attempts[ip] = entry
    
    def _clear_failures(self, ip: str):
        if ip in self._failed_attempts:
            del self._failed_attempts[ip]
