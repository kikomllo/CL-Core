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
