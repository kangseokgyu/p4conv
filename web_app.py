"""Local web interface for selecting and converting Pocket 4 videos."""

import json
import shutil
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

from convert_pocket4_videos import (
    ProgressReporter,
    collect_mp4_files,
    find_pocket4_mounts,
    format_duration,
    format_file_size,
    probe_video_duration,
    process_files,
)


class ConversionState:
    """Thread-safe state shared by the conversion worker and HTTP handlers."""

    def __init__(self, args: Any) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.files: List[Path] = []
        self.running = False
        self.logs: List[str] = []
        self.items: Dict[str, Dict[str, Any]] = {}
        self.summary = "파일을 선택한 뒤 변환을 시작하세요."

    def roots(self) -> List[Path]:
        roots = find_pocket4_mounts(Path(self.args.volume_root))
        roots.extend(Path(root) for root in self.args.source_root)
        return roots

    def refresh_files(self) -> List[Dict[str, Any]]:
        roots = self.roots()
        files = collect_mp4_files(roots) if roots else []
        with self.lock:
            self.files = files
            if not self.running:
                self.summary = f"{len(files)}개 MP4 파일을 찾았습니다." if files else "MP4 파일을 찾지 못했습니다."
        return self.file_payload(files)

    def file_payload(self, files: List[Path]) -> List[Dict[str, Any]]:
        payload = []
        for index, path in enumerate(files):
            try:
                stat = path.stat()
            except OSError:
                continue
            duration = probe_video_duration(path)
            payload.append({
                "id": index,
                "name": path.name,
                "path": str(path),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": format_file_size(stat.st_size),
                "duration": format_duration(duration) if duration is not None else "알 수 없음",
                "alreadyConverted": (Path.cwd() / path.with_suffix(".mov").name).exists(),
            })
        return payload

    def add_log(self, message: str) -> None:
        with self.lock:
            self.logs.append(message)
            self.logs = self.logs[-100:]

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return {"running": self.running, "summary": self.summary, "items": list(self.items.values()), "logs": self.logs[-20:]}


class WebProgressReporter(ProgressReporter):
    """Expose conversion progress through the shared web state instead of stdout."""

    def __init__(self, state: ConversionState, selected_files: List[Path]) -> None:
        super().__init__(enabled=False)
        self.state = state
        self.selected_files = selected_files
        self.current_id = ""

    def log(self, message: str) -> None:
        self.state.add_log(message)

    def start_file(self, index: int, total: int, file_name: str) -> None:
        self.current_id = str(self.selected_files[index - 1])
        with self.state.lock:
            self.state.items[self.current_id] = {"name": file_name, "status": "변환 준비 중", "encoding": 0, "upload": 0 if self.upload_enabled else None}
            self.state.summary = f"{index}/{total}개 파일 처리 중"

    def _update(self, status: str, **values: Any) -> None:
        with self.state.lock:
            item = self.state.items.get(self.current_id)
            if item:
                item["status"] = status
                item.update(values)

    def render(self, phase: str) -> None:
        self._update(phase)

    def update_upload(self, raw_percent: int) -> None:
        self._update("YouTube 업로드 중", upload=max(0, min(100, raw_percent)))

    def update_encoding(self, raw_percent: int) -> None:
        self._update("인코딩 중", encoding=max(0, min(100, raw_percent)))

    def finish_encoding(self) -> None:
        self._update("인코딩 완료", encoding=100)

    def finish_upload(self, success: bool) -> None:
        self._update("업로드 완료" if success else "업로드 실패", upload=100 if success else 0)

    def mark_caption(self, caption: str) -> None:
        self._update(f"캡션 설정: {caption}")

    def mark_photos(self, success: bool) -> None:
        self._update("Photos 추가 완료" if success else "Photos 추가 실패")

    def complete(self) -> None:
        self._update("완료", encoding=100, upload=100 if self.upload_enabled else None)

    def skip_existing(self) -> None:
        self._update("이미 변환됨 - 건너뜀")


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pocket 4 Converter</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#18212f;background:#f4f7fb}body{max-width:1050px;margin:0 auto;padding:36px 20px}h1{margin:0;font-size:29px}p{color:#596579}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:24px 0 14px}button{border:0;border-radius:8px;padding:10px 15px;font-size:14px;font-weight:650;cursor:pointer;background:#e5eaf2;color:#172033}button.primary{background:#2563eb;color:white}button:disabled{opacity:.5;cursor:not-allowed}.card{background:white;border:1px solid #dce3ef;border-radius:12px;overflow:hidden;box-shadow:0 1px 2px #18212f08}.status{font-weight:600;margin-left:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:13px 12px;border-bottom:1px solid #edf0f5;font-size:14px}th{background:#f8fafc;color:#526074;font-size:12px}tr:last-child td{border-bottom:0}.muted{color:#708097}.notice{padding:40px;text-align:center}.progress{height:7px;background:#e6ebf2;border-radius:99px;overflow:hidden;min-width:95px}.progress i{display:block;height:100%;background:#2563eb;border-radius:99px}.hidden{display:none}#jobs{margin-top:24px}#logs{white-space:pre-wrap;background:#111827;color:#d1d5db;border-radius:10px;padding:14px;min-height:35px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace;max-height:220px;overflow:auto}@media(max-width:700px){body{padding:24px 12px}th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){display:none}.status{margin-left:0}}
</style></head><body><h1>Pocket 4 Converter</h1><p>변환할 MP4를 선택하면 인코딩, YouTube 업로드, Photos 추가 진행 상황을 이 화면에서 확인할 수 있습니다.</p><div class="toolbar"><button onclick="loadFiles()">파일 새로고침</button><button id="all" onclick="toggleAll()">전체 선택</button><button id="start" class="primary" onclick="start()">선택한 파일 변환</button><span id="status" class="status">불러오는 중…</span></div><div class="card"><table><thead><tr><th></th><th>파일</th><th>수정일</th><th>길이</th><th>크기</th><th>상태</th></tr></thead><tbody id="files"></tbody></table><div id="empty" class="notice hidden">연결된 Pocket 4 또는 지정한 소스 경로에서 MP4 파일을 찾지 못했습니다.</div></div><section id="jobs" class="hidden"><h2>진행 상황</h2><div class="card"><table><thead><tr><th>파일</th><th>상태</th><th>인코딩</th><th>YouTube</th></tr></thead><tbody id="jobRows"></tbody></table></div><h2>작업 로그</h2><div id="logs"></div></section><script>
let files=[];const $=id=>document.getElementById(id);const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options){const r=await fetch(path,options);if(!r.ok)throw new Error(await r.text());return r.json()}
async function loadFiles(){try{files=await api('/api/files');$('files').innerHTML=files.map(f=>`<tr><td><input type="checkbox" value="${f.id}" ${f.alreadyConverted?'disabled':''}></td><td><strong>${esc(f.name)}</strong><div class="muted">${esc(f.path)}</div></td><td>${f.modified}</td><td>${f.duration}</td><td>${f.size}</td><td>${f.alreadyConverted?'<span class="muted">이미 변환됨</span>':'대기 중'}</td></tr>`).join('');$('empty').classList.toggle('hidden',files.length>0);$('status').textContent=`${files.length}개 파일`; }catch(e){$('status').textContent='파일 목록 오류: '+e.message}}
function toggleAll(){const boxes=[...document.querySelectorAll('#files input:not(:disabled)')],on=boxes.some(x=>!x.checked);boxes.forEach(x=>x.checked=on);$('all').textContent=on?'선택 해제':'전체 선택'}
async function start(){const selected=[...document.querySelectorAll('#files input:checked')].map(x=>Number(x.value));if(!selected.length){alert('변환할 파일을 선택하세요.');return}try{await api('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected})});$('start').disabled=true;$('jobs').classList.remove('hidden');poll()}catch(e){alert(e.message)}}
const bar=v=>v===null?'<span class="muted">사용 안 함</span>':`<div class="progress"><i style="width:${v}%"></i></div><span class="muted">${v}%</span>`;
async function poll(){try{const s=await api('/api/status');$('status').textContent=s.summary;$('jobRows').innerHTML=s.items.map(i=>`<tr><td>${esc(i.name)}</td><td>${esc(i.status)}</td><td>${bar(i.encoding)}</td><td>${bar(i.upload)}</td></tr>`).join('');$('logs').textContent=s.logs.join('\n');$('jobs').classList.toggle('hidden',!s.items.length);$('start').disabled=s.running;if(s.running){setTimeout(poll,500)}else{loadFiles()}}catch(e){$('status').textContent='상태 확인 오류: '+e.message}}
loadFiles();poll();
</script></body></html>"""


def make_handler(state: ConversionState, ffmpeg_path: str) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to this server's conversion state."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/files":
                self.send_json(state.file_payload(state.files) if state.running else state.refresh_files())
            elif self.path == "/api/status":
                self.send_json(state.status())
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path != "/api/start":
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                selected_ids = json.loads(self.rfile.read(length)).get("selected", [])
                selected = [state.files[int(index)] for index in selected_ids]
            except (IndexError, TypeError, ValueError, json.JSONDecodeError):
                self.send_json({"error": "올바른 파일 선택이 아닙니다."}, HTTPStatus.BAD_REQUEST)
                return
            with state.lock:
                if state.running:
                    self.send_json({"error": "이미 변환 작업이 진행 중입니다."}, HTTPStatus.CONFLICT)
                    return
                if not selected:
                    self.send_json({"error": "선택한 파일이 없습니다."}, HTTPStatus.BAD_REQUEST)
                    return
                state.running, state.items, state.logs, state.summary = True, {}, [], "변환 작업을 시작합니다."

            def worker() -> None:
                reporter = WebProgressReporter(state, selected)
                try:
                    converted, skipped, imported = process_files(selected, ffmpeg_path, dry_run=state.args.dry_run, import_photos=not state.args.no_import_photos, import_youtube=not state.args.no_upload_youtube, output_dir=Path.cwd(), reporter=reporter)
                    with state.lock:
                        state.summary = f"완료: 변환 {converted}개, 건너뜀 {skipped}개, Photos 추가 {imported}개"
                except Exception as exc:
                    state.add_log(f"오류: {exc}")
                    with state.lock:
                        state.summary = "작업 중 오류가 발생했습니다. 로그를 확인하세요."
                finally:
                    with state.lock:
                        state.running = False

            threading.Thread(target=worker, daemon=True).start()
            self.send_json({"ok": True})

    return Handler


def run_web_app(args: Any) -> int:
    """Start the local web UI and keep it available until interrupted."""
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        print("Error: ffmpeg is not installed or not on PATH.")
        return 1
    state = ConversionState(args)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state, ffmpeg_path))
    except OSError as exc:
        print(f"Error: could not start the web server on port {args.port}: {exc}")
        return 1
    address = f"http://127.0.0.1:{server.server_port}"
    print(f"Pocket 4 Converter web interface: {address}")
    print("Press Ctrl+C to stop the server.")
    if not args.no_open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb server stopped.")
    finally:
        server.server_close()
    return 0
