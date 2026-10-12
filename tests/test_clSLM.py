import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from nlp.clSLM import ensure_gguf_exists


class TestEnsureGgufExists:
    """model_url is a project-specific fine-tune's download link, configured
    per-model in core.json -- there's no hardcoded fallback URL to guess."""

    def test_returns_true_when_file_already_present(self, tmp_path):
        model_path = tmp_path / "model.gguf"
        model_path.write_bytes(b"fake weights")

        with patch("nlp.clSLM.urllib.request.urlretrieve") as mock_dl:
            assert ensure_gguf_exists(str(model_path), "", "TEST") is True
            mock_dl.assert_not_called()

    def test_missing_file_and_no_url_fails_without_crashing(self, tmp_path):
        model_path = tmp_path / "missing.gguf"
        assert ensure_gguf_exists(str(model_path), "", "TEST") is False
        assert not model_path.exists()

    def test_downloads_when_url_is_configured(self, tmp_path):
        model_path = tmp_path / "sub" / "model.gguf"

        def fake_download(url, dest, reporthook=None):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(b"downloaded weights")

        with patch("nlp.clSLM.urllib.request.urlretrieve", side_effect=fake_download) as mock_dl:
            result = ensure_gguf_exists(str(model_path), "https://example.com/model.gguf", "TEST")

        assert result is True
        assert model_path.exists()
        mock_dl.assert_called_once()

    def test_cleans_up_partial_file_on_download_failure(self, tmp_path):
        model_path = tmp_path / "model.gguf"

        def failing_download(url, dest, reporthook=None):
            # Simulate a partial write before the connection drops.
            with open(dest, "wb") as f:
                f.write(b"partial")
            raise ConnectionError("dropped")

        with patch("nlp.clSLM.urllib.request.urlretrieve", side_effect=failing_download):
            result = ensure_gguf_exists(str(model_path), "https://example.com/model.gguf", "TEST")

        assert result is False
        assert not model_path.exists()


class FakeLlm:
    """Records each call's grammar and returns scripted completions."""
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def __call__(self, prompt, max_tokens, stop, grammar, temperature):
        self.calls.append(grammar)
        return {"choices": [{"text": self.texts[len(self.calls) - 1]}]}


def make_engine(llm, fast_decode=True):
    from nlp.clSLM import SLMInferenceEngine
    eng = SLMInferenceEngine({"settings": {"slm_settings": {"enabled": False, "fast_decode": fast_decode}}})
    eng.enabled, eng.llm, eng.grammar = True, llm, "GRAMMAR"
    return eng


VALID = '{"actions": [{"action_id": "spotify.control", "action": "pause"}]}'


class TestFastDecode:
    """Grammar-constrained decoding is ~4x slower per token, so the model decodes freely and the grammar
    is only the fallback for output that isn't exactly what it would have allowed."""

    def test_valid_free_output_is_used_without_the_grammar(self):
        llm = FakeLlm(VALID)
        eng = make_engine(llm)

        assert eng._infer_sync("p") == {"actions": [{"action_id": "spotify.control", "action": "pause"}]}
        assert llm.calls == [None]
        assert (eng.fast_hits, eng.fast_fallbacks) == (1, 0)

    def test_invalid_output_is_redone_under_the_grammar(self):
        llm = FakeLlm('{"actions": [{"action_id": "spotify.control", "action": "post"}]}', VALID)
        eng = make_engine(llm)

        assert eng._infer_sync("p") == {"actions": [{"action_id": "spotify.control", "action": "pause"}]}
        assert llm.calls == [None, "GRAMMAR"]
        assert (eng.fast_hits, eng.fast_fallbacks) == (0, 1)

    def test_prose_instead_of_json_falls_back(self):
        llm = FakeLlm("Sure, pausing the music.", VALID)
        eng = make_engine(llm)
        assert eng._infer_sync("p")["actions"][0]["action"] == "pause"
        assert llm.calls == [None, "GRAMMAR"]

    def test_a_crash_in_the_free_pass_falls_back(self):
        class Boom(FakeLlm):
            def __call__(self, prompt, max_tokens, stop, grammar, temperature):
                if grammar is None:
                    self.calls.append(grammar)
                    raise RuntimeError("cuda hiccup")
                return super().__call__(prompt, max_tokens, stop, grammar, temperature)
        llm = Boom("unused", VALID)
        llm.calls = []
        eng = make_engine(llm)
        assert eng._infer_sync("p") is not None

    def test_can_be_switched_off(self):
        llm = FakeLlm(VALID)
        eng = make_engine(llm, fast_decode=False)

        eng._infer_sync("p")

        assert llm.calls == ["GRAMMAR"] and eng.fast_decode is False

    def test_without_a_readable_grammar_it_stays_on_the_old_path(self):
        from nlp.clSLM import SLMInferenceEngine
        with patch("nlp.clSLM.IntentSchema.from_gbnf", return_value=None):
            eng = SLMInferenceEngine({"settings": {"slm_settings": {"enabled": False}}})
        assert eng.fast_decode is False

        eng.enabled, eng.llm, eng.grammar = True, FakeLlm(VALID), "GRAMMAR"
        eng._infer_sync("p")
        assert eng.llm.calls == ["GRAMMAR"]

    def test_the_constrained_path_still_reports_bad_json(self):
        eng = make_engine(FakeLlm("nope", "still not json"))
        assert eng._infer_sync("p") is None

    def test_no_model_means_no_result(self):
        eng = make_engine(None)
        assert eng._infer_sync("p") is None
