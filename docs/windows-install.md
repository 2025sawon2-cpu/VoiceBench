# Windows 새 PC 설치 가이드

> 2026-09-26: 운영 기본 모델은 ZONOS2입니다. 아래 Qwen 설치는 비교 엔진 및 Whisper 검사 환경에도 사용됩니다. ZONOS2는 추가로 `powershell -ExecutionPolicy Bypass -File .\install-fish-zonos.ps1 -Engine zonos2`를 실행해 설치·등록하세요. 기본 레퍼런스는 기존 본인 목소리입니다.

이 문서는 새 Windows PC에서 저장소를 복제하고 VoiceBench의 Qwen3-TTS 1.7B 음성 복제 서비스를 설치하는 절차입니다. 모델, 생성 음성, 개인 레퍼런스 음성, 웹 비밀번호, 외부 API 키는 Git에 포함되지 않습니다.

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

LAN 서버를 처음 실행하면 외부 API 키가 다음 로컬 파일에 생성됩니다.

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
