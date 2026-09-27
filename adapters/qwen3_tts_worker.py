from __future__ import annotations

import argparse
import hashlib
import json
import random
import threading
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
LANGUAGES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "auto": "Auto",
}


def continuation_prompt(model, anchor_prompt, audio: str, text: str, mode: str = "replace"):
    """Experimental acoustic context with the owner's original speaker embedding.

    The caller must verify the complete previous audio/transcript pair. No text
    overlap or approximate waveform cropping is involved. Never mutate the
    cached anchor or carry a newly estimated speaker identity into the next turn.
    """
    if len(anchor_prompt) != 1 or not text.strip():
        raise ValueError("A single anchor and a complete continuation transcript are required")
    if mode not in {"replace", "anchored"}:
        raise ValueError("Unsupported continuation mode")
    previous = model.create_voice_clone_prompt(
        ref_audio=audio, ref_text=text, x_vector_only_mode=False
    )
    anchor = anchor_prompt[0]
    if mode == "anchored":
        import torch

        # Retain the real recording's acoustic codes as well as its identity.
        # Both transcripts describe complete audio sequences in the same order.
        # Qwen consumes the context; it is not added to the spoken target text.
        if anchor.ref_code is None or not anchor.ref_text:
            raise ValueError("Anchored context requires full-reference audio and text")
        return [replace(
            previous[0], ref_spk_embedding=anchor.ref_spk_embedding,
            ref_code=torch.cat([anchor.ref_code, previous[0].ref_code], dim=0),
            ref_text=f"{anchor.ref_text.rstrip()} {text.strip()}",
        )]
    return [replace(previous[0], ref_spk_embedding=anchor.ref_spk_embedding)]


class QwenWorkerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], model_name: str, dtype_name: str, attention: str
    ):
        import torch
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from qwen_tts import Qwen3TTSModel

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the Qwen3-TTS worker environment")
        capability = torch.cuda.get_device_capability(0)
        if dtype_name == "auto":
            dtype_name = "fp32" if capability[0] < 7 else "fp16"
        self.torch = torch
        self.dtype_name = dtype_name
        self.attention = attention
        self.model_name = model_name
        self.device_name = torch.cuda.get_device_name(0)
        dtype = torch.float32 if dtype_name == "fp32" else torch.float16
        print(
            f"Loading {model_name} on {self.device_name} "
            f"(CC {capability[0]}.{capability[1]}, {dtype_name.upper()}/{attention})",
            flush=True,
        )
        model_source = model_name
        if not Path(model_name).is_dir():
            try:
                # Passing a cached directory also prevents the tokenizer's
                # unrelated model_info lookup when operating offline.
                model_source = snapshot_download(model_name, local_files_only=True)
            except LocalEntryNotFoundError:
                pass  # Retain the existing first-run download behavior.
        self.model = Qwen3TTSModel.from_pretrained(
            model_source,
            device_map="cuda:0",
            dtype=dtype,
            attn_implementation=attention,
            low_cpu_mem_usage=True,
        )
        self.prompt_cache: dict[tuple[str, str, str], Any] = {}
        self.generation_lock = threading.Lock()
        super().__init__(address, QwenWorkerHandler)

    def synthesize(self, payload: dict[str, Any]) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf

        text = str(payload.get("text", "")).strip()
        reference = str(payload.get("reference", "")).strip()
        reference_text = str(payload.get("reference_text", "")).strip()
        output = Path(str(payload.get("output", ""))).resolve()
        language = LANGUAGES.get(str(payload.get("language", "ko")).lower(), "Korean")
        seed = int(payload.get("seed", 13))
        supplied_generation = payload.get("generation") or {}
        if not isinstance(supplied_generation, dict):
            raise ValueError("Unsupported generation parameters")
        generation = dict(supplied_generation)
        speaker = generation.pop("speaker", None)
        instruct = generation.pop("instruct", "")
        custom_voice = "CustomVoice" in getattr(self, "model_name", "")
        if custom_voice:
            supported = {v.lower() for v in self.model.get_supported_speakers()}
            if not isinstance(speaker, str) or speaker.lower() not in supported:
                raise ValueError("Unsupported built-in speaker")
            if not isinstance(instruct, str) or len(instruct) > 300:
                raise ValueError("Invalid voice instruction")
        elif speaker or instruct:
            raise ValueError("Built-in speakers require a CustomVoice model")
        allowed_generation = {
            "do_sample",
            "top_k",
            "top_p",
            "temperature",
            "repetition_penalty",
            "subtalker_dosample",
            "subtalker_top_k",
            "subtalker_top_p",
            "subtalker_temperature",
            "non_streaming_mode",
        }
        if not isinstance(generation, dict) or set(generation) - allowed_generation:
            raise ValueError("Unsupported generation parameters")
        if "non_streaming_mode" in generation and not isinstance(generation["non_streaming_mode"], bool):
            raise ValueError("non_streaming_mode must be a boolean")
        if not text or (not custom_voice and (not reference or not reference_text)):
            raise ValueError("text, reference, and reference_text are required")

        with self.generation_lock:
            context = None
            if not custom_voice:
                reference_path = Path(reference).resolve()
                cache_key = (
                    str(reference_path), reference_text,
                    hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                )
                prompt = self.prompt_cache.get(cache_key)
                if prompt is None:
                    prompt = self.model.create_voice_clone_prompt(
                        ref_audio=cache_key[0],
                        ref_text=reference_text,
                        x_vector_only_mode=False,
                    )
                    if len(self.prompt_cache) >= 4:
                        self.prompt_cache.pop(next(iter(self.prompt_cache)))
                    self.prompt_cache[cache_key] = prompt
                context = payload.get("continuation")
                if context is not None:
                    # Only the trusted runner and local comparison tools supply
                    # this context. External caller payloads never forward it.
                    info = sf.info(context["audio"])
                    if not 3 <= info.duration <= 20:
                        raise ValueError("Continuation audio must be a complete 3–20 second segment")
                    prompt = continuation_prompt(
                        self.model, prompt, context["audio"], context["text"],
                        mode=context.get("mode", "replace"),
                    )
            # Prompt cache misses must not change the sampling RNG state.
            random.seed(seed)
            np.random.seed(seed)
            self.torch.manual_seed(seed)
            self.torch.cuda.manual_seed_all(seed)
            with self.torch.inference_mode():
                if custom_voice:
                    wavs, sample_rate = self.model.generate_custom_voice(
                        text=text, language=language, speaker=speaker, instruct=instruct, **generation,
                    )
                else:
                    wavs, sample_rate = self.model.generate_voice_clone(
                        text=text,
                        language=language,
                        voice_clone_prompt=prompt,
                        **generation,
                    )
            output.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output, wavs[0], sample_rate, subtype="PCM_16")
        return {
            "output": str(output),
            "sample_rate": sample_rate,
            "prompt_cache_entries": len(self.prompt_cache),
            "generation": {**generation, **({"speaker":speaker,"instruct":instruct} if custom_voice else {})},
            "continuation_used": context is not None,
        }


class QwenWorkerHandler(BaseHTTPRequestHandler):
    server: QwenWorkerServer

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[Qwen worker] {format % args}", flush=True)

    def do_GET(self) -> None:
        if self.path != "/health":
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        self.send_json(
            {
                "status": "ready",
                "runtime_version": 3,
                "model": self.server.model_name,
                "dtype": self.server.dtype_name,
                "attention": self.server.attention,
                "gpu": self.server.device_name,
                "prompt_cache_entries": len(self.server.prompt_cache),
            }
        )

    def do_POST(self) -> None:
        try:
            if self.path == "/synthesize":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("Invalid request body size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                return self.send_json(self.server.synthesize(payload))
            if self.path == "/shutdown":
                self.send_json({"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.send_json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent local Qwen3-TTS worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dtype", choices=("auto", "fp16", "fp32"), default="auto")
    parser.add_argument("--attention", choices=("eager", "sdpa"), default="eager")
    args = parser.parse_args()
    server = QwenWorkerServer((args.host, args.port), args.model, args.dtype, args.attention)
    print(f"Qwen3-TTS worker ready at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
