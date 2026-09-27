"""Create equal-RMS listening copies; never overwrite original WAVs.

RMS matching reduces level bias, but is not perceptual LUFS normalization.
All files receive constant gain, with a shared target lowered if any would clip.
"""
import argparse
import audioop
import hashlib
import json
import math
import wave
from pathlib import Path


def match_volume(sources, destination):
    destination = Path(destination).resolve()
    loaded = []
    if len({Path(p).name for p in sources}) != len(sources):
        raise ValueError('Input filenames must be unique')
    for source in map(Path, sources):
        if source.resolve().parent == destination:
            raise ValueError('Output directory must differ from the originals')
        with wave.open(str(source), 'rb') as wav:
            params, pcm = wav.getparams(), wav.readframes(wav.getnframes())
        if params.sampwidth != 2 or params.comptype != 'NONE' or not pcm:
            raise ValueError('Expected nonempty PCM16 WAV')
        rms, peak = audioop.rms(pcm, 2), audioop.max(pcm, 2)
        if not rms:
            raise ValueError('Cannot match silent audio')
        loaded.append((source, params, pcm, rms, peak))
    target = min([32767 * 10 ** (-26 / 20)] + [0.98 * 32767 * rms / peak for _, _, _, rms, peak in loaded])
    destination.mkdir(parents=True, exist_ok=True)
    report = []
    for source, params, pcm, rms, peak in loaded:
        gain = target / rms
        output = destination / source.name
        adjusted = audioop.mul(pcm, 2, gain)
        with wave.open(str(output), 'wb') as wav:
            wav.setparams(params)
            wav.writeframes(adjusted)
        report.append({'source':str(source.resolve()), 'output':str(output),
                       'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                       'gain_db':20 * math.log10(gain), 'output_rms':audioop.rms(adjusted, 2)})
    (destination / 'volume-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('wav', type=Path, nargs='+')
    args = parser.parse_args()
    print(json.dumps(match_volume(args.wav, args.output_dir), indent=2))
