import pytest
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clControl import LightManager, poll_light_status

@pytest.fixture
def manager():
    with patch.dict(os.environ, {"TAPO_EMAIL": "test@test.com", "TAPO_PASSWORD": "dummy", "LIGHT_TYPE": "tapo"}):
        with patch('utils.clEnvLoader.load_dotenv'):
            from clControl import LightManager
            m = LightManager()
            
    m.last_discovered_devices = [
        {"type": "wiz", "model": "WIZ_BULB", "ip": "192.168.1.88", "mac": "cc40851c1118"},
        {"type": "wiz", "model": "WIZ_BULB", "ip": "192.168.1.217", "mac": "444f8e30aa06"},
        {"type": "tapo", "model": "L530", "ip": "192.168.1.111", "mac": "10:5A:95:B7:0C:86"}
    ]

    m.word_to_number = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4"}
    
    m.update_env_credentials = MagicMock() 
    m._save_devices = MagicMock()
    m._save_last_target = MagicMock()
    
    return m

class TestLightManagerResolution:
    """Tests the fuzzy routing, collision handling, and device resolution of the LightManager."""

    @pytest.mark.parametrize("spoken_text, expected_status, expected_ip, expected_mac, expected_type", [
        # --- 1. DIRECT INDEXING ---
        (2, "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        ("0", "success", "192.168.1.88", "cc40851c1118", "wiz"),
        ("device two please", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        
        # --- 2. SEMANTIC & BRAND TARGETING ---
        ("save the tapo bulb", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        ("connect to the l530", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        
        # --- 3. COLLISIONS & TARGETED RESOLUTION ---
        ("use the wiz bulb", "error", None, None, None), # Collision (2 Wiz bulbs found)
        ("wiz bulb 88", "success", "192.168.1.88", "cc40851c1118", "wiz"), # Resolves collision
        ("192.168.1.217", "success", "192.168.1.217", "444f8e30aa06", "wiz"), # Exact IP match
        
        # --- 4. PRECEDENCE & CHAOS FORMATTING ---
        ("tapo bulb 0", "success", "192.168.1.88", "cc40851c1118", "wiz"), # Numeric index beats brand
        ("one hundred", "success", "192.168.1.217", "444f8e30aa06", "wiz"), # First valid number (1) wins
        ("   TaPo   BuLb  ", "success", "192.168.1.111", "10:5A:95:B7:0C:86", "tapo"),
        
        # --- 5. OUT OF BOUNDS & GARBAGE INPUT ---
        (1030, "error", None, None, None),
        ("device four", "error", None, None, None),
        ("save the philips hue", "error", None, None, None),
        ("   ", "error", None, None, None),
    ])
    
    def test_device_routing(self, manager, spoken_text, expected_status, expected_ip, expected_mac, expected_type):
        manager.update_env_credentials.reset_mock()
        result = manager._handle_discovery_selection(spoken_text)
        assert result["status"] == expected_status, f"Status mismatch for '{spoken_text}'"
        if expected_status == "success":
            manager.update_env_credentials.assert_called_with(expected_ip, expected_mac, expected_type, "main")

    def test_octet_collision_prevention(self, manager):
        """Ensures '11' targets .11 and not .111 when evaluating octets."""
        manager.last_discovered_devices = [
            {"type": "tapo", "model": "L530", "ip": "192.168.1.11", "mac": "AA"},
            {"type": "tapo", "model": "L530", "ip": "192.168.1.111", "mac": "BB"}
        ]
        
        result = manager._handle_discovery_selection("tapo 11")
        
        assert result["status"] == "success", "Failed to resolve device cleanly."
        manager.update_env_credentials.assert_called_with("192.168.1.11", "AA", "tapo", "main")

    def test_empty_memory_catch(self, manager):
        """Ensures the system rejects selection attempts if no scan was performed."""
        manager.last_discovered_devices = []
        
        result = manager._handle_discovery_selection(1)
        
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_last_target_memory(self, manager, mocker):
        """Ensures generic light commands target the last modified light instead of all."""
        manager.lights = {
            "bedroom": {"ip": "192.168.1.10", "mac": "AA", "type": "wiz"},
            "desk_light": {"ip": "192.168.1.11", "mac": "BB", "type": "wiz"}
        }
        mocker.patch.object(manager, '_execute_wiz_target')
        
        # 1. Reset last_target to 'all'
        manager.last_target = "all"
        assert manager.last_target == "all"

        # 2. Control bedroom light explicitly
        await manager.control_bulb(off=True, target_name="bedroom light")
        assert manager.last_target == "bedroom"

        # 3. Generic turn off the light should target bedroom, not all
        await manager.control_bulb(off=True, target_name="the light")
        assert manager.last_target == "bedroom"

        # 4. Explicit turn off all lights sets last_target to all
        await manager.control_bulb(off=True, target_name="all lights")
        assert manager.last_target == "all"