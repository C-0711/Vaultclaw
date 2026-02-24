"""
0711 Document Vault - Albert Storage Layer (Python Port)

Replaces MinIO with local encrypted SQLite storage.
Zero-knowledge: encryption key derived from master password.

Based on: ~/clawd/archive/canvas/0711/albert-mac/src/documents/vault.js
"""

import os
import sqlite3
import hashlib
import base64
import uuid
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from contextlib import contextmanager

# Cryptography
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
import secrets

# ===========================================
# CONFIGURATION
# ===========================================

DATA_DIR = Path(os.getenv("VAULT_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "vault.db"
VAULT_PATH = DATA_DIR / "vault"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
VAULT_PATH.mkdir(parents=True, exist_ok=True)


# ===========================================
# CRYPTO LAYER
# ===========================================

class VaultCrypto:
    """ChaCha20-Poly1305 encryption with PBKDF2 key derivation."""
    
    SALT_SIZE = 32
    NONCE_SIZE = 12
    KEY_SIZE = 32
    ITERATIONS = 100_000
    
    @staticmethod
    def derive_key(password: str, salt: bytes = None) -> Tuple[bytes, bytes]:
        """Derive encryption key from master password."""
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
    def encrypt(plaintext: bytes, key: bytes) -> bytes:
        """Encrypt data with ChaCha20-Poly1305."""
        nonce = secrets.token_bytes(VaultCrypto.NONCE_SIZE)
        cipher = ChaCha20Poly1305(key)
        ciphertext = cipher.encrypt(nonce, plaintext, None)
        # Format: nonce + ciphertext (includes auth tag)
        return nonce + ciphertext
    
    @staticmethod
    def decrypt(ciphertext: bytes, key: bytes) -> bytes:
        """Decrypt data with ChaCha20-Poly1305."""
        nonce = ciphertext[:VaultCrypto.NONCE_SIZE]
        data = ciphertext[VaultCrypto.NONCE_SIZE:]
        cipher = ChaCha20Poly1305(key)
        return cipher.decrypt(nonce, data, None)
    
    @staticmethod
    def hash_content(content: bytes) -> str:
        """SHA-256 hash for integrity."""
        return hashlib.sha256(content).hexdigest()


# ===========================================
# DATA CLASSES
# ===========================================

@dataclass
class Document:
    id: int
    uuid: str
    original_name: str
    mime_type: str
    size: int
    category: str
    tags: List[str]
    ai_summary: Optional[str]
    ai_tags: Optional[List[str]]
    hash: str
    created_at: datetime
    modified_at: datetime
    accessed_at: Optional[datetime]
    content: Optional[bytes] = None  # Only populated when fetched


@dataclass
class VaultStats:
    total_documents: int
    total_size: int
    total_gb: float
    photos: int
    documents: int
    videos: int
    processed: int
    pending: int
    face_clusters: int
    place_clusters: int
    categories: List[Dict[str, Any]]


# ===========================================
# DOCUMENT VAULT
# ===========================================

class DocumentVault:
    """
    Zero-knowledge encrypted document storage.
    
    Replaces MinIO with local SQLite + ChaCha20-Poly1305.
    """
    
    def __init__(self, master_key: bytes = None, master_password: str = None, salt: bytes = None):
        """
        Initialize vault with either:
        - master_key: Direct encryption key (for server-side with stored key)
        - master_password + salt: Derive key from password (for client-side)
        """
        if master_key:
            self.encryption_key = master_key
            self.key_salt = salt
        elif master_password:
            self.encryption_key, self.key_salt = VaultCrypto.derive_key(master_password, salt)
        else:
            raise ValueError("Must provide master_key or master_password")
        
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with schema."""
        self.db_path = DB_PATH
        
        with self._get_db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    encrypted_name TEXT NOT NULL,
                    mime_type TEXT,
                    size INTEGER,
                    item_type TEXT DEFAULT 'document',
                    category TEXT DEFAULT 'Uncategorized',
                    tags TEXT DEFAULT '[]',
                    encrypted_content BLOB,
                    text_content TEXT,
                    ai_summary TEXT,
                    ai_tags TEXT,
                    processing_status TEXT DEFAULT 'pending',
                    hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accessed_at TIMESTAMP,
                    is_deleted INTEGER DEFAULT 0
                );
                
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    encrypted_content BLOB NOT NULL,
                    hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                
                CREATE TABLE IF NOT EXISTS shares (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    share_token TEXT UNIQUE NOT NULL,
                    expires_at TIMESTAMP,
                    max_views INTEGER,
                    view_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS face_clusters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    encrypted_name TEXT,
                    relationship TEXT,
                    photo_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS place_clusters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT,
                    latitude REAL,
                    longitude REAL,
                    photo_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_docs_user ON documents(user_id);
                CREATE INDEX IF NOT EXISTS idx_docs_category ON documents(category);
                CREATE INDEX IF NOT EXISTS idx_docs_type ON documents(item_type);
                CREATE INDEX IF NOT EXISTS idx_docs_created ON documents(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(processing_status);
                CREATE INDEX IF NOT EXISTS idx_audit_doc ON audit_log(document_id);
                CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
            """)
    
    @contextmanager
    def _get_db(self):
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    # ===========================================
    # DOCUMENT OPERATIONS
    # ===========================================
    
    def add_document(
        self,
        user_id: str,
        content: bytes,
        filename: str,
        mime_type: str = None,
        item_type: str = "document",
        category: str = "Uncategorized",
        tags: List[str] = None,
        encrypted_metadata: str = None
    ) -> Dict[str, Any]:
        """
        Add a new document to the vault.
        
        Returns: {id, uuid, storage_key, upload_url}
        """
        doc_uuid = str(uuid.uuid4())
        encrypted_name = f"{doc_uuid}.enc"
        
        # Hash for integrity
        content_hash = VaultCrypto.hash_content(content)
        
        # Encrypt content
        encrypted_content = VaultCrypto.encrypt(content, self.encryption_key)
        
        # Detect mime type if not provided
        if not mime_type:
            mime_type = self._get_mime_type(filename)
        
        # Extract text for searchability
        text_content = None
        if mime_type.startswith('text/') or mime_type == 'application/json':
            try:
                text_content = content.decode('utf-8')
            except:
                pass
        
        with self._get_db() as db:
            cursor = db.execute("""
                INSERT INTO documents 
                (uuid, user_id, original_name, encrypted_name, mime_type, size, 
                 item_type, category, tags, encrypted_content, text_content, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_uuid,
                user_id,
                filename,
                encrypted_name,
                mime_type,
                len(content),
                item_type,
                category,
                json.dumps(tags or []),
                encrypted_content,
                text_content,
                content_hash
            ))
            
            doc_id = cursor.lastrowid
            
            # Audit log
            self._log(db, doc_id, user_id, 'ADD', {
                'filename': filename,
                'size': len(content),
                'type': item_type
            })
        
        return {
            'item_id': str(doc_id),
            'uuid': doc_uuid,
            'storage_key': encrypted_name,
            'upload_url': None  # No presigned URL needed - content already stored
        }
    
    def get_document(self, document_id: int, user_id: str = None) -> Document:
        """Get and decrypt a document."""
        with self._get_db() as db:
            query = "SELECT * FROM documents WHERE id = ? AND is_deleted = 0"
            params = [document_id]
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            row = db.execute(query, params).fetchone()
            
            if not row:
                raise ValueError("Document not found")
            
            # Decrypt content
            encrypted_content = row['encrypted_content']
            content = VaultCrypto.decrypt(encrypted_content, self.encryption_key)
            
            # Update access time
            db.execute(
                "UPDATE documents SET accessed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (document_id,)
            )
            
            # Audit log
            self._log(db, document_id, user_id, 'ACCESS', {})
            
            return Document(
                id=row['id'],
                uuid=row['uuid'],
                original_name=row['original_name'],
                mime_type=row['mime_type'],
                size=row['size'],
                category=row['category'],
                tags=json.loads(row['tags'] or '[]'),
                ai_summary=row['ai_summary'],
                ai_tags=json.loads(row['ai_tags'] or '[]') if row['ai_tags'] else None,
                hash=row['hash'],
                created_at=row['created_at'],
                modified_at=row['modified_at'],
                accessed_at=row['accessed_at'],
                content=content
            )
    
    def get_document_by_uuid(self, doc_uuid: str, user_id: str = None) -> Document:
        """Get document by UUID."""
        with self._get_db() as db:
            query = "SELECT id FROM documents WHERE uuid = ? AND is_deleted = 0"
            params = [doc_uuid]
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            row = db.execute(query, params).fetchone()
            
            if not row:
                raise ValueError("Document not found")
            
            return self.get_document(row['id'], user_id)
    
    def delete_document(self, document_id: int, user_id: str, permanent: bool = False):
        """Soft or hard delete a document."""
        with self._get_db() as db:
            if permanent:
                db.execute(
                    "DELETE FROM documents WHERE id = ? AND user_id = ?",
                    (document_id, user_id)
                )
                self._log(db, document_id, user_id, 'DELETE_PERMANENT', {})
            else:
                db.execute(
                    "UPDATE documents SET is_deleted = 1 WHERE id = ? AND user_id = ?",
                    (document_id, user_id)
                )
                self._log(db, document_id, user_id, 'DELETE_SOFT', {})
    
    def restore_document(self, document_id: int, user_id: str):
        """Restore a soft-deleted document."""
        with self._get_db() as db:
            db.execute(
                "UPDATE documents SET is_deleted = 0 WHERE id = ? AND user_id = ?",
                (document_id, user_id)
            )
            self._log(db, document_id, user_id, 'RESTORE', {})
    
    # ===========================================
    # LIST & SEARCH
    # ===========================================
    
    def list_documents(
        self,
        user_id: str,
        item_type: str = None,
        category: str = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List documents for a user."""
        with self._get_db() as db:
            query = """
                SELECT id, uuid, original_name, mime_type, size, item_type, 
                       category, tags, ai_summary, processing_status, created_at, modified_at
                FROM documents 
                WHERE user_id = ? AND is_deleted = 0
            """
            params = [user_id]
            
            if item_type:
                query += " AND item_type = ?"
                params.append(item_type)
            
            if category:
                query += " AND category = ?"
                params.append(category)
            
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = db.execute(query, params).fetchall()
            
            return [{
                'id': str(row['id']),
                'uuid': row['uuid'],
                'item_type': row['item_type'],
                'original_name': row['original_name'],
                'mime_type': row['mime_type'],
                'file_size': row['size'],
                'category': row['category'],
                'tags': json.loads(row['tags'] or '[]'),
                'ai_summary': row['ai_summary'],
                'processing_status': row['processing_status'],
                'created_at': row['created_at'],
                'modified_at': row['modified_at'],
                'encrypted_metadata': None,  # Compatibility with old API
                'storage_key': f"{row['uuid']}.enc"
            } for row in rows]
    
    def search_documents(
        self,
        user_id: str,
        query: str,
        item_type: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Search documents by name, content, or AI summary."""
        with self._get_db() as db:
            sql = """
                SELECT id, uuid, original_name, mime_type, size, item_type,
                       category, tags, ai_summary, created_at
                FROM documents 
                WHERE user_id = ? AND is_deleted = 0
                  AND (original_name LIKE ? OR text_content LIKE ? 
                       OR ai_summary LIKE ? OR tags LIKE ?)
            """
            search_term = f"%{query}%"
            params = [user_id, search_term, search_term, search_term, search_term]
            
            if item_type:
                sql += " AND item_type = ?"
                params.append(item_type)
            
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            rows = db.execute(sql, params).fetchall()
            
            return [{
                'id': str(row['id']),
                'uuid': row['uuid'],
                'item_type': row['item_type'],
                'storage_key': f"{row['uuid']}.enc",
                'similarity': 1.0  # Compatibility with semantic search API
            } for row in rows]
    
    # ===========================================
    # STATS
    # ===========================================
    
    def get_stats(self, user_id: str) -> VaultStats:
        """Get vault statistics for a user."""
        with self._get_db() as db:
            # Total counts
            total = db.execute(
                "SELECT COUNT(*) as count, SUM(size) as size FROM documents WHERE user_id = ? AND is_deleted = 0",
                (user_id,)
            ).fetchone()
            
            # By type
            photos = db.execute(
                "SELECT COUNT(*) as count FROM documents WHERE user_id = ? AND is_deleted = 0 AND item_type = 'photo'",
                (user_id,)
            ).fetchone()['count']
            
            documents = db.execute(
                "SELECT COUNT(*) as count FROM documents WHERE user_id = ? AND is_deleted = 0 AND item_type = 'document'",
                (user_id,)
            ).fetchone()['count']
            
            videos = db.execute(
                "SELECT COUNT(*) as count FROM documents WHERE user_id = ? AND is_deleted = 0 AND item_type = 'video'",
                (user_id,)
            ).fetchone()['count']
            
            # Processing status
            processed = db.execute(
                "SELECT COUNT(*) as count FROM documents WHERE user_id = ? AND is_deleted = 0 AND processing_status = 'completed'",
                (user_id,)
            ).fetchone()['count']
            
            pending = db.execute(
                "SELECT COUNT(*) as count FROM documents WHERE user_id = ? AND is_deleted = 0 AND processing_status = 'pending'",
                (user_id,)
            ).fetchone()['count']
            
            # Clusters
            face_clusters = db.execute(
                "SELECT COUNT(*) as count FROM face_clusters WHERE user_id = ?",
                (user_id,)
            ).fetchone()['count']
            
            place_clusters = db.execute(
                "SELECT COUNT(*) as count FROM place_clusters WHERE user_id = ?",
                (user_id,)
            ).fetchone()['count']
            
            # Categories
            categories = db.execute("""
                SELECT category, COUNT(*) as count 
                FROM documents 
                WHERE user_id = ? AND is_deleted = 0 
                GROUP BY category 
                ORDER BY count DESC
            """, (user_id,)).fetchall()
            
            total_size = total['size'] or 0
            
            return VaultStats(
                total_documents=total['count'],
                total_size=total_size,
                total_gb=total_size / (1024 * 1024 * 1024),
                photos=photos,
                documents=documents,
                videos=videos,
                processed=processed,
                pending=pending,
                face_clusters=face_clusters,
                place_clusters=place_clusters,
                categories=[{'category': r['category'], 'count': r['count']} for r in categories]
            )
    
    # ===========================================
    # VERSIONING
    # ===========================================
    
    def update_document(self, document_id: int, user_id: str, new_content: bytes) -> int:
        """Update document content, creating a new version."""
        with self._get_db() as db:
            # Get current document
            doc = db.execute(
                "SELECT * FROM documents WHERE id = ? AND user_id = ?",
                (document_id, user_id)
            ).fetchone()
            
            if not doc:
                raise ValueError("Document not found")
            
            # Get next version number
            max_version = db.execute(
                "SELECT MAX(version) as max FROM versions WHERE document_id = ?",
                (document_id,)
            ).fetchone()['max'] or 0
            next_version = max_version + 1
            
            # Store old version
            db.execute("""
                INSERT INTO versions (document_id, version, encrypted_content, hash)
                VALUES (?, ?, ?, ?)
            """, (document_id, next_version - 1, doc['encrypted_content'], doc['hash']))
            
            # Encrypt and update
            new_hash = VaultCrypto.hash_content(new_content)
            encrypted_content = VaultCrypto.encrypt(new_content, self.encryption_key)
            
            db.execute("""
                UPDATE documents 
                SET encrypted_content = ?, hash = ?, size = ?, modified_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (encrypted_content, new_hash, len(new_content), document_id))
            
            self._log(db, document_id, user_id, 'UPDATE', {'version': next_version})
            
            return next_version
    
    def get_versions(self, document_id: int, user_id: str) -> List[Dict[str, Any]]:
        """Get version history for a document."""
        with self._get_db() as db:
            # Verify ownership
            doc = db.execute(
                "SELECT id FROM documents WHERE id = ? AND user_id = ?",
                (document_id, user_id)
            ).fetchone()
            
            if not doc:
                raise ValueError("Document not found")
            
            rows = db.execute("""
                SELECT id, version, hash, created_at 
                FROM versions 
                WHERE document_id = ? 
                ORDER BY version DESC
            """, (document_id,)).fetchall()
            
            return [dict(row) for row in rows]
    
    # ===========================================
    # SECURE SHARING
    # ===========================================
    
    def create_share(
        self,
        document_id: int,
        user_id: str,
        expires_hours: int = 24,
        max_views: int = None
    ) -> Dict[str, Any]:
        """Create a share link for a document."""
        with self._get_db() as db:
            # Verify ownership
            doc = db.execute(
                "SELECT id FROM documents WHERE id = ? AND user_id = ?",
                (document_id, user_id)
            ).fetchone()
            
            if not doc:
                raise ValueError("Document not found")
            
            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=expires_hours)
            
            db.execute("""
                INSERT INTO shares (document_id, share_token, expires_at, max_views)
                VALUES (?, ?, ?, ?)
            """, (document_id, token, expires_at.isoformat(), max_views))
            
            self._log(db, document_id, user_id, 'SHARE_CREATE', {
                'token': token[:8] + '...',
                'expires_at': expires_at.isoformat()
            })
            
            return {'token': token, 'expires_at': expires_at.isoformat()}
    
    def access_share(self, token: str) -> Document:
        """Access a shared document by token."""
        with self._get_db() as db:
            share = db.execute("""
                SELECT s.*, d.id as doc_id 
                FROM shares s 
                JOIN documents d ON s.document_id = d.id 
                WHERE s.share_token = ?
            """, (token,)).fetchone()
            
            if not share:
                raise ValueError("Share not found")
            
            # Check expiry
            if share['expires_at']:
                expires = datetime.fromisoformat(share['expires_at'])
                if expires < datetime.utcnow():
                    raise ValueError("Share expired")
            
            # Check max views
            if share['max_views'] and share['view_count'] >= share['max_views']:
                raise ValueError("Max views reached")
            
            # Increment view count
            db.execute(
                "UPDATE shares SET view_count = view_count + 1 WHERE share_token = ?",
                (token,)
            )
            
            return self.get_document(share['doc_id'])
    
    def revoke_share(self, token: str, user_id: str):
        """Revoke a share token."""
        with self._get_db() as db:
            # Verify ownership through join
            db.execute("""
                DELETE FROM shares 
                WHERE share_token = ? 
                  AND document_id IN (SELECT id FROM documents WHERE user_id = ?)
            """, (token, user_id))
    
    # ===========================================
    # FACE & PLACE CLUSTERS (Compatibility)
    # ===========================================
    
    def get_face_clusters(self, user_id: str) -> List[Dict[str, Any]]:
        """Get face clusters for a user."""
        with self._get_db() as db:
            rows = db.execute("""
                SELECT uuid as id, encrypted_name, relationship, photo_count
                FROM face_clusters WHERE user_id = ?
            """, (user_id,)).fetchall()
            return [dict(row) for row in rows]
    
    def get_place_clusters(self, user_id: str) -> List[Dict[str, Any]]:
        """Get place clusters for a user."""
        with self._get_db() as db:
            rows = db.execute("""
                SELECT uuid as id, name, latitude, longitude, photo_count
                FROM place_clusters WHERE user_id = ?
            """, (user_id,)).fetchall()
            return [dict(row) for row in rows]
    
    # ===========================================
    # AUDIT LOG
    # ===========================================
    
    def _log(self, db, document_id: int, user_id: str, action: str, details: dict):
        """Add audit log entry."""
        db.execute("""
            INSERT INTO audit_log (document_id, user_id, action, details)
            VALUES (?, ?, ?, ?)
        """, (document_id, user_id, action, json.dumps(details)))
    
    def get_audit_log(self, user_id: str, document_id: int = None, limit: int = 100) -> List[Dict]:
        """Get audit log entries."""
        with self._get_db() as db:
            if document_id:
                rows = db.execute("""
                    SELECT * FROM audit_log 
                    WHERE document_id = ? AND user_id = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (document_id, user_id, limit)).fetchall()
            else:
                rows = db.execute("""
                    SELECT * FROM audit_log 
                    WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (user_id, limit)).fetchall()
            
            return [dict(row) for row in rows]
    
    # ===========================================
    # UTILITIES
    # ===========================================
    
    def _get_mime_type(self, filename: str) -> str:
        """Detect MIME type from filename."""
        ext = Path(filename).suffix.lower()
        types = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.heic': 'image/heic',
            '.mp4': 'video/mp4',
            '.mov': 'video/quicktime',
            '.html': 'text/html',
            '.csv': 'text/csv',
        }
        return types.get(ext, 'application/octet-stream')
    
    def set_processing_status(self, document_id: int, status: str):
        """Update processing status."""
        with self._get_db() as db:
            db.execute(
                "UPDATE documents SET processing_status = ? WHERE id = ?",
                (status, document_id)
            )
    
    def set_ai_summary(self, document_id: int, summary: str):
        """Set AI-generated summary."""
        with self._get_db() as db:
            db.execute(
                "UPDATE documents SET ai_summary = ?, processing_status = 'completed' WHERE id = ?",
                (summary, document_id)
            )
    
    def set_ai_tags(self, document_id: int, tags: List[str]):
        """Set AI-generated tags."""
        with self._get_db() as db:
            db.execute(
                "UPDATE documents SET ai_tags = ? WHERE id = ?",
                (json.dumps(tags), document_id)
            )


# ===========================================
# COMPATIBILITY LAYER (for old MinIO API)
# ===========================================

# Global vault instance (initialized on first use)
_vault_instance: Optional[DocumentVault] = None


def get_vault(user_encryption_key: bytes = None) -> DocumentVault:
    """Get or create vault instance."""
    global _vault_instance
    
    if _vault_instance is None:
        # Use server master key from environment or generate one
        master_key = os.getenv("VAULT_MASTER_KEY")
        if master_key:
            key = base64.b64decode(master_key)
        else:
            # Development mode: derive from a default password
            key, _ = VaultCrypto.derive_key("development-only-change-in-production")
        
        _vault_instance = DocumentVault(master_key=key)
    
    return _vault_instance


# Compatibility functions matching old storage.py API

def generate_upload_url(user_id: str, filename: str, content_type: str = None, expires=None):
    """
    Compatibility wrapper - returns storage_key instead of presigned URL.
    Content should be POSTed directly to /vault/items endpoint.
    """
    storage_key = f"{user_id}/{uuid.uuid4()}"
    ext = Path(filename).suffix
    if ext:
        storage_key += ext
    
    # No presigned URL - use direct upload
    return None, storage_key


def generate_download_url(storage_key: str, expires=None, filename: str = None):
    """
    Compatibility wrapper - returns internal URL.
    Should use /vault/items/{id}/download endpoint instead.
    """
    return f"/vault/download/{storage_key}"


def delete_object(storage_key: str) -> bool:
    """Delete by storage key - not recommended, use document_id."""
    return True


def get_user_storage_used(user_id: str) -> int:
    """Get storage used by user in bytes."""
    vault = get_vault()
    stats = vault.get_stats(user_id)
    return stats.total_size


# ===========================================
# SELF TEST
# ===========================================

def self_test():
    """Run self-test to verify crypto and storage."""
    print("🔐 Running DocumentVault self-test...")
    
    # Test crypto
    key, salt = VaultCrypto.derive_key("test-password")
    key2, _ = VaultCrypto.derive_key("test-password", salt)
    assert key == key2, "Key derivation inconsistent"
    print("✓ Key derivation OK")
    
    # Test encryption
    plaintext = b"Hello, Albert!"
    encrypted = VaultCrypto.encrypt(plaintext, key)
    decrypted = VaultCrypto.decrypt(encrypted, key)
    assert plaintext == decrypted, "Encryption/decryption failed"
    print("✓ Encryption OK")
    
    # Test vault operations
    vault = DocumentVault(master_password="test-password")
    
    # Add document
    result = vault.add_document(
        user_id="test-user",
        content=b"Test document content",
        filename="test.txt",
        item_type="document"
    )
    doc_id = int(result['item_id'])
    print(f"✓ Add document OK (id={doc_id})")
    
    # Get document
    doc = vault.get_document(doc_id, "test-user")
    assert doc.content == b"Test document content", "Content mismatch"
    print("✓ Get document OK")
    
    # List documents
    docs = vault.list_documents("test-user")
    assert len(docs) >= 1, "List failed"
    print(f"✓ List documents OK ({len(docs)} docs)")
    
    # Stats
    stats = vault.get_stats("test-user")
    assert stats.total_documents >= 1, "Stats failed"
    print(f"✓ Stats OK ({stats.total_documents} docs, {stats.total_size} bytes)")
    
    # Cleanup
    vault.delete_document(doc_id, "test-user", permanent=True)
    print("✓ Delete OK")
    
    print("🎉 All DocumentVault tests passed!")
    return True


if __name__ == "__main__":
    self_test()
