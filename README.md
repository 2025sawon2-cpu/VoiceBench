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

GTX 1070 Ti용으로 PyTorch 2.6.0/CUDA 12.6과 eager attention을 사용하는 격리 환경을 설치하고 Qwen3-TTS 0.6B 및 1.7B 엔진들을 등록합니다. FlashAttention은 Pascal GPU에서 지원되지 않으므로 설치하지 않습니다. 생산 기본값은 1.7B FP16/eager 표현형 프리셋이며, 0.6B 어댑터는 compute capability 6.x에서 FP32 안전 경로를 사용합니다.

```powershell
.\install-qwen3-tts.cmd -DownloadModel
```

`-DownloadModel`은 생산 Qwen3-TTS 1.7B와 Whisper Base 품질 검사 모델을 미리 다운로드합니다. 생략하면 Qwen 모델은 첫 합성 때 자동 다운로드되며, Whisper Base가 없을 때는 품질 검사가 파형 검사만으로 축소됩니다.

### 현재 PC 실측

GTX 1070 Ti 8GB에서 공식 레퍼런스와 짧은 한국어 문장으로 전체 작업 큐를 검증했습니다.

- 출력: 24kHz, mono, PCM 16비트, 3.68초
- 전체 생성 시간: 46.18초
- 피크 VRAM: 6,909MB
- GPU 사용률 피크: 100%
- 클리핑: 0%, 자동 hard reject 통과

## 데이터 구조

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

## 다음 Gate

1. Qwen3-TTS 0.6B FP16/eager 어댑터와 짧은 smoke test
2. GPT-SoVITS v2ProPlus 어댑터
3. CosyVoice3 0.5B 비교
4. MOSS-TTS v1.5 8B Q4의 8GB VRAM 실행 가능성 검증
5. Gate 0 전체에서 음질·화자 유사도·OOM·VRAM·RAM·생성 시간을 기록해 최종 엔진 선정
