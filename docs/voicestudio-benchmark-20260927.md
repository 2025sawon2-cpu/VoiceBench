# VoiceStudio 소스 검토 및 VoiceBench 반영 — 2026-09-27

## 검토 범위

VoiceStudio를 `.runtime/research/VoiceStudio`에 sparse clone하여 실제 구현을 읽었다. 검토 커밋은 `7ea5d18d8863588b00bc9931ac68feb1b4870ec4`이다. 이 폴더는 Git 추적에서 제외한다. 화면 GIF를 보고 전체 제품을 검증했다고 주장하지 않는다.

| 검토한 실제 소스 | 확인한 동작 | VoiceBench 적용 |
|---|---|---|
| [VoiceSelector.jsx](https://github.com/debpalash/VoiceStudio/blob/7ea5d18d8863588b00bc9931ac68feb1b4870ec4/electron/src/shared/components/VoiceSelector.jsx) | 검색, 즐겨찾기, 기본/복제/갤러리 등 그룹, 목소리 ID 선택 | 목소리와 추론 엔진의 분리, 검색·성별·모델·즐겨찾기, 명시적 voice_id |
| [voice-setup.tsx](https://github.com/debpalash/VoiceStudio/blob/7ea5d18d8863588b00bc9931ac68feb1b4870ec4/electron/src/renderer/src/features/clone/voice-setup.tsx) | 목소리 프로필 탐색, 격자 선택, 많은 프로필의 표시 처리 | 설명형 목소리 카드, 스크롤 목록, 선택 상태, 별도 미리듣기 버튼 |
| [VoicePreview.jsx](https://github.com/debpalash/VoiceStudio/blob/7ea5d18d8863588b00bc9931ac68feb1b4870ec4/electron/src/shared/components/VoicePreview.jsx) | 짧은 샘플 생성, 미리듣기 플레이어, 자동 재생 옵션 | 카드 선택 즉시 저장 샘플 재생, 고정 플레이어, 생성 기록을 만들지 않는 GET |
| [archetypes.py](https://github.com/debpalash/VoiceStudio/blob/7ea5d18d8863588b00bc9931ac68feb1b4870ec4/backend/api/routers/archetypes.py) | 캐시·갤러리·참조·현재 엔진 결과의 구분 | 생성 샘플과 원본 녹음의 명확한 구분, 모델 설정/참조 해시로 캐시 유효성 검사 |

VoiceStudio 소스·스타일·이미지·의존성을 제품에 복사하지 않았다. VoiceStudio의 AGPL-3.0-only 표기를 확인했고, 기존 VoiceBench Python/정적 웹 구조 안에서 동작을 별도로 구현했다. 전체 앱을 이식하면 기존 외부 API, 모델 워커, 품질 검사와 중복되는 부분이 많아 이번 요구에 맞지 않는다.

## 기본 음성의 실제 지원 범위

| 모델 경로 | 제공하는 목소리 | 정확한 성격 |
|---|---|---|
| ZONOS2 | AmericanMale, AmericanFemale, BritishFemale | upstream이 배포하는 공식 참조 녹음 3개로 복제 |
| Qwen CustomVoice 1.7B | Sohee, Aiden, Ryan, Serena, Vivian, Uncle_Fu, Dylan, Eric, Ono_Anna | 모델의 내장 화자 9명. generate_custom_voice 사용 |
| Qwen Base 1.7B | 공용 여성/남성, 내 목소리 | Base 모델의 참조 기반 복제. CustomVoice와 별도 모델 |
| VoxCPM2 / Fish / Chatterbox | 각각 공용 여성/남성, 내 목소리 | 현재 어댑터에 공식 이름 있는 화자 목록이 없다. Qwen 소희/에이든으로 만든 한국어 합성 참조를 각각의 엔진으로 복제 |

공식 화자 설명은 [Qwen 공식 문서](https://github.com/QwenLM/Qwen3-TTS)의 화자 설명을 기준으로 한국어로 표현했다. 소희를 제외한 Qwen 화자의 기반 언어는 중국어·영어·일본어이므로 한국어 억양은 샘플로 판단해야 한다. 이름의 ‘밝은/따뜻한/활기찬’은 화자 설명이며 Temperature나 특정 수치가 감정을 보장한다는 의미가 아니다. 감정 지시는 기본으로 넣지 않는다.

## 구현과 검증 기준

- 공식 12개 + 공용 8개를 같은 짧은 한국어 문장으로 미리 생성한다. 기본 Seed 13, 실패 시 10으로 한 번 재시도한다.
- Whisper 전사, 누락/반복 검출, 파형 검사를 통과한 파일만 생성 샘플로 노출한다. 이는 자동 오류 검사이지 음질 선호도 평가가 아니다.
- 샘플 메타데이터는 voice ID, 엔진 설정 해시, 참조 해시, 출력 해시, Seed, 대본, 전사/검사와 처리 시간을 기록한다.
- 미리듣기는 인증된 Range GET으로 저장 음원을 전달한다. 재생 때문에 작업이나 GPU 추론을 만들지 않는다.
- 내 목소리/등록 음성은 원본 녹음을 미리 들려준다. 현재 모델 출력인 것처럼 표시하지 않는다.
- 고급 설정은 요청에 저장하고 서버에서 지원 항목/범위를 검증한다. 외부 API 고정 정책 및 엔진 기본 설정과 분리한다.
- 생성에 사용되는 참조는 콘텐츠 해시로 복사하여, 이후 갤러리 파일 교체가 대기 작업을 바꾸지 않도록 한다.

## 유지 관리

Qwen CustomVoice 모델 고정 revision: `0c0e3051f131929182e2c023b9537f8b1c68adfe`. 추가 다운로드 약 4.52GB. 기존 Qwen 가상환경이 설치되어 있어야 한다.

```powershell
.venv/Scripts/python.exe tools/register_voice_gallery.py --download
# VoiceBench 서버를 종료하고, 대기/실행 작업이 없는 상태에서:
.venv/Scripts/python.exe tools/prepare_voice_gallery.py
# 일부 샘플만 다시 준비:
.venv/Scripts/python.exe tools/prepare_voice_gallery.py --ids qwen-sohee zonos-americanmale
```

모델 캐시는 Windows의 심볼릭 링크 권한을 요구하지 않는 일반 파일 방식이며, 런타임에는 다운로드한 로컬 snapshot 경로를 고정해 사용한다. 미리듣기 파일과 모델은 로컬 데이터로 Git에 넣지 않는다. 서버 기본 포트에서 서비스 중이면 준비 도구가 실행되지 않도록 한다.


## 완료 검증

- 생성 샘플 20개 모두 준비 완료: 공식 12개, 공용 8개. 원본 미리듣기 6개는 본인 녹음의 모델별 진입점 5개와 등록 보관함 1개이며, 별도 기본 화자 6명이라는 뜻이 아니다.
- 모든 생성 샘플을 정식 HTTP 경로로 조회해 WAV 파싱 성공. 로컬 조회 최대 73.5ms. `.runtime/gallery-http-audit.json`에 파일 해시/길이/조회 시간 기록.
- Python 회귀 81개, Node UI 검사 12개 통과. 입력 타입 보완 후 관련 Python 17개 재검사 통과. 인증된 미리듣기 byte-range 및 작업 생성 없음도 검사.
- 브라우저에서 빈 대본으로 소희 선택 → 즉시 재생 → 5.44초 끝까지 재생 확인. 남성/분위기 검색, 즐겨찾기, 선택을 바꾸지 않는 미리듣기, 좁은 화면 가로 넘침 없음 확인.
- 정식 UI에서 Qwen 소희 + Temperature 0.95로 요청 #33 생성 성공. 약 4.8초. 다시 사용에서 동일 목소리와 고급 설정 복원 확인.
- 기본 자동화는 ZONOS2 본인 목소리, Seed 13, HQ 유지. 외부 API 인증 유지 확인. 서버 재시작 완료.
