"""Powering the computer off by voice: an exact phrase, a spoken yes, a delay in which "cancel" still works. Nothing else may shut the machine down."""
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clDaemon import CentralDaemon, POWEROFF_CONFIRM_S, POWEROFF_DELAY_S
from utils.clConfirm import is_affirmative, is_shutdown_request


@pytest.fixture
def daemon(mocker):
    def fake_slm(core_config):
        engine = MagicMock(enabled=False)
        engine.parse_intent_async = AsyncMock(return_value=None)
        engine.generate_reply_async = AsyncMock(return_value=None)
        return engine
    mocker.patch("clDaemon.SLMInferenceEngine", side_effect=fake_slm)
    mocker.patch("clDaemon.ReplySLMEngine", side_effect=fake_slm)
    d = CentralDaemon()
    d.loader.update_json_atomic = MagicMock()
    return d


def ids(queue):
    return [a for _, a in queue]


# ------------------------------------------------------------------------------------------------ the words
class TestTheExactPhrase:
    @pytest.mark.parametrize("text", [
        "shut down the computer", "Shut down the computer.", "shutdown the computer", "shut down my computer", "shut down the pc", "turn off the computer",
        "turn off my pc", "turn the computer off", "power off the computer", "power down the pc", "switch off the computer", "shut the computer down",
        "shut my pc down now", "please shut down the computer", "hey jarvis, turn off my pc", "shut down the computer please", "could you shut down the computer",
    ])
    def test_these_ask_for_a_shutdown(self, text):
        assert is_shutdown_request(text) is True

    @pytest.mark.parametrize("text", [
        "shut down the heater", "turn off the computer screen", "turn off the computer light", "turn off the monitor", "shut down", "turn off", "turn off the light",
        "shut down the computer and the lights", "ask claude to shut down the computer", "turn on the computer", "restart the computer", "shut down the computers",
        "what happens if I shut down the computer", "do not shut down the computer", "shut up", "", "computer", "pc",
    ])
    def test_nothing_else_does(self, text):
        assert is_shutdown_request(text) is False


class TestTheYes:
    @pytest.mark.parametrize("text", ["yes", "Yes.", "yeah", "yep", "yes please", "confirm", "confirmed", "affirmative", "do it", "go ahead", "yes, shut it down",
                                      "yes shut down the computer now", "hey jarvis yes", "yes sir", "proceed"])
    def test_a_clear_yes(self, text):
        assert is_affirmative(text) is True

    @pytest.mark.parametrize("text", ["", "no", "nope", "okay", "ok", "sure", "maybe", "yes no", "yes cancel", "yes wait", "no don't", "stop", "never mind",
                                      "yes I think the weather is nice", "shut down the computer", "turn it off", "what", "yes but not now", "yes actually no"])
    def test_anything_else_is_not(self, text):
        assert is_affirmative(text) is False


# ------------------------------------------------------------------------------------------------ the conversation
class TestTheConversation:
    @pytest.mark.asyncio
    async def test_the_request_only_asks_it_does_not_shut_down(self, daemon):
        queue, _ = await daemon.route_voice_command("shut down the computer")
        assert ids(queue) == ["system.speak"]
        assert "yes to confirm" in queue[0][0]["text"] and queue[0][0]["request_reply"] is True and queue[0][0]["ignore_silent"] is True
        assert daemon.active_context["type"] == "poweroff_confirm"
        assert daemon._poweroff_authorised is False

    @pytest.mark.asyncio
    async def test_a_yes_sends_a_delayed_shutdown_and_says_how_to_cancel(self, daemon):
        await daemon.route_voice_command("turn off the computer")
        queue, _ = await daemon.route_voice_command("yes")
        assert ids(queue) == ["terminal.shutdown", "system.speak"]
        assert queue[0][0] == {"action": "shutdown", "delay": POWEROFF_DELAY_S, "silent": True}
        assert "Say cancel" in queue[1][0]["text"] and str(POWEROFF_DELAY_S) in queue[1][0]["text"]
        assert daemon._poweroff_authorised is True and daemon.active_context["type"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", ["no", "cancel", "stop", "never mind", "yes no", "okay", "sure", "turn on the light", "what time is it", "hey jarvis", "you"])
    async def test_anything_but_a_clear_yes_cancels_and_shuts_nothing_down(self, daemon, answer):
        await daemon.route_voice_command("shut down the computer")
        queue, _ = await daemon.route_voice_command(answer)
        assert "terminal.shutdown" not in ids(queue) and daemon._poweroff_authorised is False
        assert daemon.active_context["type"] is None

    @pytest.mark.asyncio
    async def test_a_late_yes_does_nothing(self, daemon, mocker):
        clock = [1000.0]
        mocker.patch("clDaemon.time.time", lambda: clock[0])
        await daemon.route_voice_command("shut down the computer")
        clock[0] += POWEROFF_CONFIRM_S + 1
        queue, _ = await daemon.route_voice_command("yes")
        assert "terminal.shutdown" not in ids(queue) and daemon._poweroff_authorised is False

    @pytest.mark.asyncio
    async def test_the_answer_just_in_time_still_counts(self, daemon, mocker):
        clock = [1000.0]
        mocker.patch("clDaemon.time.time", lambda: clock[0])
        await daemon.route_voice_command("shut down the computer")
        clock[0] += POWEROFF_CONFIRM_S - 2
        queue, _ = await daemon.route_voice_command("yes")
        assert "terminal.shutdown" in ids(queue)

    @pytest.mark.asyncio
    async def test_a_yes_with_nothing_asked_does_nothing(self, daemon):
        queue, _ = await daemon.route_voice_command("yes")
        assert "terminal.shutdown" not in ids(queue) and daemon._poweroff_authorised is False

    @pytest.mark.asyncio
    async def test_one_yes_is_one_shutdown(self, daemon):
        await daemon.route_voice_command("shut down the computer")
        await daemon.route_voice_command("yes")
        assert daemon._authorise_dispatch("terminal.shutdown") is True
        assert daemon._authorise_dispatch("terminal.shutdown") is False            # used up
        queue, _ = await daemon.route_voice_command("yes")                         # a second yes asks nothing
        assert "terminal.shutdown" not in ids(queue)

    @pytest.mark.asyncio
    async def test_the_authorisation_does_not_outlive_the_next_command(self, daemon):
        await daemon.route_voice_command("shut down the computer")
        await daemon.route_voice_command("yes")
        assert daemon._poweroff_authorised is True
        await daemon.route_voice_command("turn on the light")                      # the dispatcher never consumed it
        assert daemon._poweroff_authorised is False

    @pytest.mark.asyncio
    async def test_similar_commands_are_not_a_shutdown_request(self, daemon):
        for text in ("shut down the heater", "turn off the computer screen", "turn off the computer light"):
            queue, _ = await daemon.route_voice_command(text)
            assert daemon.active_context["type"] != "poweroff_confirm", text
            assert "terminal.shutdown" not in ids(queue), text

    @pytest.mark.asyncio
    async def test_free_text_being_collected_is_not_taken_for_the_command(self, daemon):
        daemon.active_context = {"type": "todo_add_target", "expires_at": time.time() + 30}
        queue, _ = await daemon.route_voice_command("shut down the computer")
        assert ids(queue) == ["utilities.todo_create"] and queue[0][0]["task"] == "shut down the computer"
        assert daemon.active_context["type"] != "poweroff_confirm"

    @pytest.mark.asyncio
    async def test_a_compound_with_it_never_shuts_down_without_the_yes(self, daemon):
        queue, _ = await daemon.route_voice_command("turn off the light and shut down the computer")
        assert "terminal.shutdown" not in ids(queue)

    @pytest.mark.asyncio
    async def test_a_claude_request_about_it_is_not_a_command(self, daemon):
        queue, _ = await daemon.route_voice_command("ask claude how do I shut down the computer")
        assert ids(queue)[0] == "claude.ask" and daemon.active_context["type"] != "poweroff_confirm"


class TestTheGate:
    def test_a_shutdown_without_the_yes_is_refused(self, daemon):
        assert daemon._authorise_dispatch("terminal.shutdown") is False

    def test_other_actions_are_untouched(self, daemon):
        for action in ("light.set", "system.speak", "terminal.app_volume", "system.abort"):
            assert daemon._authorise_dispatch(action) is True

    def test_the_slm_cannot_produce_it_because_the_grammar_does_not_know_it(self):
        gbnf = open(os.path.join(os.path.dirname(__file__), "..", "config", "grammars", "intent_schema.gbnf"), encoding="utf-8").read()
        assert "terminal.shutdown" not in gbnf and "shutdown" not in gbnf.lower().replace("system.shutdown", "")

    def test_no_intent_maps_to_it(self):
        import json
        intents = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config", "intents.json"), encoding="utf-8"))
        assert not [k for k, v in intents.items() if v["action_id"] in ("terminal.shutdown", "terminal.cancel_shutdown")]


class TestTheRouter:
    def test_the_confirmed_shutdown_reaches_the_terminal_service_with_its_delay(self, daemon):
        topic, payload = daemon.action_router.prepare("terminal.shutdown", action="shutdown", delay=20, silent=True)
        assert topic == "pc/system/control" and payload == {"action": "shutdown", "delay": 20, "silent": True}

    def test_a_cancel_is_a_message_to_the_same_service(self, daemon):
        topic, payload = daemon.action_router.prepare("terminal.cancel_shutdown")
        assert topic == "pc/system/control" and payload["action"] == "cancel_shutdown"

    def test_a_prompt_can_ask_to_be_heard_in_silent_mode(self, daemon):
        _, payload = daemon.action_router.prepare("system.speak", text="Shut down?", request_reply=True, ignore_silent=True)
        assert payload["ignore_silent"] is True and payload["request_reply"] is True


# ------------------------------------------------------------------------------------------------ the service that does it
class FakeTimer:
    instances = []

    def __init__(self, interval, fn):
        self.interval, self.fn, self.cancelled, self.started, self.daemon = interval, fn, False, False, False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


@pytest.fixture
def terminal(mocker):
    import clTerminal
    mocker.patch.object(clTerminal.TerminalManager, "_load_shortcuts")
    FakeTimer.instances = []
    mocker.patch("clTerminal.threading.Timer", FakeTimer)
    popen = mocker.patch("clTerminal.subprocess.Popen")
    m = clTerminal.TerminalManager()
    m.popen = popen
    return m


class TestTheTerminalService:
    def test_a_shutdown_with_a_delay_waits_and_then_shuts_down(self, terminal, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        ok, msg = terminal.execute_command("shutdown", delay=20)
        assert ok and "20 seconds" in msg and terminal.shutdown_pending()
        timer = FakeTimer.instances[-1]
        assert timer.interval == 20 and timer.started and timer.daemon is True
        terminal.popen.assert_not_called()                                         # nothing yet
        timer.fire()
        terminal.popen.assert_called_once_with(["shutdown", "/s", "/t", "0"])
        assert not terminal.shutdown_pending()

    def test_on_linux_it_is_the_linux_command(self, terminal, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        terminal.execute_command("shutdown", delay=5)
        FakeTimer.instances[-1].fire()
        terminal.popen.assert_called_once_with(["shutdown", "now"])

    def test_cancelling_stops_it_for_good(self, terminal):
        terminal.execute_command("shutdown", delay=20)
        ok, msg = terminal.execute_command("cancel_shutdown")
        assert ok and msg == "Shutdown cancelled." and not terminal.shutdown_pending()
        FakeTimer.instances[-1].fire()
        terminal.popen.assert_not_called()

    def test_cancelling_when_nothing_is_pending_says_so(self, terminal):
        ok, msg = terminal.execute_command("cancel_shutdown")
        assert not ok and "No shutdown is pending" in msg

    def test_a_second_request_replaces_the_first_it_does_not_stack(self, terminal):
        terminal.execute_command("shutdown", delay=20)
        first = FakeTimer.instances[-1]
        terminal.execute_command("shutdown", delay=30)
        assert first.cancelled and FakeTimer.instances[-1].interval == 30

    @pytest.mark.parametrize("delay, expected", [(0, None), (-5, 1), (99999, 600), ("12", 12)])
    def test_the_delay_is_kept_in_a_sane_range(self, terminal, delay, expected):
        terminal.execute_command("shutdown", delay=delay)
        if expected is None:
            assert not FakeTimer.instances                                         # no delay: the old immediate behaviour
        else:
            assert FakeTimer.instances[-1].interval == expected

    def test_without_a_delay_it_is_still_the_immediate_shutdown(self, terminal, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        ok, _ = terminal.execute_command("shutdown")
        assert ok
        terminal.popen.assert_called_once_with(["shutdown", "/s", "/t", "0"])

    def test_an_abort_message_cancels_a_pending_shutdown(self, terminal):
        import asyncio
        import json
        import clTerminal

        class Msg:
            def __init__(self, topic, payload=b"{}"):
                self.topic, self.payload = topic, payload

        class Client:
            def __init__(self, messages):
                self.messages, self.published, self.subscribed = messages, [], []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                raise asyncio.CancelledError

            async def subscribe(self, t):
                self.subscribed.append(t)

            async def publish(self, topic, payload):
                self.published.append((topic, json.loads(payload)))

            @property
            def msgs(self):
                return self.messages

        class Stream:
            def __init__(self, items):
                self.items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.items:
                    raise asyncio.CancelledError
                return self.items.pop(0)

        terminal.execute_command("shutdown", delay=20)
        client = Client(Stream([Msg("jarvis/sys/abort")]))
        import unittest.mock as um
        with um.patch("clTerminal.aiomqtt.Client", return_value=client):
            asyncio.run(clTerminal.mqtt_service_listener(terminal))
        assert "jarvis/sys/abort" in client.subscribed and "pc/system/control" in client.subscribed
        assert not terminal.shutdown_pending()
        assert any(t == "jarvis/feedback" and p["message"] == "Shutdown cancelled." and p["silent"] is True for t, p in client.published)

    def test_an_abort_with_nothing_pending_does_nothing(self, terminal):
        import asyncio
        import clTerminal
        import unittest.mock as um

        class Msg:
            topic, payload = "jarvis/sys/abort", b"{}"

        class Client:
            published = []

            def __init__(self):
                self.messages = self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                raise asyncio.CancelledError

            async def subscribe(self, t):
                pass

            async def publish(self, topic, payload):
                self.published.append(topic)

            def __aiter__(self):
                self._items = [Msg()]
                return self

            async def __anext__(self):
                if not self._items:
                    raise asyncio.CancelledError
                return self._items.pop()

        client = Client()
        with um.patch("clTerminal.aiomqtt.Client", return_value=client):
            asyncio.run(clTerminal.mqtt_service_listener(terminal))
        assert "jarvis/feedback" not in client.published
