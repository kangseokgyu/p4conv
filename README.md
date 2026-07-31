# Pocket 4 Converter

이 프로젝트는 Pocket 4에 저장된 `mp4` 파일을 웹 화면에서 골라 `mov`로 변환하고, 변환된 파일을 현재 작업 디렉토리에 저장한 뒤 Photos 앱에 캡션과 함께 추가하는 도구입니다.

## 사용법

```bash
git clone https://github.com/kangseokgyu/p4conv.git
cd p4conv
python3 ./convert_pocket4_videos.py
```

실행하면 기본 브라우저에 로컬 웹 화면이 열립니다. 저장된 `mp4` 목록에서 체크박스로 파일을 선택하고 **선택한 파일 변환**을 누르세요.

- 파일명, 수정일, 길이, 파일 크기와 파일별 처리 상태를 한 목록에서 확인할 수 있습니다.
- 선택한 파일은 인코딩, YouTube 업로드, Photos 추가 단계를 고정된 순서로 실시간 표시합니다.
- 작업 로그는 `작업 로그 보기`를 눌렀을 때만 펼쳐집니다.
- 기본 주소는 `http://127.0.0.1:8765`이며, 종료하려면 터미널에서 `Ctrl+C`를 누릅니다.

## 동작

- 출력 파일은 현재 작업 디렉토리에 같은 이름의 `.mov`로 저장됩니다.
- 이미 같은 이름의 `.mov`가 있으면 다시 변환하지 않습니다.
- 변환 시 해상도는 1080p 이내로 맞추고, 영상 코덱은 macOS 하드웨어 HEVC(`hevc_videotoolbox`), 오디오는 AAC로 인코딩합니다.
- 변환 진행 시 웹 화면에 영상별 실시간 진행률(인코딩, YouTube 업로드)과 현재 작업 내용(캡션 설정, Photos 추가 등)이 표시됩니다.
- 변환된 파일의 수정 시간은 원본 파일과 같게 맞춥니다.
- Photos 앱으로 import할 때 캡션은 파일 수정 시간을 기준으로 `2026-03-19`를 `1`로 시작하는 숫자만 자동 입력합니다.
- Photos 앱으로 정상적으로 가져오기(import)가 완료되면, 디바이스 용량 절약을 위해 변환 완료된 `.mov` 파일은 자동으로 삭제됩니다. (가져오기에 실패한 경우 파일 보호를 위해 삭제되지 않습니다)

## 옵션

- `--volume-root`: Pocket 4 마운트 경로를 탐색할 최상위 디렉토리
- `--source-root`: 추가 소스 루트 지정
- `--dry-run`: 실제 변환 대신 명령만 출력
- `--no-import-photos`: Photos import 생략
- `--no-upload-youtube`: YouTube 업로드 생략
- `--port`: 웹 화면에 사용할 로컬 포트 지정 (기본값: `8765`)
- `--no-open-browser`: 브라우저를 자동으로 열지 않음
- `--cli`: 기존 터미널 파일 선택 UI 사용

## 가상환경 사용

프로젝트에 맞는 Python을 쓰고 싶다면 checkout 받은 뒤 원하는 가상환경에서 실행하세요.

```bash
cd p4conv
source .venv/bin/activate
python ./convert_pocket4_videos.py
```
