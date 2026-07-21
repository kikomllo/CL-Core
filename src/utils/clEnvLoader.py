import os
import logging
from dotenv import load_dotenv, set_key

class EnvLoader:
    """Centralized Environment Variable Manager."""
    
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.env_path = os.path.abspath(os.path.join(self.base_dir, "..", "..", ".env"))
        
        if os.path.exists(self.env_path):
            load_dotenv(self.env_path)
        else:
            logging.warning(f"No .env file found at {self.env_path}")

    def get(self, key: str, default: str = "") -> str:
        return os.getenv(key, default)

    def update(self, key: str, value: str) -> None:
        """Updates the variable in the current runtime AND saves it to the .env file."""
        set_key(self.env_path, key, value)
        os.environ[key] = value