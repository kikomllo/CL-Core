import redis
import logging
from typing import Optional
from utils.clEnvLoader import EnvLoader

class RedisClient:
    """Centralized Redis connection manager utilizing connection pooling."""
    
    _pool: Optional[redis.ConnectionPool] = None

    @classmethod
    def get_client(cls) -> redis.Redis:
        """Returns a configured Redis client drawing from the shared connection pool."""
        if cls._pool is None:
            env = EnvLoader()
            host = env.get("REDIS_HOST", "localhost")
            port_str = env.get("REDIS_PORT", "6379")
            db_str = env.get("REDIS_DB", "0")
            
            try:
                port = int(port_str)
                db = int(db_str)
            except ValueError:
                port = 6379
                db = 0
                logging.warning(f"Invalid REDIS_PORT or REDIS_DB in config, falling back to defaults.")

            cls._pool = redis.ConnectionPool(
                host=host, 
                port=port, 
                db=db, 
                decode_responses=True
            )
            logging.info(f"Initialized Redis Connection Pool targeting {host}:{port}/{db}")

        return redis.Redis(connection_pool=cls._pool)
