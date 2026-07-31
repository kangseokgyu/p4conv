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
        with self.lock:
            items = {key: dict(value) for key, value in self.items.items()}
        for index, path in enumerate(files):
            try:
                stat = path.stat()
            except OSError:
                continue
            duration = probe_video_duration(path)
            item = items.get(str(path), {})
            payload.append({
                "id": index,
                "name": path.name,
                "path": str(path),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": format_file_size(stat.st_size),
                "duration": format_duration(duration) if duration is not None else "알 수 없음",
                "alreadyConverted": (Path.cwd() / path.with_suffix(".mov").name).exists(),
                "progress": item,
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
            self.state.items[self.current_id] = {
                "name": file_name,
                "status": "처리 중",
                "encoding": 0,
                "upload": 0 if self.upload_enabled else None,
                "photos": "대기",
            }
            self.state.summary = f"{index}/{total}개 파일 처리 중"

    def _update(self, status: str | None = None, **values: Any) -> None:
        with self.state.lock:
            item = self.state.items.get(self.current_id)
            if item:
                if status is not None:
                    item["status"] = status
                item.update(values)

    def render(self, phase: str) -> None:
        self._update(phase)

    def update_upload(self, raw_percent: int) -> None:
        self._update(upload=max(0, min(100, raw_percent)))

    def update_encoding(self, raw_percent: int) -> None:
        self._update(encoding=max(0, min(100, raw_percent)))

    def finish_encoding(self) -> None:
        self._update(encoding=100)

    def finish_upload(self, success: bool) -> None:
        self._update(upload=100 if success else 0, uploadFailed=not success)

    def mark_caption(self, caption: str) -> None:
        self._update(caption=caption)

    def mark_photos(self, success: bool) -> None:
        self._update(photos="완료" if success else "실패")

    def complete(self) -> None:
        self._update("완료", encoding=100, upload=100 if self.upload_enabled else None)

    def skip_existing(self) -> None:
        self._update("이미 변환됨 - 건너뜀")


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pocket 4 Converter</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#f8fafc;background:#0f172a;font-size:14px}body{max-width:1280px;margin:0 auto;padding:28px 24px}h1{margin:0;font-size:26px;letter-spacing:-.5px;color:#f8fafc;font-weight:700}p{margin:6px 0 0;color:#94a3b8}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:20px 0 12px}button{border:1px solid #334155;border-radius:7px;padding:8px 12px;font:600 13px inherit;cursor:pointer;background:#1e293b;color:#f8fafc;transition:background .15s,border-color .15s}button:hover:not(:disabled){background:#334155;border-color:#475569}button.primary{background:#2563eb;border-color:#2563eb;color:#fff}button.primary:hover:not(:disabled){background:#1d4ed8;border-color:#1d4ed8}button:disabled{opacity:.4;cursor:not-allowed}.spacer{flex:1}.status{font-weight:600;color:#cbd5e1}.card{background:#1e293b;border:1px solid #334155;border-radius:10px;overflow:auto;box-shadow:0 4px 6px -1px #0000004d,0 2px 4px -2px #0000004d}table{width:100%;min-width:940px;border-collapse:collapse}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #334155;vertical-align:middle}th{background:#0f172a;color:#94a3b8;font-size:11px;letter-spacing:.05em;text-transform:uppercase;font-weight:700}th:last-child{width:320px}tr:last-child td{border-bottom:0}tr:hover td{background:#273549}input[type="checkbox"]{width:16px;height:16px;accent-color:#3b82f6;cursor:pointer}.file-name{font-weight:600;color:#f8fafc}.muted{color:#94a3b8}.notice{padding:40px;text-align:center;color:#94a3b8}.hidden{display:none}.badge{display:inline-flex;align-items:center;white-space:nowrap;border-radius:99px;padding:4px 10px;background:#334155;color:#cbd5e1;font-size:12px;font-weight:600}.badge.done{background:#064e3b;color:#34d399}.progress-line{display:flex;align-items:center;gap:8px;min-width:295px;white-space:nowrap}.status-mark{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:700;color:#94a3b8}.status-mark i{display:grid;place-items:center;width:20px;height:20px;border-radius:50%;font-size:11px;font-style:normal;background:#334155;color:#cbd5e1}.status-mark.active{color:#60a5fa}.status-mark.active i{background:#1e3a8a;color:#60a5fa}.status-mark.done{color:#34d399}.status-mark.done i{background:#064e3b;color:#34d399}.status-mark.fail{color:#f87171}.status-mark.fail i{background:#7f1d1d;color:#f87171}.mini-steps{display:flex;align-items:center;gap:5px}.mini-step{display:inline-flex;align-items:center;gap:5px;padding:4px 7px;border-radius:6px;background:#0f172a;color:#94a3b8;font-size:11px;font-weight:600;border:1px solid #334155}.mini-step b{font-weight:700;color:#cbd5e1}.mini-step .bar{width:30px;height:5px;background:#334155;border-radius:99px;overflow:hidden}.mini-step .bar i{display:block;height:100%;background:#64748b;border-radius:99px}.mini-step.active{background:#1e3a8a;color:#93c5fd;border-color:#2563eb}.mini-step.active b{color:#fff}.mini-step.active .bar i{background:#3b82f6}.mini-step.done{background:#064e3b;color:#6ee7b7;border-color:#059669}.mini-step.done b{color:#fff}.mini-step.done .bar i{background:#10b981}.mini-step.fail{background:#7f1d1d;color:#fca5a5;border-color:#dc2626}.mini-step.fail b{color:#fff}.mini-step.fail .bar i{background:#ef4444}.mini-step.skip{color:#64748b;border-color:transparent}.logs{margin-top:16px}.logs summary{cursor:pointer;color:#94a3b8;font-size:13px;font-weight:600}.logs summary:hover{color:#f8fafc}.logs pre{white-space:pre-wrap;background:#090d16;color:#cbd5e1;border:1px solid #334155;border-radius:8px;padding:12px;margin:8px 0 0;max-height:220px;overflow:auto;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}@media(max-width:760px){body{padding:20px 12px}.spacer{display:none}.status{width:100%;order:2}.toolbar{margin-top:16px}table{min-width:760px}}
</style></head><body><header><h1>Pocket 4 Converter</h1><p>파일 선택과 변환 진행을 한 화면에서 관리합니다.</p></header><div class="toolbar"><button onclick="loadFiles()">새로고침</button><button id="all" onclick="toggleAll()">전체 선택</button><button id="start" class="primary" onclick="start()">선택한 파일 변환</button><span class="spacer"></span><span id="status" class="status">불러오는 중…</span></div><div class="card"><table><thead><tr><th></th><th>파일</th><th>수정일</th><th>길이</th><th>크기</th><th>진행 상태</th></tr></thead><tbody id="files"></tbody></table><div id="empty" class="notice hidden">연결된 Pocket 4 또는 지정한 소스 경로에서 MP4 파일을 찾지 못했습니다.</div></div><details id="logPanel" class="logs hidden"><summary>작업 로그 보기</summary><pre id="logs"></pre></details><script>
let files=[];const $=id=>document.getElementById(id);const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options){const r=await fetch(path,options);if(!r.ok)throw new Error(await r.text());return r.json()}
const step=(label,value,state='',text=`${value}%`)=>`<span class="mini-step ${state}"><b>${label}</b><span class="bar"><i style="width:${value}%"></i></span><span>${text}</span></span>`;
function progress(f){const p=f.progress||{};if(!Object.keys(p).length){return f.alreadyConverted?'<span class="badge done">✓ 완료됨</span>':'<span class="badge">○ 대기 중</span>'}const active=p.status==='처리 중';const upload=p.upload===null?'<span class="mini-step skip"><b>YouTube</b><span>대기</span></span>':step('YouTube',p.upload||0,p.uploadFailed?'fail':p.upload===100?'done':active?'active':'');const photos=p.photos==='완료'?step('Photos',100,'done','완료'):p.photos==='실패'?step('Photos',100,'fail','실패'):step('Photos',0,'','대기');const state=p.status==='완료'?'<span class="status-mark done"><i>✓</i>완료</span>':active?'<span class="status-mark active"><i>↻</i>처리 중</span>':'<span class="status-mark"><i>○</i>대기</span>';return `<div class="progress-line">${state}<div class="mini-steps">${step('인코딩',p.encoding||0,p.encoding===100?'done':active?'active':'')}${upload}${photos}</div></div>`}
function renderFiles(){ $('files').innerHTML=files.map(f=>`<tr><td><input type="checkbox" value="${f.id}" ${f.alreadyConverted?'disabled':''}></td><td><div class="file-name">${esc(f.name)}</div></td><td>${f.modified}</td><td>${f.duration}</td><td>${f.size}</td><td>${progress(f)}</td></tr>`).join('');$('empty').classList.toggle('hidden',files.length>0) }
async function loadFiles(preserveStatus=false){try{files=await api('/api/files');renderFiles();if(!preserveStatus)$('status').textContent=`${files.length}개 파일`; }catch(e){$('status').textContent='파일 목록 오류: '+e.message}}
function toggleAll(){const boxes=[...document.querySelectorAll('#files input:not(:disabled)')],on=boxes.some(x=>!x.checked);boxes.forEach(x=>x.checked=on);$('all').textContent=on?'선택 해제':'전체 선택'}
async function start(){const selected=[...document.querySelectorAll('#files input:checked')].map(x=>Number(x.value));if(!selected.length){alert('변환할 파일을 선택하세요.');return}try{await api('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected})});$('start').disabled=true;poll()}catch(e){alert(e.message)}}
async function poll(){try{const s=await api('/api/status');$('status').textContent=s.summary;$('logs').textContent=s.logs.join('\n');$('logPanel').classList.toggle('hidden',!s.logs.length);$('start').disabled=s.running;await loadFiles(true);if(s.running){setTimeout(poll,500)}}catch(e){$('status').textContent='상태 확인 오류: '+e.message}}
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
                state.running = True
                state.items = {
                    str(path): {
                        "name": path.name,
                        "status": "대기 중",
                        "encoding": 0,
                        "upload": None,
                        "photos": "대기",
                    }
                    for path in selected
                }
                state.logs = []
                state.summary = "변환 작업을 시작합니다."

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
