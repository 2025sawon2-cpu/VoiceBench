# VoiceBench External TTS API

외부 AI나 자동화 프로그램이 VoiceBench의 기본 사용자 음성 또는 승인된 공식 목소리로 WAV를 생성하는 전용 API입니다. 이 API 키는 관리자 웹 로그인과 분리되어 있으며 프로젝트, 개인 레퍼런스, 벤치마크에는 접근할 수 없습니다.

`GET /v1/voices`는 설치된 Qwen CustomVoice 및 ZONOS2 공식 목소리 목록을 반환합니다. `gender`, `model`, `available`, `preview_url`, `preview_kind`로 선택 UI를 구성할 수 있습니다. `preview_url`도 Bearer 인증이 필요하며, 저장된 샘플만 재생하고 새로운 TTS 작업은 만들지 않습니다. `reference` 미리듣기는 공식 참조 녹음으로 실제 한국어 생성 결과와 구별해 표시하세요.

`POST /v1/tts`에 `{"text":"읽을 대본", "voice_id":"qwen-sohee"}`처럼 승인된 목소리를 지정할 수 있습니다. 서버가 해당 엔진·화자를 결정하고 Seed 13/HQ 정책을 유지합니다. 생략 또는 빈 값이면 기존 기본 목소리를 사용하며 기본 설정을 바꾸지 않습니다. 응답의 `voice_id`로 선택 적용 여부를 확인할 수 있고, 서로 다른 목소리는 결과 캐시를 공유하지 않습니다. 엔진·참조 경로·생성 파라미터를 임의로 지정하는 기능은 제공하지 않습니다.

## 접속 정보

- Base URL: `http://192.168.0.58:8766`
- 인증: `Authorization: Bearer <VOICEBENCH_API_KEY>`
- Content-Type: `application/json`
- 별도의 글자 수 제한 없음(HTTP 요청 본문은 최대 2MB)
- 기본 Seed: 서버에서 `13`으로 고정(외부 입력은 무시)
- 동시 대기·실행 작업: 최대 4개
- 생성 요청 제한: 분당 10개

API 키는 저장소나 프롬프트에 공개하지 마세요. 로컬 PC에서 `show-api-key.cmd`를 실행하면 확인할 수 있습니다.

## 1. 생성 요청

```http
POST /v1/tts
Authorization: Bearer <VOICEBENCH_API_KEY>
Content-Type: application/json

{
  "text": "안녕하세요. 외부 AI에서 요청한 음성입니다."
}
```

외부 요청의 첫 생성 Seed는 항상 `13`이다. 호환되지 않은 기존 클라이언트가 `seed` 필드를 보내더라도 서버는 그 값을 무시한다. 명확한 품질 실패가 감지된 조각의 대체 후보 Seed는 서버가 내부적으로만 관리한다.

응답은 HTTP `202 Accepted`입니다.
동일 조건의 작업이 실행 중이거나 최근 24시간 안에 완료됐다면 새 작업을 만들지 않고 HTTP `200`으로 기존 요청을 반환하며 `reused`가 `true`입니다.

```json
{
  "id": 10,
  "status": "queued",
  "seed": 13,
  "segment_count": 1,
  "completed_segments": 0,
  "status_url": "/v1/tts/10",
  "audio_url": null,
  "reused": false
}
```

## 2. 상태 확인

5~10초 간격으로 확인합니다.

```http
GET /v1/tts/10
Authorization: Bearer <VOICEBENCH_API_KEY>
```

`status`는 `queued`, `running`, `succeeded`, `failed`, `cancelled` 중 하나입니다. `succeeded`가 되면 `audio_url`이 생깁니다.

요청 ID를 받은 뒤에는 같은 대본을 다시 `POST`하지 말고 해당 ID만 조회합니다. 상태 응답의 `download_count`와 `last_downloaded_at`은 최종 WAV 전체 전송이 완료된 횟수와 마지막 시각입니다.

장문 HQ 요청에는 `chapters` 배열도 포함됩니다. 각 장이 완료되면 해당 항목의 `audio_url`에서 먼저 확인할 수 있고, 전체 요청이 완료되면 최종 `audio_url`에서 결합 WAV를 받습니다.

`quality_control`에는 `checked_segments`, `regenerated_segments`, `fallback_selected_segments`, `warning_segments`, `selected_seed_counts`가 포함됩니다. 기본 Seed 13 이외의 후보가 실제 선택되면 `selected_seed_counts`에 해당 Seed와 조각 수가 표시됩니다. `warning_segments`가 0보다 크면 자동 후보 중 최선을 사용했지만 사람의 청취 확인을 권장한다는 뜻입니다.

`speech_plan`에는 `paragraph_count`, `segment_count`, `sentence_count`, `paragraph_breaks`, `role_counts`가 포함됩니다. `role_counts`로 도입, 질문, 전환, 결론 등 의미 단위가 어떻게 구성됐는지 확인할 수 있습니다.

## 3. WAV 다운로드

```http
GET /v1/tts/10/audio
Authorization: Bearer <VOICEBENCH_API_KEY>
```

응답은 엔진에 따라 24kHz 또는 44.1kHz mono PCM WAV입니다. 브라우저 및 스트리밍 클라이언트를 위한 HTTP Range 요청도 지원합니다.

## curl 예제

```bash
curl -X POST "http://192.168.0.58:8766/v1/tts" \
  -H "Authorization: Bearer $VOICEBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"안녕하세요. API 테스트입니다."}'
```

## Python 클라이언트

프로젝트에 포함된 클라이언트를 사용할 수 있습니다.

```powershell
$env:VOICEBENCH_API_KEY = '<API 키>'
python tools\external_tts_client.py `
  --base-url 'http://192.168.0.58:8766' `
  --text '안녕하세요. 외부 AI가 요청한 대본입니다.' `
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
      }
    },
    "required": ["text"]
  }
}
```

에이전트가 `create_voice`를 호출하면 구현부는 생성 요청을 보내고, 완료 상태를 폴링한 다음 WAV 파일 또는 다운로드 URL을 반환해야 합니다.
장문 요청은 생성 호출을 오래 열어두지 않고 요청 ID를 즉시 반환한 뒤 상태 도구로 확인해야 합니다. 대기 시간이 초과되어도 기존 ID를 보존하며 같은 대본을 다시 제출하지 않습니다.

## 운영 조건

- 기본 엔진은 `ZONOS2 Q8/CUDA 내 목소리(T0.9/P0.95/minP0.05)`, 고정 Seed는 `13`입니다.
- 긴 대본은 장문 HQ 모드에서 질문, 전환어, 결론, 문단 경계를 먼저 분석하고 약 180자를 목표로 의미 단위로 나눕니다. 장 파일도 가능한 한 문단 끝에서 나눈 뒤 최종 WAV로 결합합니다.
- 문단 끝은 기본 420ms, 질문 문단 뒤는 500ms, 전환·결론 단위 앞은 최소 460ms의 전체 호흡을 목표로 합니다. 생성 음성 자체의 가장자리 무음을 측정해 부족한 시간만 삽입합니다.
- 각 조각은 로컬 CPU Whisper Base로 대본 일치 여부를 검사합니다. 음절 누락, 비정상 반복, 빈 음성, 극단적인 길이처럼 신뢰도 높은 실패에만 서버 내부 대체 후보를 최대 두 번 만들고 가장 좋은 결과를 선택합니다.
- 각 조각의 실제 앞뒤 무음을 분석해 과도한 정지를 줄이고 직전 문장부호에 맞는 간격으로 결합합니다. 페이드와 음량 보정은 추후 비교 대상으로 남겨두었습니다.
- 동일 조건의 실행 중 작업과 최근 24시간 내 완료 작업은 자동 재사용됩니다.

- PC가 켜져 있고 절전 상태가 아니어야 합니다.
- VoiceBench와 ngrok 프로세스가 실행 중이어야 합니다.
- API 키를 가진 프로그램은 사용자의 목소리로 임의의 대본을 생성할 수 있으므로 신뢰할 수 있는 AI에만 전달하세요.
- 키가 유출되면 새 키로 교체하고 호스팅을 재시작해야 합니다.


2026-09-26: 기본 엔진은 ZONOS2이며 기존 본인 목소리와 Seed 13을 유지합니다. 240자 이하 대본은 한 번에 생성합니다. HQ 자동 검사 실패 시 Seed 10, 42로 재시도하며, 모두 실패하거나 음성인식 검사를 사용할 수 없으면 완료 음성을 제공하지 않습니다. Qwen은 웹에서 비교용으로 선택할 수 있습니다.


새 MCP 도구·기록·취소·편집 출력은 [LAN 연결 안내](lan-mcp-and-services.md)를 참고하세요.
