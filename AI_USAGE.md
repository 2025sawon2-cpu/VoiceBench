# VoiceBench AI Usage Guide

이 문서는 외부 AI 에이전트 또는 자동화 프로그램이 VoiceBench를 호출해 사용자의 고정된 복제 음성으로 WAV 파일을 생성하는 방법을 설명한다.

## 연결 정보

- Base URL: `https://explicit-silliness-ceramics.ngrok-free.dev`
- 인증 방식: HTTP Bearer token
- API 키 전달: `Authorization: Bearer <VOICEBENCH_API_KEY>`
- 기본 Seed: `10`
- 결과 형식: 24kHz, mono, PCM WAV

`VOICEBENCH_API_KEY`의 실제 값은 저장소에 포함되지 않는다. 사용자에게 별도로 받거나 로컬 PC에서 `show-api-key.cmd`를 실행해 확인해야 한다. 웹 관리자 비밀번호를 API 인증에 사용하지 않는다.

ngrok 안내 페이지를 우회하기 위해 모든 API 요청에 다음 헤더를 추가한다.

```http
ngrok-skip-browser-warning: VoiceBench API client
```

## 에이전트 실행 절차

AI 에이전트는 다음 순서를 정확히 따른다.

1. `POST /v1/tts`로 대본과 Seed를 전송한다.
2. 응답에서 `id`와 `status_url`을 저장한다.
3. `GET {status_url}`을 5초 간격으로 호출한다.
4. `status`가 `queued` 또는 `running`이면 계속 기다린다.
5. `status`가 `succeeded`이면 `audio_url`에서 WAV를 다운로드한다.
6. `status`가 `failed` 또는 `cancelled`이면 폴링을 중단하고 `error`를 사용자에게 보고한다.
7. HTTP `429`를 받으면 최소 60초 후 다시 시도한다.
8. HTTP `401`을 받으면 API 키가 없거나 잘못된 것이므로 사용자에게 새 키를 요청한다.

고정된 내 목소리와 현재 Qwen 엔진은 서버에서 자동 선택된다. 에이전트가 레퍼런스 음성, 프로젝트 또는 엔진을 지정할 필요가 없다.

## 1. 음성 생성

```http
POST /v1/tts
Authorization: Bearer <VOICEBENCH_API_KEY>
Content-Type: application/json; charset=utf-8
ngrok-skip-browser-warning: VoiceBench API client

{
  "text": "안녕하세요. 외부 AI가 요청한 대본입니다.",
  "seed": 10
}
```

Seed는 생략할 수 있으며 생략하면 `10`이 사용된다.

성공 응답은 HTTP `202 Accepted`이다.

```json
{
  "id": 10,
  "status": "queued",
  "seed": 10,
  "segment_count": 1,
  "completed_segments": 0,
  "duration_seconds": null,
  "error": null,
  "status_url": "/v1/tts/10",
  "audio_url": null
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
  "seed": 10,
  "segment_count": 1,
  "completed_segments": 1,
  "duration_seconds": 4.0,
  "error": null,
  "status_url": "/v1/tts/10",
  "audio_url": "/v1/tts/10/audio"
}
```

`status_url`과 `audio_url`은 상대경로이므로 Base URL 앞에 붙여 사용한다.

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
  -d '{"text":"안녕하세요. API 음성 생성 요청입니다.","seed":10}'
```

## 포함된 Python 클라이언트

저장소의 `tools/external_tts_client.py`는 생성, 상태 폴링, WAV 다운로드를 한 번에 수행한다.

```powershell
$env:VOICEBENCH_API_KEY = '<사용자에게 받은 API 키>'
python tools\external_tts_client.py `
  --base-url 'https://explicit-silliness-ceramics.ngrok-free.dev' `
  --text '읽을 대본' `
  --seed 10 `
  --output result.wav
```

## 제약과 운영 조건

- 별도의 글자 수 제한은 없지만 HTTP 요청 본문은 최대 2MB이다.
- 긴 대본은 서버가 약 90자 단위로 자동 분할해 하나의 WAV로 합친다.
- 동시 대기·실행 가능한 외부 작업은 최대 4개이다.
- 생성 요청은 분당 최대 10개이다.
- PC, VoiceBench 서버, ngrok이 실행 중일 때만 API를 사용할 수 있다.
- API 키로는 음성 생성, 해당 외부 요청의 상태 확인, WAV 다운로드만 가능하다.
- 프로젝트, 레퍼런스 음성, 벤치마크, 관리자 화면에는 API 키로 접근할 수 없다.

## AI 에이전트용 요약 지시문

```text
VoiceBench로 음성을 생성할 때 POST /v1/tts를 호출한다.
Authorization: Bearer <VOICEBENCH_API_KEY> 헤더를 사용한다.
Seed가 지정되지 않으면 10을 사용한다.
반환된 status_url을 5초 간격으로 확인한다.
status가 succeeded가 되면 audio_url에서 WAV를 다운로드해 사용자에게 제공한다.
failed 또는 cancelled이면 error를 보고한다.
429이면 60초 기다리고, 401이면 사용자에게 올바른 API 키를 요청한다.
```

더 자세한 설명은 `docs/external-api.md`를 참고한다.
