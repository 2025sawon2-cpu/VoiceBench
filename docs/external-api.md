# VoiceBench External TTS API

외부 AI나 자동화 프로그램이 VoiceBench의 고정된 사용자 음성으로 WAV를 생성하는 전용 API입니다. 이 API 키는 관리자 웹 로그인과 분리되어 있으며 프로젝트, 레퍼런스, 벤치마크에는 접근할 수 없습니다.

## 접속 정보

- Base URL: `https://explicit-silliness-ceramics.ngrok-free.dev`
- 인증: `Authorization: Bearer <VOICEBENCH_API_KEY>`
- Content-Type: `application/json`
- 별도의 글자 수 제한 없음(HTTP 요청 본문은 최대 2MB)
- Seed 범위: `0`부터 `2147483647`
- 동시 대기·실행 작업: 최대 4개
- 생성 요청 제한: 분당 10개

API 키는 저장소나 프롬프트에 공개하지 마세요. 로컬 PC에서 `show-api-key.cmd`를 실행하면 확인할 수 있습니다.

## 1. 생성 요청

```http
POST /v1/tts
Authorization: Bearer <VOICEBENCH_API_KEY>
Content-Type: application/json

{
  "text": "안녕하세요. 외부 AI에서 요청한 음성입니다.",
  "seed": 10
}
```

응답은 HTTP `202 Accepted`입니다.

```json
{
  "id": 10,
  "status": "queued",
  "seed": 10,
  "segment_count": 1,
  "completed_segments": 0,
  "status_url": "/v1/tts/10",
  "audio_url": null
}
```

## 2. 상태 확인

5~10초 간격으로 확인합니다.

```http
GET /v1/tts/10
Authorization: Bearer <VOICEBENCH_API_KEY>
```

`status`는 `queued`, `running`, `succeeded`, `failed`, `cancelled` 중 하나입니다. `succeeded`가 되면 `audio_url`이 생깁니다.

## 3. WAV 다운로드

```http
GET /v1/tts/10/audio
Authorization: Bearer <VOICEBENCH_API_KEY>
```

응답은 24kHz mono PCM WAV입니다. 브라우저 및 스트리밍 클라이언트를 위한 HTTP Range 요청도 지원합니다.

## curl 예제

```bash
curl -X POST "https://explicit-silliness-ceramics.ngrok-free.dev/v1/tts" \
  -H "Authorization: Bearer $VOICEBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -H "ngrok-skip-browser-warning: VoiceBench API client" \
  -d '{"text":"안녕하세요. API 테스트입니다.","seed":10}'
```

## Python 클라이언트

프로젝트에 포함된 클라이언트를 사용할 수 있습니다.

```powershell
$env:VOICEBENCH_API_KEY = '<API 키>'
python tools\external_tts_client.py `
  --base-url 'https://explicit-silliness-ceramics.ngrok-free.dev' `
  --text '안녕하세요. 외부 AI가 요청한 대본입니다.' `
  --seed 10 `
  --output result.wav
```

## AI 에이전트용 도구 정의 예시

에이전트에는 다음 함수를 제공하고, 실제 실행 코드는 위 HTTP API를 호출하도록 연결합니다.

```json
{
  "name": "create_voice",
  "description": "Create a WAV file using the owner's fixed cloned voice.",
  "parameters": {
    "type": "object",
    "properties": {
      "text": {
        "type": "string",
        "description": "Text to speak. Long scripts are split into safe segments automatically."
      },
      "seed": {
        "type": "integer",
        "default": 10,
        "minimum": 0,
        "maximum": 2147483647
      }
    },
    "required": ["text"]
  }
}
```

에이전트가 `create_voice`를 호출하면 구현부는 생성 요청을 보내고, 완료 상태를 폴링한 다음 WAV 파일 또는 다운로드 URL을 반환해야 합니다.

## 운영 조건

- PC가 켜져 있고 절전 상태가 아니어야 합니다.
- VoiceBench와 ngrok 프로세스가 실행 중이어야 합니다.
- API 키를 가진 프로그램은 사용자의 목소리로 임의의 대본을 생성할 수 있으므로 신뢰할 수 있는 AI에만 전달하세요.
- 키가 유출되면 새 키로 교체하고 호스팅을 재시작해야 합니다.
