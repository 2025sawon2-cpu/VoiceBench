# VoiceBench

VoiceBench는 한 대의 로컬 PC에서 보이스 클로닝 TTS 엔진들을 같은 조건으로 비교하기 위한 벤치마크 하네스입니다. 모든 음성, 평가 결과, 메타데이터는 로컬에만 저장하며 외부 API를 호출하지 않습니다.

이 프로젝트는 Pinokio 및 Pinokio 번들 런타임을 사용하지 않습니다. 하네스는 시스템에 설치된 Python으로 저장소 내부의 독립 가상환경을 만들고, 실제 TTS 엔진도 엔진별 독립 환경에 연결하는 구조입니다.

## 내 목소리 TTS Studio

선택된 9.95초 레퍼런스를 사용해 사용자가 입력한 대본을 내 목소리로 생성합니다.

```powershell
.\start.cmd
```

브라우저의 첫 화면에서 대본을 입력하고 `음성 만들기`를 누릅니다. 별도의 글자 수 제한은 없으며, 긴 대본은 약 180자의 문장 중심 의미 단위로 나누어 생성한 뒤 문장부호와 문단에 맞는 호흡을 넣어 WAV 하나로 자동 결합합니다. 완성된 결과는 화면에서 재생하거나 WAV로 저장할 수 있습니다.

Qwen 모델은 첫 요청에서 한 번만 GPU에 올리고 서버가 실행되는 동안 재사용합니다. 현재 PC 실측으로 첫 조각은 모델 준비를 포함해 약 75초, 같은 요청의 다음 조각은 약 18~25초였습니다. 실행 창을 닫으면 워커가 종료되고 GPU 메모리가 반환됩니다.

Studio 결과는 다음 위치에 저장됩니다.

```text
data/artifacts/studio/request-<번호>.wav
```

## 현재 구현 범위

- 프로젝트와 레퍼런스 음성 관리
- 한국어 Gate 0 평가 대본 20개 및 사용자 대본
- SQLite WAL 기반 작업 큐
- 실제 엔진을 별도 프로세스로 연결하는 command adapter
- 결과 WAV의 해시, 엔진 버전·설정, seed, 대본, 시스템 정보 기록
- WAV 길이·피크·무음·클리핑 자동 검사와 hard reject
- GPU 메모리·사용률·온도 및 생성 시간 기록
- 엔진 이름을 숨긴 블라인드 A/B 비교와 승률 집계
- 전체 흐름을 검증하는 결정론적 Mock 엔진 2개

Mock 엔진은 하네스 자체를 시험하는 톤 생성기이며 실제 TTS가 아닙니다.

## 요구 사항

- Windows 10/11 64비트
- Python 3.10 64비트와 Python Launcher(`py.exe`)
- 실제 GPU 엔진 사용 시 NVIDIA 드라이버

현재 PC에는 독립 Python 3.10이 확인되어 있으므로 추가 시스템 설치 없이 실행할 수 있습니다.

## 새 Windows PC 설치

저장소를 복제한 새 PC에서는 다음 명령으로 애플리케이션, Qwen3-TTS 엔진, 생산 모델과 품질 검사 모델을 설치할 수 있습니다.

```powershell
git clone <저장소-URL> VoiceBench
cd VoiceBench
.\setup-windows.cmd -DownloadModels
```

개인 레퍼런스 음성은 Git에 포함되지 않습니다. 필요한 파일 복사, LAN 방화벽, MCP/API 설정을 포함한 전체 절차는 [Windows 새 PC 설치 가이드](docs/windows-install.md)를 참고하세요.

## 기존 PC 최초 실행

PowerShell에서 다음 명령을 실행합니다.

```powershell
.\bootstrap.cmd
.\start.cmd
```

`start.cmd`만 실행해도 `.venv`가 없으면 자동으로 부트스트랩합니다. 이 PC처럼 PowerShell 스크립트 실행 정책이 제한된 환경에서도 시스템 정책을 변경하지 않고 작동합니다. 브라우저를 자동으로 열지 않으려면 다음과 같이 실행합니다.

```powershell
.\start.cmd -NoBrowser
```

기본 주소는 `http://127.0.0.1:8765`입니다.

## 사용 순서

1. 새 프로젝트를 만듭니다.
2. 깨끗한 레퍼런스 음성을 업로드합니다.
3. 레퍼런스에서 실제로 말한 문장을 정확히 입력합니다.
4. 기본 한국어 대본 20개를 추가합니다.
5. 먼저 Mock A/B로 작업 큐와 평가 화면을 확인합니다.
6. 실제 TTS 엔진을 연결한 뒤 동일한 seed와 대본으로 생성합니다.
7. hard reject를 통과한 결과만 블라인드 비교합니다.

## 실제 엔진 연결 규약

실제 엔진은 PCM 16비트 WAV 하나를 출력하는 외부 명령으로 연결합니다. 각 엔진은 전용 Python/Conda 환경과 얇은 wrapper를 사용하며, VoiceBench에는 다음 형식으로 등록합니다.

```json
{
  "command": [
    "C:\\path\\to\\engine-python.exe",
    "C:\\path\\to\\adapter.py",
    "--text", "{text}",
    "--reference", "{reference}",
    "--output", "{output}",
    "--seed", "{seed}",
    "--language", "{language}"
  ],
  "timeout_seconds": 7200
}
```

명령은 셸을 통하지 않고 argv 배열 그대로 실행합니다. 지원 placeholder는 `{text}`, `{reference}`, `{reference_text}`, `{output}`, `{seed}`, `{language}`입니다.

## Qwen3-TTS 설치

PyTorch 2.6.0/CUDA 12.6 격리 환경을 설치하고 Qwen3-TTS 0.6B 및 1.7B 엔진들을 등록합니다. 생산 기본값은 1.7B FP16/SDPA 표현형 프리셋입니다. 0.6B 비교 엔진은 기존 eager attention을 유지하며, compute capability 6.x에서 FP32 안전 경로를 사용합니다. 별도 FlashAttention 2 패키지는 설치하지 않습니다.

```powershell
.\install-qwen3-tts.cmd -DownloadModel
```

`-DownloadModel`은 생산 Qwen3-TTS 1.7B와 Whisper Base 품질 검사 모델을 미리 다운로드합니다. 생략하면 Qwen 모델은 첫 합성 때 자동 다운로드되며, Whisper Base가 없을 때는 품질 검사가 파형 검사만으로 축소됩니다.

### Chatterbox Multilingual V3 실험 엔진

한국어 제로샷 보이스 클로닝을 음색 비교용으로 선택할 수 있습니다. 기본 엔진은 계속 Qwen3-TTS 1.7B입니다. Chatterbox는 별도 가상환경을 사용하며, V3를 포함하는 공식 Git 커밋을 설치해야 합니다. PyPI의 `chatterbox-tts 0.1.7` 단독 설치에는 V3 선택 코드가 없습니다.

```powershell
.\.venv\Scripts\python.exe -m venv .runtime\engines\chatterbox-v3\venv
.\.runtime\engines\chatterbox-v3\venv\Scripts\python.exe -m pip install --upgrade pip
.\.runtime\engines\chatterbox-v3\venv\Scripts\python.exe -m pip install torch==2.6.0+cu126 torchaudio==2.6.0+cu126 --extra-index-url https://download.pytorch.org/whl/cu126
.\.runtime\engines\chatterbox-v3\venv\Scripts\python.exe -m pip install git+https://github.com/resemble-ai/chatterbox.git@5de7a54aa4e5e2baadb0182dde554908b48b85c2
.\.venv\Scripts\python.exe tools\register_chatterbox_v3.py
```

첫 합성 시 선택된 V3 가중치 약 3GB를 `.runtime/cache/huggingface`에 받습니다. Studio에서 `Chatterbox Multilingual V3 · 실험용 음색 비교`를 선택해 Qwen 결과와 들어 비교하세요. 동일 한국어 샘플의 로컬 Whisper 문자 오류율은 Qwen보다 높았으므로 기본 모델로 승격하지 않았습니다.

### VoxCPM2 실험 엔진

```powershell
.\install-voxcpm2.cmd
```

전용 `.runtime/engines/voxcpm2/venv`에 VoxCPM 2.0.3과 PyTorch 2.6.0/CUDA 12.6을 설치하고, 고정된 모델 리비전을 약 5GB 다운로드한 뒤 엔진을 등록합니다. Studio와 벤치마크에서 `VoxCPM2 2B · 실험용 음성 복제`를 선택하세요. 기본 모델과 외부 API는 기존 Qwen 설정을 유지합니다.

참조 WAV와 정확한 대본을 함께 사용하는 복제 방식입니다. 공식 기본값인 CFG 2.0, 추론 10단계와 BF16/SDPA를 사용하며 48kHz PCM WAV를 출력합니다. Windows 기본 경로에서는 `torch.compile`을 끕니다. 참조 음성 정제나 텍스트 정규화는 추가로 적용하지 않습니다.

Qwen과 같은 로컬 작업 포트(8790)를 공유하므로 두 모델 사이를 전환할 때 이전 모델을 종료합니다. 같은 모델의 다음 작업은 이미 로딩한 프로세스를 재사용합니다. 최초 작업의 전체 시간에는 로딩 비용이 포함되며, 결과 manifest의 `adapter.worker`에는 모델 로딩 시간, 합성 시간, RTF, 최대 GPU 할당 메모리를 별도로 기록합니다. 합성 시간은 참조 음성 인코딩을 포함합니다.

VoxCPM2는 실험용으로 파형 검사만 적용합니다. 모델 내부 자동 재시도와 Qwen 전용 Whisper 자동 재생성은 적용하지 않아 요청한 Seed를 유지합니다. 장문은 기존 VoiceBench 분할·결합 경로를 사용하지만, 긴 대본의 음색 연속성은 별도 평가가 필요합니다. 다운로드 이후 합성은 오프라인으로 실행됩니다.

사용자 청취 결과에 따라 쇼츠 감정 프리셋 두 개는 비활성화했습니다. VoxCPM2는 감정·속도·강조 지시를 추가하지 않는 기본 복제 설정만 선택할 수 있습니다. 재설치해도 감정 프리셋을 다시 활성화하지 않으며, 이전 비교 결과와 음성 파일은 보존합니다.

### 이전 GTX 1070 Ti 실측

GTX 1070 Ti 8GB에서 공식 레퍼런스와 짧은 한국어 문장으로 전체 작업 큐를 검증했습니다.

- 출력: 24kHz, mono, PCM 16비트, 3.68초
- 전체 생성 시간: 46.18초
- 피크 VRAM: 6,909MB
- GPU 사용률 피크: 100%
- 클리핑: 0%, 자동 hard reject 통과

## Fish S2-Pro / ZONOS2 비교 엔진

Windows에서 `powershell -ExecutionPolicy Bypass -File .\install-fish-zonos.ps1`로
두 선택형 엔진을 설치합니다. 한 모델만 설치할 때는 `-Engine fish-speech` 또는
`-Engine zonos2`를 사용합니다. 기존 서버는 설치 후 재시작하면 Studio 목록이 갱신됩니다.
실행 중인 작업이 없는지 먼저 확인하세요.

- Fish: 원본 BF16 가중치, 4096토큰 캐시, 모델/코덱 순차 GPU 사용, 감정 태그 없음.
- ZONOS2: 공식 Q8_0 GGUF와 로컬 CUDA 빌드, 정확한 화자 복제 모드, 감정 제어 꺼짐.
- VoxCPM2: 기존 10단계와 추가 20단계 비교 프리셋. 단계 증가가 청감 개선을 보장하지 않습니다.
- 모두 같은 8790 작업자 포트를 사용해 모델을 교체하며 GPU 작업은 직렬 실행합니다.
- 운영 Qwen 기본값과 외부 API의 고정 엔진/Seed는 유지합니다.

Built with Fish Audio. Fish S2-Pro는 연구·비상업 평가용입니다.
상업적 사용은 [Fish Audio의 별도 라이선스](https://github.com/fishaudio/fish-speech/blob/main/LICENSE)가 필요합니다.
설정 및 검증 범위는 [네 모델 비교 기록](docs/four-model-comparison.md)을 참고하세요.
ZONOS2 설치는 VoiceBench 내부에 MSVC/SDK/CUDA 도구를 준비하고 CUDA 서버를 빌드하므로
시간이 걸립니다. 기본 CUDA 아키텍처는 이 PC의 RTX 4070 Ti SUPER에 맞춘 SM89입니다.

## 데이터 저장

```text
data/
  voicebench.sqlite3
  artifacts/
    references/
    outputs/
```

`data/`, `.venv/`, `.runtime/`은 Git에 포함하지 않습니다. 실제 서비스 단계에서도 평가·작업 실행기와 저장 구조를 그대로 재사용할 수 있습니다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 자연스러움 개선과 비교

생산 기본은 Qwen 1.7B 표현형·Seed 13·long-form HQ입니다. 실제 분할 결과가 한 구간이면
사용자가 스토리·설명형·대화형 비교에서 선호한 A의 전체 대본 입력 방식으로 생성합니다. 여러 구간인 대본은 기존
장문 생성 방식을 사용합니다. 이는 30초 기준의 분할 규칙이 아닙니다. [품질 점검과 비교 도구 안내](docs/voice-quality.md)에
확인한 문제, 적용한 수정, 로컬 음성 비교 방법, 다음 개선 우선순위를 정리했습니다.

Studio에서 `Qwen3-TTS 1.7B · 문맥 연결 비교 (실험·HQ)`를 선택하면 원본 목소리 정보를
유지하면서 검증된 앞 구간의 말투를 참조합니다. 연결 검증에 실패하면 같은 Seed의 원본
참조로 다시 생성합니다. 기존 설치에는 `.\.venv\Scripts\python.exe tools\register_continuity.py`로
비교 프리셋을 추가할 수 있습니다. 이 문맥 연결 프리셋은 선택 사항이며 외부 API/MCP의
기본 엔진은 표현형입니다. 단일 생성 개선과 청취 결과는 [쇼츠 품질 기록](docs/shorts-quality-20260925.md)에 정리했습니다.
