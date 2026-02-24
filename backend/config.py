"""
Configuration settings for 0711 Vault API
"""

from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    # App
    APP_URL: str = "https://vault.0711.io"
    
    # Database
    DATABASE_URL: str = "postgresql://vault:vault_secret@localhost:5432/vault"
    
    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j_secret"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    
    # Albert Storage (replaces MinIO)
    VAULT_ENCRYPTION_KEY: str = ""  # Base64-encoded 32-byte key
    VAULT_KEY_SALT: str = ""  # Base64-encoded salt
    
    # Ollama
    OLLAMA_HOST: str = "http://localhost:11434"
    EMBEDDING_MODEL: str = "bge-m3:latest"
    VISION_MODEL: str = "llama4:latest"
    
    # Auth - JWT
    JWT_SECRET: str = "change_this_to_a_real_secret_key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    
    # OAuth - GitHub
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    
    # OAuth - Google
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    
    # Keycloak (optional)
    KEYCLOAK_URL: str = "http://localhost:8180"
    KEYCLOAK_REALM: str = "vault"
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "https://vault.0711.io"]
    
    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 100
    
    # File limits
    MAX_FILE_SIZE_MB: int = 100
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
