# Claude Desktop LAN MCP 연결

> 현재 권장 방식은 Studio를 로컬에 두고 `mcp-lan.cmd`로 8766 게이트웨이를 실행하는 방식입니다. 새 도구 11개와 HTTP MCP 연결은 [최신 LAN 연결 안내](lan-mcp-and-services.md)를 참고하세요. 아래 8765 호스팅 방식은 기존 방식이며 계속 사용할 수 있습니다.


이 구성은 GPU가 있는 **VoiceBench PC**에서 음성을 합성하고, 같은 사설망의 **Claude Desktop PC**가 MCP 도구로 요청하는 방식이다. ngrok이나 인터넷 공개는 사용하지 않는다.

## 1. VoiceBench PC: LAN 서비스 시작

VoiceBench PC에서 PowerShell을 **관리자 권한으로 실행**한 뒤 프로젝트 폴더에서 실행한다.

```powershell
.\host-lan.cmd
```

이 명령은 다음을 수행한다.

- VoiceBench를 `0.0.0.0:8765`에서 시작한다.
- Windows 방화벽에서 `8765/TCP`를 **Private 프로필 + LocalSubnet(같은 사설망)** 으로만 허용한다.
- ngrok 터널을 시작하지 않는다.

VoiceBench PC의 IPv4 주소를 확인한다.

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'} | Format-Table IPAddress,InterfaceAlias
```

예를 들어 주소가 `192.168.0.25`라면 API 주소는 `http://192.168.0.25:8765`이다. 공유기에서 이 PC에 DHCP 주소 예약을 해 두면 주소가 바뀌지 않는다.

API 키는 VoiceBench PC에서 확인한다.

```powershell
.\show-api-key.cmd
```

종료할 때는 다음을 실행한다.

```powershell
.\stop-hosting.cmd
```

## 2. Claude Desktop PC: MCP 모듈 배치

`tools/voicebench_mcp.py` 파일을 Claude Desktop PC에 복사한다. 예시는 `C:\VoiceBench\voicebench_mcp.py`이다. Python 3.10 이상이 필요하다. 이 파일은 외부 패키지를 설치하지 않는다.

Claude Desktop을 완전히 종료한 뒤 `%APPDATA%\Claude\claude_desktop_config.json`을 열어 `mcpServers`에 다음 항목을 추가한다. 기존 JSON 항목은 유지한다.

```json
{
  "mcpServers": {
    "voicebench": {
      "command": "py",
      "args": ["-3.10", "C:\\VoiceBench\\voicebench_mcp.py"],
      "env": {
        "VOICEBENCH_URL": "http://192.168.0.25:8765",
        "VOICEBENCH_API_KEY": "VoiceBench PC에서 확인한 API 키"
      }
    }
  }
}
```

Python Launcher가 없으면 `command`에 Python 실행 파일의 절대 경로를 지정한다. 예: `C:\\Users\\name\\AppData\\Local\\Programs\\Python\\Python310\\python.exe`. JSON 안에서 Windows 경로의 `\`는 반드시 `\\`로 쓴다.

Claude Desktop을 다시 시작하면 `voicebench_tts`, `voicebench_tts_submit`, `voicebench_tts_status` 도구가 표시된다.

## Claude에게 요청하는 예

```text
다음 대본을 VoiceBench로 음성 생성하고,
C:\Users\me\Downloads\announcement.wav 에 저장해줘.

안녕하세요. 오늘 안내 말씀을 드립니다.
```

`voicebench_tts`는 짧은 대본은 완료까지 기다리지만, 8개 이상의 생성 조각으로 나뉘는 장문은 요청 ID를 즉시 반환한다. 장문 응답을 받으면 같은 대본을 다시 보내지 말고 `voicebench_tts_status`에 그 요청 ID를 전달한다.

`output_path`가 주어졌다면 완료 상태를 확인할 때 같은 경로를 `voicebench_tts_status`에 전달해 **Claude Desktop이 설치된 PC**에 WAV를 저장한다. 제한 시간이 지나도 오류로 새 작업을 만들지 않고 기존 요청 ID와 실행 상태를 반환한다.

서버는 동일한 대본과 고정 생성 조건의 작업이 실행 중이거나 최근 24시간 안에 완료된 경우 기존 요청을 자동 재사용한다. 따라서 Claude가 실수로 다시 제출해도 중복 GPU 작업이 생기지 않는다.

## 보안 원칙

- VoiceBench PC와 Claude PC의 네트워크 프로필은 `Private`여야 한다.
- API 키는 Claude Desktop 설정 파일에만 넣고 Git에 커밋하지 않는다.
- 공유기 포트 포워딩, DMZ, 공용 Wi-Fi에서는 사용하지 않는다.
- 필요 없을 때는 `stop-hosting.cmd`로 서비스를 종료한다.
