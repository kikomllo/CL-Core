import pytest
import os
import json
import sys
from unittest.mock import patch, mock_open

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clConfigLoader import ConfigLoader

@pytest.fixture
def dummy_config_dir(tmp_path):
    return str(tmp_path)

@pytest.fixture
def dummy_config_path(dummy_config_dir):
    config_file = os.path.join(dummy_config_dir, "core.json")
    with open(config_file, "w") as f:
        json.dump({"test_key": "test_value"}, f)
    return config_file

class TestConfigLoaderEdgeCases:
    
    def test_json_corruption_recovery(self, dummy_config_dir, dummy_config_path):
        """Test how atomic update handles a corrupted source JSON."""
        # Corrupt the file
        with open(dummy_config_path, "w") as f:
            f.write("{ invalid json")
            
        loader = ConfigLoader(config_dir=dummy_config_dir)
        
        def dummy_callback(data):
            data["new_key"] = "new_value"
            
        # Should catch JSONDecodeError, pass empty data, and write the new data
        loader.update_json_atomic("core.json", dummy_callback)
        
        with open(dummy_config_path, "r") as f:
            data = json.load(f)
            
        assert "new_key" in data

    @patch('filelock.FileLock')
    def test_lock_timeout(self, mock_filelock, dummy_config_dir, dummy_config_path):
        """Test atomic update gracefully handles lock acquisition timeouts."""
        import filelock
        # Make the lock's acquire method raise a Timeout
        mock_lock_instance = mock_filelock.return_value
        mock_lock_instance.__enter__.side_effect = filelock.Timeout(lock_file="core.json.lock")
        
        loader = ConfigLoader(config_dir=dummy_config_dir)
        def dummy_callback(data):
            pass
            
        with pytest.raises(filelock.Timeout):
            loader.update_json_atomic("core.json", dummy_callback)
