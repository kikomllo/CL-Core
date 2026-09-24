"""Hooks for the bridged Claude session, launched by the CLI itself (see
clPtyBridge.py's --settings), never run by hand. Must stay quiet and never
crash -- a hook failure would surface inside the TUI.

  (no args)      Stop hook: forward the finished turn's final answer to MQTT
                 so the bridge can speak it.
  --block-speak  PreToolUse hook: refuse the standing "speak a summary" shell
                 command, since the bridge already speaks every answer.
"""
import json
import sys

BLOCK_MESSAGE = (
    "Blocked: the host application already reads your final answer aloud. "
    "Do not run the speak command; just answer normally in text."
)


def extract_reply(stdin_text: str) -> str:
    try:
        return str(json.loads(stdin_text).get("last_assistant_message") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def is_speak_command(stdin_text: str) -> bool:
    try:
        command = json.loads(stdin_text).get("tool_input", {}).get("command", "")
    except (json.JSONDecodeError, AttributeError):
        return False
    return "jarvis/sys/speak" in str(command)


def forward_reply() -> None:
    text = extract_reply(sys.stdin.read())
    if not text:
        return
    import paho.mqtt.publish as publish
    publish.single("jarvis/claude/reply", json.dumps({"text": text}), hostname="localhost")


if __name__ == "__main__":
    try:
        if "--block-speak" in sys.argv:
            if is_speak_command(sys.stdin.read()):
                print(BLOCK_MESSAGE, file=sys.stderr)
                sys.exit(2)
        else:
            forward_reply()
    except Exception:
        pass
