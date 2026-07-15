# Pocket 4 Converter

이 프로젝트는 Pocket 4에 저장된 `mp4` 파일을 하나씩 골라 `mov`로 변환하고, 변환된 파일을 현재 작업 디렉토리에 저장한 뒤 Photos 앱에 캡션과 함께 추가하는 도구입니다.

## 사용법

```bash
git clone https://github.com/kangseokgyu/p4conv.git
cd p4conv
python3 ./convert_pocket4_videos.py
```

실행하면 저장된 `mp4` 목록이 파일명, 생성 날짜, 길이, 파일 크기와 함께 표시됩니다.

- 번호를 입력하면 해당 파일만 변환합니다.
- 그냥 Enter를 누르면 전체 파일을 처리합니다.

## 동작

- 출력 파일은 현재 작업 디렉토리에 같은 이름의 `.mov`로 저장됩니다.
- 이미 같은 이름의 `.mov`가 있으면 다시 변환하지 않습니다.
- 변환 시 해상도는 1080p 이내로 맞추고, 영상 코덱은 macOS 하드웨어 HEVC(`hevc_videotoolbox`), 오디오는 AAC로 인코딩합니다.
- 변환 진행 시 터미널에 영상별 실시간 진행률 바(Progress Bar)와 현재 진행 중인 작업 내용(인코딩, 캡션 설정, Photos 추가 등)이 표시됩니다.
- 변환된 파일의 수정 시간은 원본 파일과 같게 맞춥니다.
- Photos 앱으로 import할 때 캡션은 파일 수정 시간을 기준으로 `2026-03-19`를 `1`로 시작하는 숫자만 자동 입력합니다.

## 옵션

- `--volume-root`: Pocket 4 마운트 경로를 탐색할 최상위 디렉토리
- `--source-root`: 추가 소스 루트 지정
- `--dry-run`: 실제 변환 대신 명령만 출력
- `--no-import-photos`: Photos import 생략

## 가상환경 사용

프로젝트에 맞는 Python을 쓰고 싶다면 checkout 받은 뒤 원하는 가상환경에서 실행하세요.

```bash
cd p4conv
source .venv/bin/activate
python ./convert_pocket4_videos.py
```
