import os
import json
import logging
import paho.mqtt.publish as publish

class ActionRouter:
    """
    Centralized MQTT dispatcher.
    Loads config/actions.json and routes action strings (e.g., 'ui.fullscreen')
    to their appropriate MQTT topics, validating schemas and injecting defaults.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ActionRouter, cls).__new__(cls)
            cls._instance._init()
        return cls._instance
        
    def _init(self):
        # Resolve path to config/actions.json
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(base_dir, "..", "..", "config", "actions.json")
        self.registry = self._load_registry()
        
    def _load_registry(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"[ACTION ROUTER] Failed to load actions.json: {e}")
            return {}
            
    def reload(self):
        self.registry = self._load_registry()
        
    def _get_action_def(self, action_path: str):
        keys = action_path.split(".")
        current = self.registry
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current
        
    def prepare(self, action_path: str, **kwargs):
        """
        Prepare an action for dispatch without publishing it.
        Returns (topic, payload) or (None, None) if validation fails.
        """
        action_def = self._get_action_def(action_path)
        
        if not action_def:
            logging.error(f"[ACTION ROUTER] Action '{action_path}' not found in registry.")
            return None, None
            
        topic = action_def.get("topic")
        if not topic:
            logging.error(f"[ACTION ROUTER] Action '{action_path}' is missing a 'topic' definition.")
            return None, None
            
        # Base payload from registry
        payload = action_def.get("payload", {}).copy()
        
        # Schema validation & defaults injection
        schema = action_def.get("schema", {})
        for field, rules in schema.items():
            if field in kwargs:
                payload[field] = kwargs[field]
            elif "default" in rules:
                payload[field] = rules["default"]
            elif rules.get("required", False):
                logging.error(f"[ACTION ROUTER] Missing required field '{field}' for action '{action_path}'")
                return None, None
                
        return topic, payload

    def dispatch(self, action_path: str, **kwargs):
        """
        Dispatch an action to the MQTT broker based on actions.json registry.
        
        :param action_path: Dot-separated path (e.g., 'ui.fullscreen')
        :param kwargs: Additional arguments to merge/validate against the schema
        """
        topic, payload = self.prepare(action_path, **kwargs)
        if not topic:
            return False
            
        # Publish
        try:
            publish.single(topic, json.dumps(payload), hostname="localhost")
            logging.info(f"[ACTION ROUTER] Dispatched '{action_path}' -> {topic}")
            return True
        except Exception as e:
            logging.error(f"[ACTION ROUTER] Failed to publish {action_path}: {e}")
            return False
