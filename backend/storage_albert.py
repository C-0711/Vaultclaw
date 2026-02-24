"""
0711 Vault Storage - Albert Backend

Replaces MinIO with PostgreSQL + ChaCha20 encryption.
Encrypted content stored directly in database.

This module provides the storage layer for vault-api.
"""

import os
import base64
import secrets
from typing import Optional, Tuple
from dataclasses import dataclass

# Cryptography
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend


# ===========================================
# CONFIGURATION
# ===========================================

# Server-side encryption key (must be set in production)
# In production: use secrets manager or HSM
VAULT_ENCRYPTION_KEY = os.getenv("VAULT_ENCRYPTION_KEY")
VAULT_KEY_SALT = os.getenv("VAULT_KEY_SALT")


# ===========================================
# CRYPTO LAYER
# ===========================================

class VaultCrypto:
    """ChaCha20-Poly1305 encryption with PBKDF2 key derivation."""
    
    SALT_SIZE = 32
    NONCE_SIZE = 12
    KEY_SIZE = 32
    ITERATIONS = 100_000
    
    _instance_key: bytes = None
    _instance_salt: bytes = None
    
    @classmethod
    def init_server_key(cls):
        """Initialize server encryption key."""
        if cls._instance_key is not None:
            return
        
        if VAULT_ENCRYPTION_KEY:
            # Use configured key
            cls._instance_key = base64.b64decode(VAULT_ENCRYPTION_KEY)
            cls._instance_salt = base64.b64decode(VAULT_KEY_SALT) if VAULT_KEY_SALT else None
        else:
            # Development mode: derive from default password
            # WARNING: Change in production!
            print("⚠️  WARNING: Using development encryption key. Set VAULT_ENCRYPTION_KEY in production!")
            key, salt = cls.derive_key("0711-development-key-change-me")
            cls._instance_key = key
            cls._instance_salt = salt
    
    @classmethod
    def get_key(cls) -> bytes:
        """Get the server encryption key."""
        if cls._instance_key is None:
            cls.init_server_key()
        return cls._instance_key
    
    @staticmethod
    def derive_key(password: str, salt: bytes = None) -> Tuple[bytes, bytes]:
        """Derive encryption key from password."""
        if salt is None:
            salt = secrets.token_bytes(VaultCrypto.SALT_SIZE)
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA512(),
            length=VaultCrypto.KEY_SIZE,
            salt=salt,
            iterations=VaultCrypto.ITERATIONS,
            backend=default_backend()
        )
        key = kdf.derive(password.encode('utf-8'))
        return key, salt
    
    @staticmethod
    def encrypt(plaintext: bytes, key: bytes = None) -> bytes:
        """Encrypt data with ChaCha20-Poly1305."""
        if key is None:
            key = VaultCrypto.get_key()
        
        nonce = secrets.token_bytes(VaultCrypto.NONCE_SIZE)
        cipher = ChaCha20Poly1305(key)
        ciphertext = cipher.encrypt(nonce, plaintext, None)
        # Format: nonce (12) + ciphertext (includes 16-byte auth tag)
        return nonce + ciphertext
    
    @staticmethod
    def decrypt(ciphertext: bytes, key: bytes = None) -> bytes:
        """Decrypt data with ChaCha20-Poly1305."""
        if key is None:
            key = VaultCrypto.get_key()
        
        nonce = ciphertext[:VaultCrypto.NONCE_SIZE]
        data = ciphertext[VaultCrypto.NONCE_SIZE:]
        cipher = ChaCha20Poly1305(key)
        return cipher.decrypt(nonce, data, None)
    
    @staticmethod
    def encrypt_for_user(plaintext: bytes, user_key: bytes) -> bytes:
        """
        Double encryption: server key + user key.
        Content encrypted first with user's key, then server key.
        User key derived from their master password (client-side).
        """
        # First layer: user encryption (if user_key provided)
        if user_key:
            user_cipher = ChaCha20Poly1305(user_key)
            user_nonce = secrets.token_bytes(VaultCrypto.NONCE_SIZE)
            user_encrypted = user_nonce + user_cipher.encrypt(user_nonce, plaintext, None)
        else:
            user_encrypted = plaintext
        
        # Second layer: server encryption
        return VaultCrypto.encrypt(user_encrypted)
    
    @staticmethod
    def decrypt_for_user(ciphertext: bytes, user_key: bytes = None) -> bytes:
        """
        Double decryption: server key + user key.
        """
        # First layer: server decryption
        server_decrypted = VaultCrypto.decrypt(ciphertext)
        
        # Second layer: user decryption (if user_key provided)
        if user_key:
            user_nonce = server_decrypted[:VaultCrypto.NONCE_SIZE]
            user_data = server_decrypted[VaultCrypto.NONCE_SIZE:]
            user_cipher = ChaCha20Poly1305(user_key)
            return user_cipher.decrypt(user_nonce, user_data, None)
        
        return server_decrypted


# ===========================================
# STORAGE INTERFACE (PostgreSQL)
# ===========================================

@dataclass
class StoredFile:
    """Represents a stored file."""
    storage_key: str
    encrypted_content: bytes
    original_size: int
    encrypted_size: int


class AlbertStorage:
    """
    PostgreSQL-based encrypted storage.
    
    Files are encrypted with ChaCha20-Poly1305 and stored in PostgreSQL BYTEA.
    No external object storage (MinIO/S3) required.
    """
    
    def __init__(self, db_pool):
        """Initialize with asyncpg connection pool."""
        self.db = db_pool
        VaultCrypto.init_server_key()
    
    async def ensure_table(self):
        """Ensure storage table exists."""
        async with self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS vault_content (
                    id SERIAL PRIMARY KEY,
                    storage_key TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    encrypted_content BYTEA NOT NULL,
                    original_size INTEGER NOT NULL,
                    encrypted_size INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accessed_at TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_content_user ON vault_content(user_id);
                CREATE INDEX IF NOT EXISTS idx_content_key ON vault_content(storage_key);
            """)
    
    async def store(
        self,
        user_id: str,
        storage_key: str,
        content: bytes,
        user_key: bytes = None
    ) -> StoredFile:
        """Store encrypted content."""
        import hashlib
        
        # Encrypt content
        encrypted = VaultCrypto.encrypt_for_user(content, user_key)
        checksum = hashlib.sha256(content).hexdigest()
        
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO vault_content 
                (storage_key, user_id, encrypted_content, original_size, encrypted_size, checksum)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (storage_key) DO UPDATE SET
                    encrypted_content = EXCLUDED.encrypted_content,
                    original_size = EXCLUDED.original_size,
                    encrypted_size = EXCLUDED.encrypted_size,
                    checksum = EXCLUDED.checksum
            """, storage_key, user_id, encrypted, len(content), len(encrypted), checksum)
        
        return StoredFile(
            storage_key=storage_key,
            encrypted_content=encrypted,
            original_size=len(content),
            encrypted_size=len(encrypted)
        )
    
    async def retrieve(
        self,
        storage_key: str,
        user_id: str = None,
        user_key: bytes = None
    ) -> bytes:
        """Retrieve and decrypt content."""
        async with self.db.acquire() as conn:
            query = "SELECT encrypted_content FROM vault_content WHERE storage_key = $1"
            params = [storage_key]
            
            if user_id:
                query += " AND user_id = $2"
                params.append(user_id)
            
            row = await conn.fetchrow(query, *params)
            
            if not row:
                raise FileNotFoundError(f"Content not found: {storage_key}")
            
            # Update access time
            await conn.execute(
                "UPDATE vault_content SET accessed_at = CURRENT_TIMESTAMP WHERE storage_key = $1",
                storage_key
            )
            
            # Decrypt
            return VaultCrypto.decrypt_for_user(row['encrypted_content'], user_key)
    
    async def delete(self, storage_key: str, user_id: str = None) -> bool:
        """Delete content."""
        async with self.db.acquire() as conn:
            query = "DELETE FROM vault_content WHERE storage_key = $1"
            params = [storage_key]
            
            if user_id:
                query += " AND user_id = $2"
                params.append(user_id)
            
            result = await conn.execute(query, *params)
            return "DELETE 1" in result
    
    async def exists(self, storage_key: str) -> bool:
        """Check if content exists."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM vault_content WHERE storage_key = $1",
                storage_key
            )
            return row is not None
    
    async def get_user_storage(self, user_id: str) -> dict:
        """Get storage stats for user."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as file_count,
                    COALESCE(SUM(original_size), 0) as total_original,
                    COALESCE(SUM(encrypted_size), 0) as total_encrypted
                FROM vault_content
                WHERE user_id = $1
            """, user_id)
            
            return {
                'file_count': row['file_count'],
                'total_bytes': row['total_original'],
                'encrypted_bytes': row['total_encrypted']
            }


# ===========================================
# COMPATIBILITY LAYER (replaces old storage.py)
# ===========================================

# Global storage instance
_storage: Optional[AlbertStorage] = None


def init_storage(db_pool):
    """Initialize storage with database pool."""
    global _storage
    _storage = AlbertStorage(db_pool)
    return _storage


def get_storage() -> AlbertStorage:
    """Get storage instance."""
    if _storage is None:
        raise RuntimeError("Storage not initialized. Call init_storage() first.")
    return _storage


# Legacy compatibility functions (for gradual migration)

def generate_upload_url(user_id: str, filename: str, content_type: str = None, expires=None):
    """
    DEPRECATED: Direct upload now supported.
    Returns None for upload_url (content posted directly to API).
    """
    import uuid as uuid_mod
    from pathlib import Path
    
    storage_key = f"{user_id}/{uuid_mod.uuid4()}"
    ext = Path(filename).suffix
    if ext:
        storage_key += ext
    
    # No presigned URL - direct upload to /vault/items/upload
    return None, storage_key


def generate_download_url(storage_key: str, expires=None, filename: str = None):
    """
    DEPRECATED: Direct download now supported.
    Returns internal API path.
    """
    return f"/vault/content/{storage_key}"


async def store_content(user_id: str, storage_key: str, content: bytes):
    """Store content directly (replaces presigned upload)."""
    storage = get_storage()
    return await storage.store(user_id, storage_key, content)


async def retrieve_content(storage_key: str, user_id: str = None):
    """Retrieve content directly (replaces presigned download)."""
    storage = get_storage()
    return await storage.retrieve(storage_key, user_id)


async def delete_content(storage_key: str, user_id: str = None):
    """Delete content."""
    storage = get_storage()
    return await storage.delete(storage_key, user_id)


def get_user_storage_used(user_id: str) -> int:
    """Get storage used by user in bytes (sync wrapper)."""
    # Note: This is a sync function for compatibility
    # In async context, use storage.get_user_storage() directly
    return 0


# ===========================================
# SELF TEST
# ===========================================

def test_crypto():
    """Test crypto functions."""
    print("🔐 Testing VaultCrypto...")
    
    # Test key derivation
    key1, salt = VaultCrypto.derive_key("test-password")
    key2, _ = VaultCrypto.derive_key("test-password", salt)
    assert key1 == key2, "Key derivation inconsistent"
    print("✓ Key derivation OK")
    
    # Test encryption
    plaintext = b"Hello, Albert! This is a test message."
    encrypted = VaultCrypto.encrypt(plaintext)
    decrypted = VaultCrypto.decrypt(encrypted)
    assert plaintext == decrypted, "Encryption/decryption failed"
    print("✓ Encryption OK")
    
    # Test double encryption
    user_key, _ = VaultCrypto.derive_key("user-password")
    double_encrypted = VaultCrypto.encrypt_for_user(plaintext, user_key)
    double_decrypted = VaultCrypto.decrypt_for_user(double_encrypted, user_key)
    assert plaintext == double_decrypted, "Double encryption failed"
    print("✓ Double encryption OK")
    
    print("🎉 All crypto tests passed!")


if __name__ == "__main__":
    test_crypto()
