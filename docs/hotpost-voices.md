# Hotpost에서 공식 목소리 선택

2026-10-05 정책: Hotpost 자동 제작은 기존 개인 복제 기본 음성만 사용한다. 공식 화자는 제작실에서 직접 선택한 수동 시험에 사용한다. `/v1/health`의 `default_voice_kind`와 `ready`를 함께 검사하며, 개인 참조나 복제 모델이 없으면 다른 목소리로 대체하지 않는다. 준비 부족은 목소리 선택 화면과 제작 서비스 점검에 표시한다. 이 정책은 VoiceBench의 선택된 엔진·참조 녹음·Seed·HQ 설정을 바꾸지 않는다.

Hotpost의 목소리 선택은 엔진 설정 변경과 분리한다. 사용자는 서버가 공개한 `voice_id`만 선택하며, 모델·참조 녹음·Seed 13·HQ 정책은 VoiceBench가 결정한다. 기존 개인 목소리와 서버 기본값은 변경하지 않는다.

Qwen CustomVoice는 공식 화자 9종(여성 4, 남성 5)을 지원한다. ZONOS2는 공식 참조 녹음 3종(여성 2, 남성 1)을 사용한다. ZONOS2의 참조 녹음과 한국어 생성 샘플을 구별해 표시한다. 미리듣기 GET은 저장된 파일만 반환하며 GPU 작업을 새로 생성하지 않는다.

## PC별 설치

모델과 비밀 키는 Git으로 복사하지 않는다. `.runtime`과 `data`는 각 PC에서 유지한다. C 드라이브 공간이 부족하면 `VOICEBENCH_MODEL_CACHE`를 다른 드라이브로 지정할 수 있다. 런타임·도구 디렉터리를 junction으로 연결한 경우 Windows Installer에는 실제 경로를 사용한다.

- Qwen: 기존 Qwen 런타임에서 `tools/register_voice_gallery.py --download`로 CustomVoice를 설치한다.
- ZONOS2: 전용 Python 런타임의 의존성을 준비하고 `tools/download_fish_zonos.py zonos2 --quant q4_k --skip-prebuilt`로 공식 모델·인코더·참조 녹음을 받는다. 기본 `--quant`는 기존 Q8이므로 충분한 GPU 메모리가 있는 기존 PC는 그대로 사용할 수 있다.
- `build-zonos2-cuda.ps1 -Architecture 61`은 GTX 1070 Ti용 CUDA 빌드다. 다른 GPU에서는 해당 SM 번호를 사용한다.
- `tools/register_fish_zonos.py zonos2 --quant q4_k`는 별도 Q4 엔진을 등록한다. 기존 Q8 등록과 서버 기본값을 덮어쓰지 않는다. Q8이 없는 PC에서는 공식 목소리 카탈로그가 설치된 경량 엔진을 사용한다.
- 서버를 잠시 중지하고 `tools/prepare_voice_gallery.py --ids 목소리ID ...`로 한국어 미리듣기를 만든다. 생성 음성은 파형과 음성 인식 검사를 통과해야 캐시에 등록된다.

Hotpost와 VoiceBench 양쪽 서버에 변경된 코드를 적용해야 `/api/studio/voices` → 인증된 `/v1/voices` 연결이 동작한다. 브라우저에는 API 키가 전달되지 않는다.

## 생성과 재사용

동일 대본도 선택한 목소리가 다르면 별도 생성 요청·캐시를 사용한다. 응답의 `voice_id`가 요청과 다르면 Hotpost가 결과 다운로드를 거부한다. 목소리 변경은 저장된 제작 버전의 입력으로 고정되며 새 음성·편집을 생성한다. 기존 완료본은 보존한다. 빈 목소리 ID는 서버 기본값으로 되돌리는 명시적인 선택이다.

공식 자료: [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [ZONOS2](https://github.com/Zyphra/ZONOS2), [ZONOS2 GGUF 모델](https://huggingface.co/Zyphra/ZONOS2-GGUF).
