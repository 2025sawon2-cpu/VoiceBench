# Windows 새 PC 설치 가이드

> 2026-09-26: 운영 기본 모델은 ZONOS2입니다. 아래 Qwen 설치는 비교 엔진 및 Whisper 검사 환경에도 사용됩니다. ZONOS2는 추가로 `powershell -ExecutionPolicy Bypass -File .\install-fish-zonos.ps1 -Engine zonos2`를 실행해 설치·등록하세요. 기본 레퍼런스는 기존 본인 목소리입니다.

이 문서는 새 Windows PC에서 저장소를 복제하고 VoiceBench의 Qwen3-TTS 1.7B 음성 복제 서비스를 설치하는 절차입니다. 모델, 생성 음성, 개인 레퍼런스 음성, 웹 비밀번호, 외부 API 키는 Git에 포함되지 않습니다.

Hotpost·대본 작업실·auto_capcut까지 같은 PC에 설치할 때는 [네 저장소 통합 설치 순서](https://github.com/dkfdnd/insta_auto/blob/main/docs/windows-install.md)를 먼저 확인하세요. 각 프로젝트의 Python 환경은 서로 공유하지 않습니다. 기존 PC에서 선택한 엔진과 목소리도 로컬 DB·모델 설정에 있으므로 Git 복제만으로 새 PC에 선택되지 않습니다.

## 1. 권장 환경

- Windows 10/11 64비트
- NVIDIA GPU와 최신 NVIDIA 드라이버
- VRAM 8GB 이상 권장
- 시스템 Python 3.10 64비트와 Python Launcher(`py.exe`)
- Git for Windows
- 저장소와 모델 캐시를 위한 여유 공간 12GB 이상

현재 생산 설정은 GTX 1070 Ti 8GB에서 검증된 PyTorch 2.6.0, CUDA 12.6, FP16/eager 조합입니다. 다른 NVIDIA GPU에서도 설치할 수 있지만 성능과 최대 VRAM 사용량은 달라질 수 있습니다.

설치 전 PowerShell에서 다음 명령이 동작하는지 확인합니다.

```powershell
git --version
py -3.10 --version
nvidia-smi
```

## 2. 저장소 복제

```powershell
git clone <저장소-URL> VoiceBench
cd VoiceBench
```

저장소 경로에는 가능하면 한글이나 특수문자가 없는 짧은 경로를 사용합니다. 예: `C:\VoiceBench`.

## 3. 자동 설치

모델까지 미리 받으려면 다음 명령을 실행합니다.

```powershell
.\setup-windows.cmd -DownloadModels
```

이 명령은 다음 작업을 수행합니다.

1. `.venv`에 VoiceBench용 Python 환경 생성
2. `.runtime\engines\qwen3-tts\venv`에 독립된 TTS 환경 생성
3. PyTorch 2.6.0 CUDA 12.6 및 `qwen-tts==0.1.1` 설치
4. Qwen3-TTS 1.7B 표현형 기본 엔진과 비교용 엔진 등록
5. `-DownloadModels` 사용 시 Qwen3-TTS 1.7B 및 Whisper Base 다운로드
6. 전체 회귀 테스트 실행

모델 다운로드를 생략하면 설치는 빨리 끝나지만 첫 음성 생성 시 Qwen 모델을 자동으로 내려받습니다. Whisper Base가 없으면 서비스는 실행되지만 자동 발음 검사가 파형 검사만으로 축소됩니다.

이 명령은 **Qwen 환경**을 설치합니다. 현재 다른 PC에서 쓰는 ZONOS2나 Hotpost의 공식 남녀 목소리는 별도 준비가 필요합니다. Qwen CustomVoice와 ZONOS2 공식 목소리의 PC별 절차는 [Hotpost 목소리 가이드](hotpost-voices.md)를 따르세요. ZONOS2의 기본 설치는 Q8 가중치와 CUDA `SM89` 빌드를 전제로 합니다. GPU가 다르거나 VRAM이 8GB라면 이 기본값을 그대로 사용 가능하다고 가정하지 마세요. 기존 GTX 1070 Ti 8GB PC는 별도 Q4_K 가중치와 `SM61` 빌드로 운영했습니다. 새 PC의 GPU·메모리에 맞는 가중치와 SM 번호를 정한 뒤 설치·합성 시험을 해야 합니다.

## 4. 개인 레퍼런스 음성 설치

Git 저장소에는 개인 음성을 넣지 않습니다. 기존 PC에서 다음 두 파일을 안전한 방법으로 새 PC에 복사합니다.

```text
selected_reference.wav
selected_reference.txt
```

텍스트 파일에는 WAV에서 실제로 말한 문장을 UTF-8로 정확히 기록해야 합니다. 현재 선택된 레퍼런스는 약 9.95초 길이의 24kHz mono PCM WAV입니다.

설치 명령에서 파일을 바로 지정할 수 있습니다.

```powershell
.\setup-windows.cmd -DownloadModels `
  -ReferenceWav "D:\VoiceBackup\selected_reference.wav" `
  -ReferenceText "D:\VoiceBackup\selected_reference.txt"
```

이미 엔진 설치를 마쳤다면 아래 위치에 직접 복사해도 됩니다.

```text
data\prepared\ref_voice\selected_reference.wav
data\prepared\ref_voice\selected_reference.txt
```

`data\`는 `.gitignore`에 포함되어 Git에 업로드되지 않습니다.

## 5. 로컬 실행

```powershell
.\start.cmd
```

브라우저에서 `http://127.0.0.1:8765`를 엽니다. 첫 요청은 모델을 GPU에 올리기 때문에 이후 요청보다 오래 걸립니다.

### Hotpost 연동용 로컬 API 키 준비

위 `start.cmd`는 단독 화면 확인용입니다. Hotpost의 `start-local.cmd`는 VoiceBench의 `.runtime\external-api-key.txt`가 **먼저 존재해야** 인증된 음성 API를 시작할 수 있습니다. 단독 서버를 실행 중이었다면 정상 종료한 뒤, PowerShell에서 아래 명령을 한 번 실행하세요. 웹 비밀번호를 입력하면 로컬 전용 서버와 PC별 API 키가 준비됩니다. 관리자 권한·LAN 공개는 필요하지 않습니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\host.ps1 -BindAddress 127.0.0.1 -NoTunnel -Background
```

`host.ps1`은 기존 키가 있으면 재사용하고 없으면 `.runtime\external-api-key.txt`에 생성합니다. 실행 중인 서버는 Hotpost 통합 시작 스크립트가 재사용합니다. 키를 채팅·설정 문서·Git에 복사하지 마세요. 새 PC에서 키를 새로 생성했다면 Hotpost도 **그 PC의** VoiceBench 폴더를 바라보게 합니다. 연결 점검은 `http://127.0.0.1:8775/api/production-health`에서 확인합니다.

## 6. 같은 LAN에서 사용

관리자 권한 PowerShell에서 실행합니다.

```powershell
.\host-lan.cmd
```

8자 이상의 웹 비밀번호를 입력하면 TCP 8765에 대해 개인 네트워크·로컬 서브넷만 허용하는 Windows 방화벽 규칙을 만들고 백그라운드 서버를 시작합니다.

새 PC의 LAN 주소는 다음 명령으로 확인합니다.

```powershell
ipconfig
```

다른 기기에서는 `http://<새-PC의-IPv4-주소>:8765`로 접속합니다. 로그인 사용자 이름은 `voice`입니다.

서버 종료:

```powershell
.\stop-hosting.cmd
```

## 7. 외부 API와 MCP

인증된 로컬 `host.ps1` 또는 LAN `host-lan.cmd`를 처음 실행하면 외부 API 키가 다음 로컬 파일에 생성됩니다.

```text
.runtime\external-api-key.txt
```

키는 Git에 포함되지 않습니다. 확인 명령:

```powershell
.\show-api-key.cmd
```

Claude Desktop MCP 연결은 [claude-lan-mcp.md](claude-lan-mcp.md), HTTP API는 [external-api.md](external-api.md)를 참고합니다. MCP를 다른 PC에 연결할 때는 새 서버의 LAN 주소와 새로 생성된 API 키를 사용합니다.

## 8. 설치 확인

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

웹 Studio에서 짧은 한국어 문장으로 첫 합성을 실행한 뒤 다음을 확인합니다.

- 엔진: `Qwen3-TTS 1.7B · 표현형 T1.0/P0.95`
- Seed: 13
- 품질 모드: `long_form_hq`
- 결과 파일: `data\artifacts\studio\request-<번호>.wav`

## 9. 업데이트

서버를 종료한 뒤 다음 명령을 실행합니다.

```powershell
git pull
.\setup-windows.cmd
```

`.venv`, `.runtime`, `data`는 유지됩니다. 의존성이나 모델 설치 사양이 바뀐 업데이트에서는 `setup-windows.cmd`가 필요한 패키지를 다시 확인합니다.

## 다른 PC로 작업 이전

Git에는 `.venv/`, `.runtime/`, `data/`가 없습니다. 개인 참조 `selected_reference.wav`·`selected_reference.txt`는 안전한 매체로 별도 이동하고, 엔진·모델·API 키는 새 PC에서 설치·생성하는 것이 기본입니다. 특히 GPU용 CUDA 빌드와 가상환경을 다른 PC에서 복사해 그대로 실행하지 마세요. 이전 합성 결과와 작업 이력까지 필요하면 기존 PC의 `data/`를 앱 종료 후 별도로 백업하고, 복원 후 파일 경로와 선택 엔진을 확인합니다. 두 PC의 작업 DB는 Git으로 동기화되지 않습니다.

## 문제 해결

### `py.exe`를 찾을 수 없음

python.org의 Python 3.10 64비트 설치 프로그램에서 Python Launcher를 포함해 설치합니다.

### CUDA를 사용할 수 없음

`nvidia-smi`가 정상 동작하는지 확인하고 NVIDIA 드라이버를 업데이트합니다. 이 프로젝트는 CPU 음성 생성을 지원하지 않습니다.

### Studio에서 레퍼런스 음성을 찾지 못함

두 레퍼런스 파일의 이름과 위치를 확인합니다. WAV와 TXT가 모두 있어야 합니다.

### 첫 실행이 오래 걸림

모델을 미리 받지 않았다면 첫 요청에서 수GB의 모델을 다운로드합니다. 다운로드 중에는 서버 오류 로그와 네트워크 상태를 확인합니다.

### 포트 8765가 이미 사용 중

다른 VoiceBench 서버를 먼저 종료하거나 다른 포트를 지정합니다.

```powershell
.\start.cmd -Port 8877
```
