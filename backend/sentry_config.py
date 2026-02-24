"""
Sentry Error Tracking Configuration for 0711-Vault
Add this to main.py once you have a Sentry DSN.

Usage:
1. Create account at sentry.io
2. Create a Python project
3. Copy DSN to SENTRY_DSN environment variable
4. Import and call init_sentry() in main.py startup
"""
import os
import logging

logger = logging.getLogger(__name__)

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")


def init_sentry():
    """Initialize Sentry error tracking if configured."""
    if not SENTRY_DSN:
        logger.info("Sentry not configured (SENTRY_DSN not set)")
        return False
    
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=ENVIRONMENT,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
            ],
            # Performance monitoring
            traces_sample_rate=0.1,  # 10% of transactions
            profiles_sample_rate=0.1,  # 10% of sampled transactions
            # Data privacy
            send_default_pii=False,
            # Release tracking
            release=os.getenv("VERSION", "1.0.0"),
        )
        
        logger.info(f"✅ Sentry initialized (env: {ENVIRONMENT})")
        return True
        
    except ImportError:
        logger.warning("sentry-sdk not installed. Run: pip install sentry-sdk[fastapi]")
        return False
    except Exception as e:
        logger.error(f"Sentry initialization failed: {e}")
        return False


# Test function for Sentry
def test_sentry():
    """Trigger a test error to verify Sentry is working."""
    import sentry_sdk
    sentry_sdk.capture_message("Test message from 0711-Vault")
    raise Exception("Test exception from 0711-Vault - ignore this!")
