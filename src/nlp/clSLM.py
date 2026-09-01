import os
import sys
import json
import asyncio
import logging
import urllib.request
from typing import Dict, Any, List, Optional

try:
    from llama_cpp import Llama, LlamaGrammar
    LLAMA_AVAILABLE = True
except ImportError:
    LLAMA_AVAILABLE = False


class SLMInferenceEngine:
    """Asynchronous low-latency cognitive engine with autonomous model fetching."""

    def __init__(self, core_config: Dict[str, Any]):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        slm_cfg = core_config.get("settings", {}).get("slm_settings", {})
        
        self.enabled = slm_cfg.get("enabled", True) and LLAMA_AVAILABLE
        model_rel_path = slm_cfg.get("model_path", "models/jarvis-brain-v2-q4_k_m.gguf")
        self.model_path = os.path.abspath(os.path.join(self.base_dir, "..", "..", model_rel_path))
        
        # HuggingFace direct download link for this specific model
        self.model_url = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
        
        self.n_threads = int(slm_cfg.get("threads", 4))
        self.n_ctx = int(slm_cfg.get("context_size", 1024))
        self.temp = float(slm_cfg.get("temperature", 0.1))
        self.n_gpu_layers = int(slm_cfg.get("gpu_layers", 0))

        self.grammar_path = os.path.abspath(
            os.path.join(self.base_dir, "..", "..", "config", "grammars", "intent_schema.gbnf")
        )

        self.llm: Optional[Any] = None
        self.grammar: Optional[Any] = None

        if self.enabled:
            self._load_engine()

    def _download_progress_hook(self, count, block_size, total_size):
        """Displays a clean terminal progress bar during model download."""
        if total_size == -1: return
        percent = int(count * block_size * 100 / total_size)
        percent = max(0, min(percent, 100))
        bar = ('█' * int(percent / 2)).ljust(50, '-')
        sys.stdout.write(f"\r\033[K[SLM DOWNLOAD] |{bar}| {percent}%")
        sys.stdout.flush()

    def _ensure_model_exists(self) -> bool:
        """Checks if model exists, downloads it if not."""
        if os.path.exists(self.model_path):
            return True

        logging.info(f"[SLM] Model not found locally. Initiating autonomous download (~350MB)...")
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        
        try:
            urllib.request.urlretrieve(self.model_url, self.model_path, reporthook=self._download_progress_hook)
            sys.stdout.write("\n")
            logging.info("[SLM] Download complete! Model saved to local workspace.")
            return True
        except Exception as e:
            sys.stdout.write("\n")
            logging.error(f"[SLM] Autonomous download failed: {e}. SLM fallback disabled.")
            if os.path.exists(self.model_path):
                os.remove(self.model_path) # Clean up corrupted partial downloads
            return False

    def _load_engine(self) -> None:
        if not self._ensure_model_exists():
            self.enabled = False
            return

        try:
            logging.info(f"[SLM] Initializing GGUF engine ({os.path.basename(self.model_path)}) on {self.n_threads} threads...")
            self.llm = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                n_gpu_layers=self.n_gpu_layers,
                verbose=False
            )
            if os.path.exists(self.grammar_path):
                self.grammar = LlamaGrammar.from_file(self.grammar_path)
                logging.info("[SLM] Schema GBNF grammar loaded.")
            else:
                logging.warning("[SLM] GBNF Grammar file missing. Running unconstrained.")
            logging.info("[SLM] Cognitive engine ready.")
        except Exception as e:
            logging.error(f"[SLM] Failed to initialize llama_cpp engine: {e}")
            self.enabled = False

    def _format_prompt(self, user_prompt: str, system_snapshot: str, history: List[Dict[str, str]] = None) -> str:
        prompt = f"<|im_start|>system\n[STATE]: {system_snapshot}<|im_end|>\n"
        if history:
            for turn in history:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"
        prompt += f"<|im_start|>user\n{user_prompt}<|im_end|>\n<|im_start|>assistant\n"
        return prompt

    def _infer_sync(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.llm:
            return None
        try:
            output = self.llm(
                prompt,
                max_tokens=256,
                stop=["<|im_end|>", "\n\n\n"],
                grammar=self.grammar,
                temperature=self.temp
            )
            raw_text = output["choices"][0]["text"].strip()
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            logging.error(f"[SLM] JSON parsing error on text '{raw_text}': {e}")
            return None
        except Exception as e:
            logging.error(f"[SLM] Inference exception: {e}")
            return None

    async def parse_intent_async(
        self, user_prompt: str, system_snapshot: str, history: List[Dict[str, str]]
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        prompt = self._format_prompt(user_prompt, system_snapshot, history)
        return await asyncio.to_thread(self._infer_sync, prompt)