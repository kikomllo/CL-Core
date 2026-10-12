import json
import os
import re
from typing import Any, Dict, Optional, Set


class IntentSchema:
    """The action model's output schema (config/grammars/intent_schema.gbnf) as a validator.

    The grammar guarantees valid output but costs ~35ms per generated token (the grammar sampler walks the
    whole 150k-token vocabulary each step); free decoding costs ~8ms. So the SLM decodes freely, and only if
    this validator rejects the result does it re-run under the grammar. The validator is read from the same
    .gbnf file the generator writes, so it follows `config/intents.json` automatically."""

    def __init__(self, action_ids: Set[str], action_values: Set[str], string_fields: Set[str], number_fields: Set[str]):
        self.action_ids = action_ids
        self.action_values = action_values
        self.string_fields = string_fields
        self.number_fields = number_fields

    @classmethod
    def from_gbnf(cls, path: str) -> Optional["IntentSchema"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return None
        ids = cls._alternatives(text, "action-id")
        values = cls._alternatives(text, "action-value")
        if not ids or not values:
            return None
        strings, numbers = set(), set()
        for name, kind in re.findall(r'^\s*"\\"(\w+)\\""\s+ws\s+":"\s+ws\s+(string|number)\b', text, flags=re.MULTILINE):
            (strings if kind == "string" else numbers).add(name)
        return cls(ids, values, strings, numbers)

    @staticmethod
    def _alternatives(text: str, rule: str) -> Set[str]:
        """The quoted literals listed under `rule ::=` (one per line, `|`-separated)."""
        lines = text.splitlines()
        out: Set[str] = set()
        for i, line in enumerate(lines):
            if line.strip() == f"{rule} ::=":
                for follow in lines[i + 1:]:
                    stripped = follow.strip()
                    if not stripped.startswith('"'):
                        break
                    out.update(re.findall(r'"\\"([^"\\]+)\\""', stripped))
                break
        return out

    def parse(self, text: str) -> Optional[Dict[str, Any]]:
        """Parsed JSON if `text` is exactly what the grammar would have allowed, else None."""
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return None
        return obj if self.valid(obj) else None

    def valid(self, obj: Any) -> bool:
        if not isinstance(obj, dict) or set(obj.keys()) != {"actions"} or not isinstance(obj["actions"], list):
            return False
        return all(self._valid_action(a) for a in obj["actions"])

    def _valid_action(self, action: Any) -> bool:
        if not isinstance(action, dict) or action.get("action_id") not in self.action_ids:
            return False
        for key, value in action.items():
            if key == "action_id":
                continue
            if key == "action":
                if value not in self.action_values:
                    return False
            elif key in self.string_fields:
                if not isinstance(value, str):
                    return False
            elif key in self.number_fields:
                # The grammar allows only unsigned integers/decimals: no booleans, negatives or null.
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    return False
            else:
                return False
        return True
