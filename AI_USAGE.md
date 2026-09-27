# VoiceBench AI Usage Guide

이 문서는 외부 AI 에이전트 또는 자동화 프로그램이 VoiceBench를 호출해 사용자의 고정된 복제 음성으로 WAV 파일을 생성하는 방법을 설명한다.

## 연결 정보

- Base URL: `https://explicit-silliness-ceramics.ngrok-free.dev`
- 인증 방식: HTTP Bearer token
- API 키 전달: `Authorization: Bearer <VOICEBENCH_API_KEY>`
- 기본 Seed: `13` (외부 입력은 받지 않으며, 전달되어도 무시됨)
- 결과 형식: 24kHz, mono, PCM WAV

`VOICEBENCH_API_KEY`의 실제 값은 저장소에 포함되지 않는다. 사용자에게 별도로 받거나 로컬 PC에서 `show-api-key.cmd`를 실행해 확인해야 한다. 웹 관리자 비밀번호를 API 인증에 사용하지 않는다.

ngrok 안내 페이지를 우회하기 위해 모든 API 요청에 다음 헤더를 추가한다.

```http
ngrok-skip-browser-warning: VoiceBench API client
```

## 에이전트 실행 절차

AI 에이전트는 다음 순서를 정확히 따른다.

1. `POST /v1/tts`로 대본만 전송한다.
2. 응답에서 `id`와 `status_url`을 저장한다.
   같은 대본을 다시 `POST`하지 말고, 이후에는 반드시 이 `id`의 상태만 확인한다.
3. `GET {status_url}`을 5초 간격으로 호출한다.
4. `status`가 `queued` 또는 `running`이면 계속 기다린다.
5. `status`가 `succeeded`이면 `audio_url`에서 WAV를 다운로드한다.
6. `status`가 `failed` 또는 `cancelled`이면 폴링을 중단하고 `error`를 사용자에게 보고한다.
7. HTTP `429`를 받으면 최소 60초 후 다시 시도한다.
8. HTTP `401`을 받으면 API 키가 없거나 잘못된 것이므로 사용자에게 새 키를 요청한다.

동일한 대본·엔진·Seed·레퍼런스·품질 모드의 작업이 실행 중이거나 최근 24시간 안에 완료됐다면 서버는 새 작업을 만들지 않고 기존 요청을 `reused: true`와 함께 반환한다.

고정된 내 목소리와 `ZONOS2 Q8/CUDA 내 목소리(T0.9/P0.95/minP0.05)` 엔진은 서버에서 자동 선택된다. 에이전트가 레퍼런스 음성, 프로젝트 또는 엔진을 지정할 필요가 없다.

## 1. 음성 생성

```http
POST /v1/tts
Authorization: Bearer <VOICEBENCH_API_KEY>
Content-Type: application/json; charset=utf-8
ngrok-skip-browser-warning: VoiceBench API client

{
  "text": "안녕하세요. 외부 AI가 요청한 대본입니다."
}
```

첫 생성 Seed는 서버에서 항상 `13`으로 고정된다. 요청에 `seed` 필드를 보내더라도 무시된다. 자동 품질 검사가 명확한 누락·반복을 찾은 조각에만 서버 내부 대체 Seed를 시도할 수 있으며, 이는 외부에서 지정할 수 없다.

성공 응답은 HTTP `202 Accepted`이다.

```json
{
  "id": 10,
  "status": "queued",
  "seed": 13,
  "segment_count": 1,
  "completed_segments": 0,
  "duration_seconds": null,
  "error": null,
  "status_url": "/v1/tts/10",
  "audio_url": null,
  "reused": false
}
```

## 2. 상태 확인

```http
GET /v1/tts/10
Authorization: Bearer <VOICEBENCH_API_KEY>
ngrok-skip-browser-warning: VoiceBench API client
```

완료 응답 예시:

```json
{
  "id": 10,
  "status": "succeeded",
  "seed": 13,
  "segment_count": 1,
  "completed_segments": 1,
  "duration_seconds": 4.0,
  "quality_control": {
    "checked_segments": 1,
    "regenerated_segments": 0,
    "fallback_selected_segments": 0,
    "warning_segments": 0,
    "selected_seed_counts": {"13": 1}
  },
  "speech_plan": {
    "paragraph_count": 1,
    "segment_count": 1,
    "sentence_count": 1,
    "paragraph_breaks": 0,
    "role_counts": {"opening": 1}
  },
  "error": null,
  "status_url": "/v1/tts/10",
  "audio_url": "/v1/tts/10/audio"
}
```

`status_url`과 `audio_url`은 상대경로이므로 Base URL 앞에 붙여 사용한다.
상태 응답의 `download_count`와 `last_downloaded_at`으로 최종 WAV가 실제로 전송 완료됐는지 확인할 수 있다.

## 3. WAV 다운로드

```http
GET /v1/tts/10/audio
Authorization: Bearer <VOICEBENCH_API_KEY>
ngrok-skip-browser-warning: VoiceBench API client
```

응답 본문을 `.wav` 파일로 저장한다. 오디오 요청은 HTTP Range를 지원한다.

## curl 예제

```bash
curl -X POST "https://explicit-silliness-ceramics.ngrok-free.dev/v1/tts" \
  -H "Authorization: Bearer $VOICEBENCH_API_KEY" \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "ngrok-skip-browser-warning: VoiceBench API client" \
  -d '{"text":"안녕하세요. API 음성 생성 요청입니다."}'
```

## 포함된 Python 클라이언트

저장소의 `tools/external_tts_client.py`는 생성, 상태 폴링, WAV 다운로드를 한 번에 수행한다.

```powershell
$env:VOICEBENCH_API_KEY = '<사용자에게 받은 API 키>'
python tools\external_tts_client.py `
  --base-url 'https://explicit-silliness-ceramics.ngrok-free.dev' `
  --text '읽을 대본' `
  --output result.wav
```

## 제약과 운영 조건

- 별도의 글자 수 제한은 없지만 HTTP 요청 본문은 최대 2MB이다.
- 긴 대본은 장문 HQ 모드로 처리한다. 질문, 전환어, 결론, 문단 경계를 먼저 분석한 뒤 약 180자를 목표로 의미 단위 생성 조각을 구성하고, 약 3~5분 분량의 장으로 묶어 최종 WAV 하나로 결합한다.
- 문단 끝에는 기본 420ms, 질문 문단 뒤에는 500ms, 새 전환·결론 단위 앞에는 최소 460ms의 전체 호흡을 계획한다. 생성 파일에 이미 들어 있는 앞뒤 무음을 측정한 뒤 부족한 만큼만 추가한다.
- `speech_plan`에는 문단·문장·호흡 단위 수와 도입·질문·전환·결론 역할별 개수가 표시된다.
- 각 생성 조각은 로컬 CPU의 Whisper Base로 대본 일치 여부를 검사한다. 명확한 음절 누락, 비정상 반복, 빈 음성, 극단적인 길이가 감지될 때만 서버 내부 후보를 최대 두 번 추가 생성하고 가장 좋은 후보를 선택한다.
- `quality_control`에는 검사·재생성 조각 수와 실제 선택된 Seed별 조각 수가 기록된다. `warning_segments`가 0보다 크면 자동 후보 중 최선의 결과를 사용했다는 뜻이므로 사람이 들어보는 것이 좋다.
- 결합할 때 각 조각의 실제 앞뒤 무음을 측정해 과도한 무음을 제거하고, 마침표·물음표·쉼표 등 직전 문장부호에 맞는 간격으로 맞춘다. 페이드와 음량 보정은 아직 적용하지 않는다.
- MCP는 8개 이상의 생성 조각을 가진 장문 요청에서 오래 대기하지 않고 요청 ID를 즉시 반환한다. 이후 `voicebench_tts_status`만 사용한다.
- 짧은 요청의 대기 시간이 초과돼도 같은 요청 ID와 현재 상태가 반환된다. 시간 초과를 이유로 대본을 다시 제출하지 않는다.
- 동일 조건의 실행 중 작업과 최근 24시간 내 완료 작업은 자동으로 재사용한다.
- 동시 대기·실행 가능한 외부 작업은 최대 4개이다.
- 생성 요청은 분당 최대 10개이다.
- PC, VoiceBench 서버, ngrok이 실행 중일 때만 API를 사용할 수 있다.
- API 키로는 음성 생성, 해당 외부 요청의 상태 확인, WAV 다운로드만 가능하다.
- 프로젝트, 레퍼런스 음성, 벤치마크, 관리자 화면에는 API 키로 접근할 수 없다.

## AI 에이전트용 요약 지시문

```text
VoiceBench로 음성을 생성할 때 POST /v1/tts를 호출한다.
Authorization: Bearer <VOICEBENCH_API_KEY> 헤더를 사용한다.
Seed는 입력하지 않는다. 서버가 첫 후보에 13을 사용하며 전달된 seed 값은 무시한다.
반환된 status_url을 5초 간격으로 확인한다.
한 번 받은 요청 ID를 버리거나 같은 대본을 다시 제출하지 않는다.
status가 succeeded가 되면 audio_url에서 WAV를 다운로드해 사용자에게 제공한다.
failed 또는 cancelled이면 error를 보고한다.
429이면 60초 기다리고, 401이면 사용자에게 올바른 API 키를 요청한다.
```

더 자세한 설명은 `docs/external-api.md`를 참고한다.


2026-09-26: 기본 엔진은 ZONOS2이며 기존 본인 목소리와 Seed 13을 유지합니다. 240자 이하 대본은 한 번에 생성합니다. HQ 자동 검사 실패 시 Seed 10, 42로 재시도하며, 모두 실패하거나 음성인식 검사를 사용할 수 없으면 완료 음성을 제공하지 않습니다. Qwen은 웹에서 비교용으로 선택할 수 있습니다.
