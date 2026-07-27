import argparse
import os
import sys
import threading
from datetime import date, datetime
import re
import subprocess
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    _YOUTUBE_AVAILABLE = True
except ImportError:
    _YOUTUBE_AVAILABLE = False


POCKET4_VOLUME_PATTERN = re.compile(r"pocket\s*4", re.IGNORECASE)
DEFAULT_VOLUME_ROOT = Path("/Volumes")
CAPTION_BASE_DATE = date(2026, 3, 19)
PROGRESS_BAR_WIDTH = 20

YOUTUBE_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"


def find_pocket4_mounts(volume_root: Path = DEFAULT_VOLUME_ROOT) -> List[Path]:
    """Return mounted volumes whose names look like a Pocket 4 device."""
    if not volume_root.exists():
        return []

    matches = []
    for entry in sorted(volume_root.iterdir()):
        if not entry.is_dir():
            continue
        if POCKET4_VOLUME_PATTERN.search(entry.name):
            matches.append(entry)
    return matches


def find_mp4_files(root: Path) -> List[Path]:
    """Recursively collect every mp4 file below a root directory."""
    files = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".mp4"):
                        files.append(Path(entry.path))
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            continue
    return sorted(files)


def collect_mp4_files(roots: List[Path]) -> List[Path]:
    """Merge all mp4 files from every detected root into one sorted list."""
    files = []
    for root in roots:
        files.extend(find_mp4_files(root))
    return sorted(files)


def format_file_size(num_bytes: int) -> str:
    """Format byte counts into human-readable units like MB or GB."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as mm:ss or h:mm:ss."""
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def probe_video_duration(path: Path) -> Optional[float]:
    """Ask ffprobe for the video duration, if it is available."""
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        return None

    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError:
        return None

    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def describe_mp4_file(path: Path) -> str:
    """Build the display string shown in the selection list."""
    mtime_str = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    size_label = format_file_size(path.stat().st_size)
    duration_seconds = probe_video_duration(path)
    if duration_seconds is None:
        return f"{path.name} | {mtime_str} | {size_label}"
    duration_label = format_duration(duration_seconds)
    return f"{path.name} | {mtime_str} | {duration_label} | {size_label}"


def display_mp4_files(files: List[Path]) -> None:
    """Print the selectable mp4 list with metadata for the user."""
    print("\nAvailable MP4 files:")
    for index, file_path in enumerate(files, start=1):
        print(f"{index:>3}. {describe_mp4_file(file_path)}")


def parse_selection(selection: str, total: int) -> List[int]:
    """Parse a comma-separated selection like 1,3,5-7 into zero-based indexes."""
    selection = selection.strip()
    if not selection or selection.lower() in {"all", "a"}:
        return list(range(total))

    chosen = []
    seen = set()

    for chunk in selection.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            for number in range(start, end + 1):
                if 1 <= number <= total and number not in seen:
                    seen.add(number)
                    chosen.append(number - 1)
        else:
            number = int(part)
            if 1 <= number <= total and number not in seen:
                seen.add(number)
                chosen.append(number - 1)

    return chosen


def prompt_for_mp4_files(files: List[Path]) -> List[Path]:
    """Prompt interactively unless stdin is already redirected."""
    if not sys.stdin.isatty():
        return files

    display_mp4_files(files)
    print("\nEnter file numbers to convert.")
    print("Press Enter to convert all files.")
    print("You can also enter values like: 1,3,5-7")

    while True:
        selection = input("Selection: ")
        try:
            indexes = parse_selection(selection, len(files))
        except ValueError:
            print("Invalid selection. Please try again.")
            continue

        if not indexes:
            print("No valid files selected. Please try again.")
            continue

        return [files[index] for index in indexes]


def build_progress_bar(percent: int) -> str:
    """Build a fixed-width text progress bar."""
    percent = max(0, min(100, percent))
    filled = int(PROGRESS_BAR_WIDTH * percent / 100)
    filled = max(0, min(PROGRESS_BAR_WIDTH, filled))
    return f"[{'█' * filled}{'░' * (PROGRESS_BAR_WIDTH - filled)}]"


def parse_ffmpeg_timecode(timecode: str) -> float:
    """Convert an ffmpeg HH:MM:SS.microseconds timecode into seconds."""
    try:
        hours, minutes, seconds = timecode.strip().split(":")
        return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
    except (ValueError, AttributeError):
        return 0.0


class ProgressReporter:
    """Render per-file progress bars with thread-safe dual progress display."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.current_label = ""
        self.upload_enabled = False
        self.upload_percent = 0
        self.encode_percent = 0
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        """Write a message to stdout safely, clearing the progress line first if enabled."""
        with self._lock:
            if self.enabled:
                sys.stdout.write("\r\033[K" + message + "\n")
                self._redraw_unlocked()
            else:
                print(message)

    def start_file(self, index: int, total: int, file_name: str) -> None:
        """Begin reporting for a file."""
        with self._lock:
            self.current_label = f"[{index}/{total}] {file_name}"
            self.upload_percent = 0
            self.encode_percent = 0
            if self.enabled:
                self._render_unlocked("시작")
            else:
                print(f"{self.current_label} - 시작")

    def render(self, phase: str) -> None:
        """Render a progress update for the current file (thread-safe)."""
        with self._lock:
            self._render_unlocked(phase)

    def _render_unlocked(self, phase: str) -> None:
        """Render progress without acquiring the lock (caller must hold _lock)."""
        if self.enabled:
            if self.upload_enabled:
                upload_bar = build_progress_bar(self.upload_percent)
                encode_bar = build_progress_bar(self.encode_percent)
                line = (
                    f"{self.current_label}"
                    f" | 업로드 {upload_bar} {self.upload_percent:3d}%"
                    f" | 인코딩 {encode_bar} {self.encode_percent:3d}%"
                    f" | {phase}"
                )
            else:
                encode_bar = build_progress_bar(self.encode_percent)
                line = (
                    f"{self.current_label}"
                    f" | 인코딩 {encode_bar} {self.encode_percent:3d}%"
                    f" | {phase}"
                )
            sys.stdout.write("\r\033[K" + line)
            sys.stdout.flush()
        else:
            print(f"{self.current_label} - {phase}")

    def _redraw_unlocked(self) -> None:
        """Redraw the progress bar after a log line (caller must hold _lock)."""
        if not self.enabled:
            return
        if self.upload_enabled:
            upload_bar = build_progress_bar(self.upload_percent)
            encode_bar = build_progress_bar(self.encode_percent)
            line = (
                f"{self.current_label}"
                f" | 업로드 {upload_bar} {self.upload_percent:3d}%"
                f" | 인코딩 {encode_bar} {self.encode_percent:3d}%"
                f" |"
            )
        else:
            encode_bar = build_progress_bar(self.encode_percent)
            line = (
                f"{self.current_label}"
                f" | 인코딩 {encode_bar} {self.encode_percent:3d}%"
                f" |"
            )
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()

    def update_upload(self, raw_percent: int) -> None:
        """Update the upload progress (thread-safe, called from upload thread)."""
        with self._lock:
            self.upload_percent = max(0, min(100, raw_percent))
            self._render_unlocked("업로드")

    def update_encoding(self, raw_percent: int) -> None:
        """Update the encoding phase using the raw ffmpeg percent."""
        with self._lock:
            self.encode_percent = max(0, min(100, raw_percent))
            self._render_unlocked("인코딩")

    def finish_encoding(self) -> None:
        """Mark encoding as complete."""
        with self._lock:
            self.encode_percent = 100
            self._render_unlocked("인코딩 완료")

    def finish_upload(self, success: bool) -> None:
        """Mark upload as complete."""
        with self._lock:
            self.upload_percent = 100
            phase = "업로드 완료" if success else "업로드 실패"
            self._render_unlocked(phase)

    def mark_caption(self, caption: str) -> None:
        """Mark the caption step."""
        self.render(f"캡션: {caption}")

    def mark_photos(self, success: bool) -> None:
        """Mark the Photos import step."""
        self.render("Photos 추가" if success else "Photos 실패")

    def complete(self) -> None:
        """Mark the file as fully processed."""
        with self._lock:
            if self.enabled:
                if self.upload_enabled:
                    upload_bar = build_progress_bar(100)
                    encode_bar = build_progress_bar(100)
                    line = (
                        f"{self.current_label}"
                        f" | 업로드 {upload_bar} 100%"
                        f" | 인코딩 {encode_bar} 100%"
                        f" | 완료"
                    )
                else:
                    encode_bar = build_progress_bar(100)
                    line = (
                        f"{self.current_label}"
                        f" | 인코딩 {encode_bar} 100%"
                        f" | 완료"
                    )
                sys.stdout.write("\r\033[K" + line + "\n")
                sys.stdout.flush()
            else:
                print(f"{self.current_label} - 완료")

    def skip_existing(self) -> None:
        """Report that the output file already exists."""
        if self.enabled:
            self.render("이미 변환됨")
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            print(f"{self.current_label} - 이미 변환됨, 건너뜀")


class YouTubeUploader:
    """Manage YouTube API authentication and video uploads."""

    def __init__(
        self,
        client_secrets_path: Path,
        token_path: Path,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client_secrets_path = client_secrets_path
        self.token_path = token_path
        self._log_callback = log_callback
        self.service = None

    def log(self, message: str) -> None:
        """Write a log message using the callback or stdout."""
        if self._log_callback:
            self._log_callback(message)
        else:
            print(message)

    def authenticate(self) -> bool:
        """Authenticate with YouTube using OAuth 2.0 and build the API service."""
        if not _YOUTUBE_AVAILABLE:
            self.log("Warning: google-api-python-client 또는 google-auth-oauthlib이 설치되지 않았습니다.")
            self.log("  pip install google-api-python-client google-auth-oauthlib")
            return False

        if not self.client_secrets_path.exists():
            self.log(f"Warning: {self.client_secrets_path} 파일이 없습니다. YouTube 업로드를 건너뜁니다.")
            return False

        creds = None
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), YOUTUBE_UPLOAD_SCOPES)
            except Exception:
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_secrets_path), YOUTUBE_UPLOAD_SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception as exc:
                self.log(f"Warning: YouTube OAuth 인증 실패: {exc}")
                return False

        try:
            with open(self.token_path, "w") as token_file:
                token_file.write(creds.to_json())
        except OSError as exc:
            self.log(f"Warning: 토큰 저장 실패: {exc}")

        try:
            self.service = build("youtube", "v3", credentials=creds)
        except Exception as exc:
            self.log(f"Warning: YouTube API 서비스 초기화 실패: {exc}")
            return False

        self.log("YouTube 인증 완료.")
        return True

    def upload_video(
        self,
        video_path: Path,
        title: str,
        description: str,
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> bool:
        """Upload a video to YouTube as a private video with resumable upload."""
        if self.service is None:
            self.log("Warning: YouTube 서비스가 초기화되지 않았습니다.")
            return False

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private",
            },
        }

        media = MediaFileUpload(
            str(video_path),
            chunksize=10 * 1024 * 1024,
            resumable=True,
        )

        try:
            request = self.service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and on_progress:
                    percent = int(status.progress() * 100)
                    on_progress(percent)

            if on_progress:
                on_progress(100)

            video_id = response.get("id", "unknown")
            self.log(f"YouTube 업로드 완료: https://youtu.be/{video_id}")
            return True

        except Exception as exc:
            self.log(f"Warning: YouTube 업로드 실패: {exc}")
            return False


def build_ffmpeg_command(source: Path, target: Path, ffmpeg_path: str) -> List[str]:
    """Build the ffmpeg command for a fast HEVC 1080p transcode."""
    scale_filter = "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2"

    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:2",
        "-y",
        "-i",
        str(source),
        "-vf",
        scale_filter,
        "-c:v",
        "hevc_videotoolbox",
        "-tag:v",
        "hvc1",
        "-profile:v",
        "main",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(target),
    ]


def run_ffmpeg_with_progress(
    command: List[str],
    duration_seconds: Optional[float],
    on_progress: Optional[Callable[[int], None]] = None,
) -> None:
    """Run ffmpeg and stream progress information from stderr."""
    if duration_seconds is None or duration_seconds <= 0:
        completed = subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if on_progress is not None:
            on_progress(100)
        if completed.stderr:
            last_line = completed.stderr.strip().splitlines()[-1]
            if last_line and "=" not in last_line:
                raise subprocess.CalledProcessError(completed.returncode, command, output=completed.stdout, stderr=completed.stderr)
        return

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    if process.stderr is None:
        raise RuntimeError("Unable to read ffmpeg progress output.")

    log_lines = []
    last_reported_percent = -1

    try:
        while True:
            line = process.stderr.readline()
            if line == "" and process.poll() is not None:
                break
            if not line:
                continue

            stripped = line.strip()
            if not stripped:
                continue

            if "=" in stripped:
                key, value = stripped.split("=", 1)
                if key == "out_time":
                    elapsed_seconds = parse_ffmpeg_timecode(value)
                    raw_percent = int((elapsed_seconds / duration_seconds) * 100)
                    raw_percent = max(0, min(100, raw_percent))
                    if on_progress is not None and raw_percent != last_reported_percent:
                        last_reported_percent = raw_percent
                        on_progress(raw_percent)
                    continue
                if key == "progress" and value == "end":
                    if on_progress is not None:
                        on_progress(100)
                    continue

            log_lines.append(stripped)
    finally:
        process.wait()

    if process.returncode != 0:
        stderr_text = "\n".join(log_lines[-10:])
        raise subprocess.CalledProcessError(process.returncode, command, output=None, stderr=stderr_text)


def convert_mp4_to_mov(
    source: Path,
    ffmpeg_path: str,
    dry_run: bool = False,
    output_dir: Optional[Path] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Convert one mp4 into a mov file in the chosen output directory."""
    target_dir = output_dir or Path.cwd()
    target = target_dir / source.with_suffix(".mov").name

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if target.exists():
        log(f"Skip: already converted -> {target.name}")
        return False

    command = build_ffmpeg_command(source, target, ffmpeg_path)
    log(f"Convert: {source.name} -> {target.name}")

    if dry_run:
        log("  " + " ".join(command))
        return True

    duration_seconds = probe_video_duration(source)

    try:
        run_ffmpeg_with_progress(command, duration_seconds, on_progress=on_progress)
    except subprocess.CalledProcessError as exc:
        log(f"Failed: {source.name}")
        if exc.stderr:
            log(exc.stderr.strip().splitlines()[-1])
        return False

    try:
        source_stat = source.stat()
        os.utime(target, (source_stat.st_atime, source_stat.st_mtime))
    except OSError as exc:
        log(f"Warning: could not copy timestamps for {target.name}.")
        log(str(exc))

    return True


def quote_applescript_posix_path(path: Path) -> str:
    """Quote a file path so AppleScript can consume it as a POSIX file."""
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'POSIX file "{escaped}"'


def quote_applescript_text(text: str) -> str:
    """Quote plain text for use inside AppleScript literals."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def make_caption(source: Path) -> str:
    """Create the Photos caption from the source file date."""
    source_mtime = datetime.fromtimestamp(source.stat().st_mtime).date()
    delta_days = (source_mtime - CAPTION_BASE_DATE).days
    if delta_days >= 0:
        return f"{delta_days + 1}"
    return f"{delta_days}"


def make_youtube_description(source: Path, caption: str) -> str:
    """Create the YouTube video description from the source file metadata."""
    source_mtime = datetime.fromtimestamp(source.stat().st_mtime)
    date_str = source_mtime.strftime("%Y-%m-%d %H:%M:%S")
    return f"촬영일: {date_str}\nD+{caption}"


def import_into_photos(
    file_paths: List[Path],
    captions: Optional[List[str]] = None,
    dry_run: bool = False,
    log_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Import files into Photos and write each caption into the description field."""
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if not file_paths:
        return True

    if dry_run:
        log("Import to Photos:")
        for index, path in enumerate(file_paths):
            caption = captions[index] if captions and index < len(captions) else ""
            if caption:
                log(f"  {path} -> caption: {caption}")
            else:
                log(f"  {path}")
        return True

    if shutil.which("osascript") is None:
        log("Warning: osascript is not available, skipping Photos import.")
        return False

    apple_script_paths = ", ".join(quote_applescript_posix_path(path) for path in file_paths)
    apple_script_captions = ", ".join(quote_applescript_text(caption) for caption in (captions or []))
    script = (
        'tell application "Photos"\n'
        '    activate\n'
        f'    set importedItems to import {{{apple_script_paths}}}\n'
        f'    set importedCaptions to {{{apple_script_captions}}}\n'
        '    repeat with i from 1 to count of importedItems\n'
        '        if i is less than or equal to count of importedCaptions then\n'
        '            set description of item i of importedItems to item i of importedCaptions\n'
        '        end if\n'
        '    end repeat\n'
        'end tell'
    )

    try:
        subprocess.run(["osascript", "-e", script], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        log("Warning: Photos import failed.")
        if exc.stderr:
            log(exc.stderr.strip().splitlines()[-1])
        return False

    log(f"Imported {len(file_paths)} file(s) into Photos.")
    return True


def process_files(
    mp4_files: List[Path],
    ffmpeg_path: str,
    dry_run: bool = False,
    import_photos: bool = True,
    import_youtube: bool = True,
    output_dir: Optional[Path] = None,
    reporter: Optional["ProgressReporter"] = None,
) -> Tuple[int, int, int]:
    """Convert and import a selected batch of files one by one."""
    converted = 0
    skipped = 0
    imported = 0
    if not mp4_files:
        return 0, 0, 0

    reporter = reporter or ProgressReporter(enabled=sys.stdout.isatty() and not dry_run)
    reporter.log(f"Processing {len(mp4_files)} selected mp4 file(s)")

    # YouTube service initialization
    uploader = None
    if import_youtube:
        script_dir = Path(__file__).resolve().parent
        uploader = YouTubeUploader(
            client_secrets_path=script_dir / CLIENT_SECRETS_FILE,
            token_path=script_dir / TOKEN_FILE,
            log_callback=reporter.log,
        )
        if not uploader.authenticate():
            uploader = None

    total_files = len(mp4_files)
    for index, source in enumerate(mp4_files, start=1):
        target_dir = output_dir or Path.cwd()
        target = target_dir / source.with_suffix(".mov").name

        file_upload_enabled = uploader is not None
        reporter.upload_enabled = file_upload_enabled
        reporter.start_file(index, total_files, source.name)

        if target.exists():
            reporter.skip_existing()
            skipped += 1
            continue

        caption = make_caption(source)

        # Parallel: upload original MP4 to YouTube + encode to MOV
        upload_result = {"success": False}

        def _upload_task() -> None:
            """Background thread: upload original MP4 to YouTube."""
            yt_title = source.name
            yt_description = make_youtube_description(source, caption)
            if dry_run:
                reporter.log(f"YouTube 업로드 (dry-run): {source.name}")
                reporter.log(f"  제목: {yt_title}")
                reporter.log(f"  설명: {yt_description}")
                upload_result["success"] = True
                reporter.finish_upload(True)
                return
            upload_result["success"] = uploader.upload_video(
                source, yt_title, yt_description, on_progress=reporter.update_upload,
            )
            reporter.finish_upload(upload_result["success"])

        upload_thread = None
        if file_upload_enabled:
            upload_thread = threading.Thread(target=_upload_task, daemon=True)
            upload_thread.start()

        # Main thread: encode MP4 -> MOV
        changed = convert_mp4_to_mov(
            source,
            ffmpeg_path,
            dry_run=dry_run,
            output_dir=output_dir,
            on_progress=reporter.update_encoding,
            log_callback=reporter.log,
        )

        # Wait for upload thread to finish
        if upload_thread is not None:
            upload_thread.join()

        if changed:
            converted += 1
            reporter.finish_encoding()
            mov_path = target
            reporter.mark_caption(caption)
            if import_photos:
                if import_into_photos([mov_path], captions=[caption], dry_run=dry_run, log_callback=reporter.log):
                    reporter.mark_photos(True)
                    imported += 1
                    if not dry_run:
                        try:
                            mov_path.unlink()
                            reporter.log(f"Deleted temporary converted file: {mov_path.name}")
                        except OSError as exc:
                            reporter.log(f"Warning: could not delete temporary file {mov_path.name}: {exc}")
                    else:
                        reporter.log(f"Dry-run: Would delete temporary file: {mov_path.name}")
                else:
                    reporter.mark_photos(False)
        else:
            skipped += 1

        reporter.complete()

    return converted, skipped, imported


def parse_args() -> argparse.Namespace:
    """Parse command-line flags for the Pocket 4 converter."""
    parser = argparse.ArgumentParser(
        description="Convert mp4 files on a connected Pocket 4 device into 1080p mov files."
    )
    parser.add_argument(
        "--volume-root",
        default=str(DEFAULT_VOLUME_ROOT),
        help="Root directory to scan for mounted volumes (default: /Volumes).",
    )
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        help="Additional source roots to convert from. Can be used for testing or manual runs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ffmpeg command without converting files.",
    )
    parser.add_argument(
        "--no-import-photos",
        action="store_true",
        help="Skip importing newly converted MOV files into Photos.",
    )
    parser.add_argument(
        "--no-upload-youtube",
        action="store_true",
        help="Skip uploading original MP4 files to YouTube.",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Use the legacy terminal interface instead of the web interface.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Local port for the web interface (default: 8765).",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not automatically open the web interface in a browser.",
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point: locate files, prompt for selection, then process them."""
    args = parse_args()

    if not args.cli:
        from web_app import run_web_app

        return run_web_app(args)

    ffmpeg_path = shutil.which("ffmpeg")

    if not ffmpeg_path:
        print("Error: ffmpeg is not installed or not on PATH.")
        return 1

    volume_root = Path(args.volume_root)
    roots = find_pocket4_mounts(volume_root)

    for extra_root in args.source_root:
        roots.append(Path(extra_root))

    if not roots:
        print("No Pocket 4 volume detected.")
        print(f"Searched under: {volume_root}")
        return 0

    total_converted = 0
    total_skipped = 0
    total_imported = 0
    output_dir = Path.cwd()
    mp4_files = collect_mp4_files(roots)

    if not mp4_files:
        print("No mp4 files found in the detected Pocket 4 volumes.")
        print(f"Searched under: {', '.join(str(root) for root in roots)}")
        return 0

    selected_files = prompt_for_mp4_files(mp4_files)

    converted, skipped, imported = process_files(
        selected_files,
        ffmpeg_path,
        dry_run=args.dry_run,
        import_photos=not args.no_import_photos,
        import_youtube=not args.no_upload_youtube,
        output_dir=output_dir,
    )
    total_converted += converted
    total_skipped += skipped
    total_imported += imported

    print("\nDone.")
    print(f"Converted: {total_converted}")
    print(f"Skipped: {total_skipped}")
    print(f"Imported to Photos: {total_imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
