import pytest
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clEnvLoader import EnvLoader

@pytest.fixture(scope="module")
@patch("utils.clEnvLoader.load_dotenv")
@patch("utils.clEnvLoader.os.path.exists", return_value=True)
def env_loader(mock_exists, mock_load):
    return EnvLoader()

class TestEnvLoaderUtility:
    """Validates the centralized .env single-source-of-truth mapping."""

    @pytest.mark.parametrize("key, default_val, expected_val, inject_val", [
        ("MOCK_KEY_1", "", "super_secret", "super_secret"),
        ("MOCK_KEY_2", "fallback", "custom_val", "custom_val"),
        ("MISSING_KEY", "fallback", "fallback", None)
    ])
    def test_env_retrieval(self, env_loader, key, default_val, expected_val, inject_val):
        if inject_val:
            os.environ[key] = inject_val
        elif key in os.environ:
            del os.environ[key]
            
        assert env_loader.get(key, default_val) == expected_val

    @patch("utils.clEnvLoader.set_key")
    def test_env_update(self, mock_set_key, env_loader):
        # Update the environment state
        env_loader.update("TEST_UPDATE_KEY", "new_value")
        
        # 1. Ensure it updated the live os.environ mapping for this process
        assert os.environ["TEST_UPDATE_KEY"] == "new_value"
        
        # 2. Ensure it fired the set_key command to write to the physical .env file
        mock_set_key.assert_called_once()
        args, _ = mock_set_key.call_args
        assert args[1] == "TEST_UPDATE_KEY"
        assert args[2] == "new_value"