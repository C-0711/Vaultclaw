"""
Database connections for 0711 Vault API
MinIO removed - using Albert Storage (PostgreSQL + ChaCha20)
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from neo4j import AsyncGraphDatabase
from redis import asyncio as aioredis
import ollama
import structlog

from config import settings

logger = structlog.get_logger()

# SQLAlchemy
Base = declarative_base()

# Convert postgresql:// to postgresql+asyncpg://
db_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Neo4j
neo4j_driver = None

# Redis
redis_client = None

# Ollama
ollama_client = None


async def init_db():
    """Initialize all database connections."""
    global neo4j_driver, redis_client, ollama_client
    
    # Neo4j
    try:
        neo4j_driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
        await neo4j_driver.verify_connectivity()
        logger.info("Neo4j connected")
    except Exception as e:
        logger.warning(f"Neo4j connection failed: {e}")
    
    # Redis
    try:
        redis_client = await aioredis.from_url(settings.REDIS_URL)
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
    
    # Ollama
    try:
        ollama_client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
        logger.info("Ollama connected")
    except Exception as e:
        logger.warning(f"Ollama connection failed: {e}")
    
    # Note: Albert Storage is initialized in main.py lifespan
    logger.info("Albert Storage: initialized via main.py (MinIO replaced)")


async def get_db():
    """Get database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_neo4j():
    """Get Neo4j driver."""
    return neo4j_driver


def get_redis():
    """Get Redis client."""
    return redis_client


def get_ollama():
    """Get Ollama client."""
    return ollama_client
