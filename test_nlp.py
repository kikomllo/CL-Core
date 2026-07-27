import json
from src.nlp.clIntentEngine import IntentEngine
from src.utils.clConfigLoader import ConfigLoader

config = ConfigLoader().load_json("intents.json")
engine = IntentEngine(config, {"zero": "0", "one": "1"}, [])

print(engine.parse("turn on the desk lamp"))
