# VoiceBench LAN MCP 및 다른 서비스 연결

GPU가 있는 VoiceBench PC 한 대가 음성을 생성한다. 같은 네트워크의 PC들은 MCP 또는 HTTP API로 요청한다. 두 방식 모두 기존 Studio와 동일한 직렬 GPU 대기열, 기본 개인 목소리, Seed 13, long-form HQ 및 음성 검사를 사용한다.

## 현재 연결 주소

현재 이 PC의 이더넷 주소는 `192.168.0.58`이다. 주소가 바뀌면 실행기의 출력으로 확인한다.

| 용도 | 주소 |
|---|---|
| MCP (Streamable HTTP) | `http://192.168.0.58:8766/mcp` |
| 다른 서비스용 API base URL | `http://192.168.0.58:8766` |
| 로컬 웹 Studio | `http://127.0.0.1:8765` |

8766 게이트웨이는 `/mcp`와 허용된 `/v1` API만 제공한다. Studio 관리 화면과 개인 원본 파일 경로는 제공하지 않는다. 인증은 기존 외부 API 키를 공통으로 사용한다. PC별 키·권한·작업 소유권은 이번 버전에 포함하지 않았다. 키를 가진 사용자는 공용 외부 작업 기록을 조회하고 취소할 수 있다.

## 서버 실행 및 종료

1. 먼저 루트 폴더의 `VoiceBench.cmd`로 Studio를 실행한다.
2. 최초 설치는 VoiceBench 폴더에서 `powershell -ExecutionPolicy Bypass -File .\setup-mcp.ps1`.
3. `mcp-lan.cmd`를 실행한다. 이미 실행 중이면 재사용한다. 자동 감지 주소를 바꾸려면 `mcp-lan.cmd --public-url http://192.168.0.58:8766`.
4. 다른 PC에서 연결이 안 되면 관리자 PowerShell에서 `powershell -ExecutionPolicy Bypass -File .\enable-mcp-firewall.ps1`을 실행한다. TCP 8766을 LocalSubnet 범위로 허용한다. 현재 LAN 프로필이 Public이어도 동작하도록 설정되어 있다.
5. 게이트웨이만 종료하려면 `powershell -ExecutionPolicy Bypass -File .\stop-mcp.ps1`. Studio는 유지된다.

MCP SDK는 `.runtime/mcp/venv`에 별도로 설치한다. TTS 엔진 환경은 변경하지 않는다. 게이트웨이 로그는 `.runtime/mcp-launch.log`, 프로세스 정보는 `.runtime/mcp-session.json`에 저장한다. 서버가 재부팅되면 위 실행기를 다시 실행한다. 자동 시작 서비스는 아직 등록하지 않았다.

키는 서버의 `show-api-key.cmd`로 확인한다. 실제 키를 저장소나 대본에 적지 않는다. HTTP 요청은 모두 `Authorization: Bearer <API_KEY>` 헤더가 필요하다. 미리듣기와 완성 WAV 주소도 인증이 필요하다. 내부망 HTTP 구성이다. 외부 클라우드 서비스에서는 사설 IP에 직접 연결할 수 없으므로 같은 LAN의 실행 에이전트 또는 별도 VPN/HTTPS 연결을 마련해야 한다.

## MCP 도구

| 도구 | 입력 / 결과 |
|---|---|
| `list_voices` | 공식 Qwen 9개·ZONOS2 3개의 ID, 사용 가능 여부, 한국어 미리듣기 주소 |
| `get_voice_preview` | `voice_id` → 저장된 샘플 주소. GPU 작업을 만들지 않음 |
| `generate_speech` | `text`, 선택적 `voice_id`, `generation_key` → 비동기 작업 `id` |
| `get_job` | `request_id` → 상태, 완료 조각 수, 대기 작업 수, 장별 결과, 품질 검사 |
| `get_audio` | `request_id`, 선택적 `chapter` → WAV 주소와 결과 정보 |
| `get_server_status` | 서버 준비 상태, 활성 외부 작업 수 |
| `generate_batch` | `items`: 1–20개의 `{text, voice_id?, generation_key?}` → 항목별 작업/오류 |
| `compare_voices` | `text`, 2–4개의 서로 다른 `voice_ids`, 선택적 `generation_key` → 목소리별 작업 |
| `cancel_job` | `request_id` → 취소 상태. 진행 중인 GPU 연산은 즉시 중단되지 않을 수 있음 |
| `list_jobs` | `limit`(1–100), `before_id`, `status` → 공용 외부 기록과 `next_before_id` |
| `export_for_editing` | 완료된 `request_id` → 원본 대본, 발음 처리된 구간 대본, WAV/장 주소, 오디오 형식, 품질, SRT |

`voice_id`를 생략하면 현재 기본 개인 목소리(my_voc)를 쓴다. `qwen-sohee`, `zonos-americanmale`, `zonos-americanfemale`, `zonos-britishfemale`처럼 공식 목소리를 지정할 수 있다. 개인 목소리 사용을 위해 공식 ID를 찾을 필요는 없다. 외부 호출은 기본 엔진·레퍼런스·Seed·품질 정책을 변경하지 않는다.

생성 요청은 즉시 ID를 반환한다. 이후에는 `get_job`을 5–10초 간격으로 호출한다. 같은 대본을 완료를 기다리기 위해 재제출하지 않는다. 동일 조건의 진행 중 요청 및 최근 24시간 완료 결과는 재사용한다. `generation_key`는 캐시 식별 조건에 포함되며 동일 대본/목소리와 함께 유지한다. 같은 키라도 대본이나 목소리가 다르면 별도 작업이다.

외부 활성 요청은 최대 4개, 새 요청은 분당 최대 10개다. 일괄/비교 요청은 원자적 처리가 아니므로 일부만 접수될 수 있다. `items[index].job.id`가 있는 항목은 유지하고 `retry_indices`에 있는 항목만 재시도한다. 접수되지 않은 항목은 서버에 보관하지 않는다. 429 오류이면 최소 60초 기다린다. GPU 생성은 한 번에 하나씩 수행한다.

편집 출력 SRT는 **전체 대본 한 개의 자막**이다. 구간 대본과 장별 오디오도 제공하지만 문장·단어별 타임스탬프를 추정하지 않는다. 편집 서비스에서 정확한 자막이 필요하면 WAV에 별도 정렬/전사를 수행한다. CapCut 초안 생성이나 MP4 렌더링은 이 MCP가 직접 실행하지 않으며, 결과를 auto_capcut에 전달하는 입력으로 활용한다.

## HTTP MCP를 지원하는 AI 클라이언트

연결 설정에 다음을 입력한다.

```text
Transport: Streamable HTTP
URL: http://192.168.0.58:8766/mcp
Header: Authorization: Bearer <API_KEY>
```

이 버전은 공통 Bearer 키 방식이다. OAuth 로그인만 지원하거나 헤더를 지정할 수 없는 클라이언트는 아래 stdio 브리지를 사용한다. 일반 웹브라우저에서 MCP 주소를 여는 것은 도구 연결이 아니다.

Python 공식 SDK 예제는 `tools/mcp_client_example.py`를 참고한다.

```powershell
python -m pip install mcp==1.30.0
$env:VOICEBENCH_MCP_URL = 'http://192.168.0.58:8766/mcp'
$env:VOICEBENCH_API_KEY = '<API_KEY>'
python tools\mcp_client_example.py
```

## stdio MCP만 지원하는 데스크톱 클라이언트

`tools/voicebench_mcp.py`를 클라이언트 PC로 복사한다. 이 브리지는 Python 3.10 이상과 표준 라이브러리만 사용한다. 클라이언트의 MCP 설정에 다음 구성을 적용한다(기존 항목 유지).

```json
{
  "mcpServers": {
    "voicebench": {
      "command": "py",
      "args": ["-3.10", "C:\\VoiceBench\\voicebench_mcp.py"],
      "env": {
        "VOICEBENCH_URL": "http://192.168.0.58:8766",
        "VOICEBENCH_API_KEY": "<API_KEY>"
      }
    }
  }
}
```

새 도구 11개와 기존 `voicebench_tts`, `voicebench_tts_submit`, `voicebench_tts_status` 3개가 제공된다. stdio의 `get_audio`는 선택적 절대 `output_path`로 **클라이언트 PC**에 WAV를 저장할 수 있다. HTTP MCP에는 서버 파일 저장 경로 인수를 제공하지 않는다. 설정 변경 뒤 클라이언트를 재시작한다.

## MCP가 없는 서비스: HTTP API

Python 프로그램, 백엔드, n8n의 HTTP 요청 노드 등은 MCP 없이 아래 API를 호출하면 된다.

| 요청 | 기능 |
|---|---|
| `GET /v1/health` | 상태 |
| `GET /v1/voices` | 목소리 목록 |
| `GET /v1/voices/{voice_id}/preview` | 미리듣기 WAV |
| `POST /v1/tts` | 생성: `{ "text": "안녕하세요.", "voice_id": "qwen-sohee", "generation_key": "post_123" }` |
| `GET /v1/tts/{id}` | 작업 조회 |
| `GET /v1/tts/{id}/audio` | 완성 WAV |
| `GET /v1/tts/{id}/chapters/{chapter}/audio` | 장별 WAV |
| `GET /v1/tts?limit=20&before_id=0&status=succeeded` | 기록 |
| `POST /v1/tts/{id}/cancel` | 취소, JSON 본문 `{}` |
| `GET /v1/tts/{id}/export` | 편집용 JSON/SRT |

REST 일괄/비교는 `POST /v1/tts`를 항목별로 호출한다. 요청 ID를 외부 서비스의 게시물/작업 레코드에 저장하고 상태 조회를 재개한다. 서버가 반환하는 REST의 `audio_url`, `preview_url`은 상대 경로이므로 API base URL을 앞에 붙인다. MCP 도구 응답은 클라이언트가 접근할 수 있는 절대 주소를 제공한다.

기본 Python 클라이언트(추가 패키지 불필요):

```powershell
$env:VOICEBENCH_API_KEY = '<API_KEY>'
python tools\external_tts_client.py --base-url http://192.168.0.58:8766 `
  --text '안녕하세요. 서비스 연결 테스트입니다.' --voice-id qwen-sohee `
  --generation-key post_123 --output narration.wav --editing-export
```

기존 요청을 다시 생성하지 않고 이어받기:

```powershell
python tools\external_tts_client.py --base-url http://192.168.0.58:8766 `
  --request-id 18 --output narration.wav --editing-export
```

HTTP 자동화 흐름은 `생성 POST → ID 저장 → 5–10초 대기 → 상태 GET → 완료 시 바이너리 WAV GET → 편집 export GET`이다. 실패·취소이면 폴링을 종료한다. n8n 등에서 WAV 응답은 JSON이 아닌 바이너리 파일로 받는다. 필요하면 장별 WAV도 같은 인증으로 다운로드한다.

## 확인 및 한계

- 서버 PC에서 `127.0.0.1:8766`으로 연결해 먼저 확인하고 다른 PC에서 LAN 주소로 확인한다.
- 같은 LAN이어도 게스트 Wi-Fi/클라이언트 격리나 방화벽이 있으면 연결되지 않을 수 있다.
- 도구 호출과 WAV 다운로드 양쪽에 인증 헤더가 필요하다. URL에 API 키를 붙이지 않는다.
- 공용 작업 기록, 취소 권한은 현재 공통 키 사용자에게 공유된다. PC별 권한과 공개 범위 설정은 후속 작업이다.
- MCP SDK는 공식 Python SDK 1.30.0으로 고정했다. [공식 SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x), [Streamable HTTP 규격](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

검증 명령:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.runtime\mcp\venv\Scripts\python.exe -m unittest discover -s mcp_tests -v
git diff --check
```
