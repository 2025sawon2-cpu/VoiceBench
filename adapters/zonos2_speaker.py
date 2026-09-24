"""Official ZONOS2 ECAPA encoder/preprocessing, with pinned local HF weights.

Matches Zyphra/ZONOS2 models/speaker_cloning.py (194c0a3). This avoids the
Windows GGML CPU binary's illegal-instruction failure without changing speakers.
"""
from pathlib import Path
import io


class SpeakerEncoder:
    def __init__(self, model_path):
        import torch
        import torchaudio
        from transformers import AutoModel
        torch.set_num_threads(min(torch.get_num_threads(), 4))
        self.torch = torch
        self.torchaudio = torchaudio
        # Repository code was reviewed and is downloaded at an immutable revision.
        self.model = AutoModel.from_pretrained(
            str(model_path), trust_remote_code=True, local_files_only=True,
        ).to("cpu").eval().requires_grad_(False)
        self.mel = torchaudio.transforms.MelSpectrogram(
            sample_rate=24000, n_fft=1024, win_length=1024, hop_length=256,
            f_min=0.0, f_max=12000.0, n_mels=128, power=1.0, center=False,
            norm="slaney", mel_scale="slaney",
        )

    def encode(self, reference: Path) -> bytes:
        import numpy as np
        import soundfile as sf
        torch = self.torch
        wav, sr = sf.read(reference, dtype="float32", always_2d=True)
        with torch.inference_mode():
            wav = torch.from_numpy(wav.mean(axis=1))[None]
            if sr != 24000:
                wav = self.torchaudio.transforms.Resample(sr, 24000)(wav)
            wav = torch.nn.functional.pad(wav.unsqueeze(1), (384, 384), mode="reflect").squeeze(1)
            mel = torch.log(self.mel(wav).clamp(min=1e-5)).transpose(1, 2)
            embedding = self.model(input_values=mel).last_hidden_state.float().numpy()[0]
        if embedding.shape != (2048,) or not np.isfinite(embedding).all():
            raise RuntimeError("Invalid ZONOS2 speaker embedding")
        buffer = io.BytesIO()
        np.save(buffer, embedding.astype("<f4"), allow_pickle=False)
        return buffer.getvalue()
