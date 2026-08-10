import os
import sys
import json
import logging
from typing import Dict, Any, Optional
import jsonschema
from jsonschema import validate, ValidationError

# Standalone Logging Setup
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [CONFIG] %(message)s", datefmt="%H:%M:%S")

class ConfigLoader:
    """Centralized JSON loader with strict JSON Schema validation."""

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir:
            self.config_dir = config_dir
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.config_dir = os.path.abspath(os.path.join(base_dir, "..", "..", "config"))

    def load_json(self, filename: str) -> Dict[str, Any]:
        """Loads a raw JSON file without schema validation."""
        filepath = os.path.join(self.config_dir, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Configuration file not found: {filepath}")

        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_and_validate(self, data_filename: str, schema_filename: str, fail_fast: bool = True) -> Dict[str, Any]:
        """
        Loads a JSON file and validates it against a target schema.
        Fails fast by terminating the process if validation fails, unless fail_fast is False.
        """
        data = self.load_json(data_filename)
        schema = self.load_json(schema_filename)

        try:
            validate(instance=data, schema=schema)
            logging.info(f"Successfully validated '{data_filename}' against '{schema_filename}'.")
            return data
        except ValidationError as ve:
            logging.critical(f"\n{'='*60}\nFATAL CONFIGURATION ERROR in '{data_filename}'\n{'='*60}")
            logging.critical(f"Failed Element Path : {' -> '.join(str(p) for p in ve.path)}")
            logging.critical(f"Validation Error    : {ve.message}")
            logging.critical(f"{'='*60}\n")
            if fail_fast:
                sys.exit(1)
            raise ve

    def update_json_atomic(self, filename: str, callback) -> None:
        """
        Safely updates a JSON file using a file lock to prevent concurrent write issues.
        The callback function receives the parsed JSON dict, mutates it, and it gets saved.
        """
        from filelock import FileLock
        filepath = os.path.join(self.config_dir, filename)
        lockpath = filepath + ".lock"
        
        with FileLock(lockpath, timeout=5):
            data = {}
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        pass
            
            # Allow the callback to mutate data
            callback(data)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)



if __name__ == "__main__":
    # --- STANDALONE CLI RUNNER ---
    import argparse

    parser = argparse.ArgumentParser(description="Standalone JSON Schema Validator for JARVIS Configs.")
    parser.add_argument("data_file", help="Target JSON file inside config/ (e.g. intents.json)")
    parser.add_argument("schema_file", help="Schema JSON file inside config/ (e.g. intents_schema.json)")

    args = parser.parse_args()

    loader = ConfigLoader()
    print(f"\n--- Running pre-flight validation on '{args.data_file}' ---")
    loader.load_and_validate(args.data_file, args.schema_file)
    print("Pre-flight check passed!\n")