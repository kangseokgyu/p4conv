import argparse
import os
import sys
from datetime import date, datetime
import re
import subprocess
import shutil
from pathlib import Path
from typing import List, Optional, Tuple


POCKET4_VOLUME_PATTERN = re.compile(r"pocket\s*4", re.IGNORECASE)
DEFAULT_VOLUME_ROOT = Path("/Volumes")
CAPTION_BASE_DATE = date(2026, 3, 19)


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
    size_label = format_file_size(path.stat().st_size)
    duration_seconds = probe_video_duration(path)
    if duration_seconds is None:
        return f"{path.name} | {size_label}"
    duration_label = format_duration(duration_seconds)
    return f"{path.name} | {duration_label} | {size_label}"


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


def build_ffmpeg_command(source: Path, target: Path, ffmpeg_path: str) -> List[str]:
    """Build the ffmpeg command for a fast HEVC 1080p transcode."""
    scale_filter = "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2"

    return [
        ffmpeg_path,
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


def convert_mp4_to_mov(
    source: Path,
    ffmpeg_path: str,
    dry_run: bool = False,
    output_dir: Optional[Path] = None,
) -> bool:
    """Convert one mp4 into a mov file in the chosen output directory."""
    target_dir = output_dir or Path.cwd()
    target = target_dir / source.with_suffix(".mov").name

    if target.exists():
        print(f"Skip: already converted -> {target.name}")
        return False

    command = build_ffmpeg_command(source, target, ffmpeg_path)
    print(f"Convert: {source.name} -> {target.name}")

    if dry_run:
        print("  " + " ".join(command))
        return True

    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"Failed: {source.name}")
        if exc.stderr:
            print(exc.stderr.strip().splitlines()[-1])
        return False

    try:
        source_stat = source.stat()
        os.utime(target, (source_stat.st_atime, source_stat.st_mtime))
    except OSError as exc:
        print(f"Warning: could not copy timestamps for {target.name}.")
        print(str(exc))

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


def import_into_photos(
    file_paths: List[Path],
    captions: Optional[List[str]] = None,
    dry_run: bool = False,
) -> bool:
    """Import files into Photos and write each caption into the description field."""
    if not file_paths:
        return True

    if dry_run:
        print("Import to Photos:")
        for index, path in enumerate(file_paths):
            caption = captions[index] if captions and index < len(captions) else ""
            if caption:
                print(f"  {path} -> caption: {caption}")
            else:
                print(f"  {path}")
        return True

    if shutil.which("osascript") is None:
        print("Warning: osascript is not available, skipping Photos import.")
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
        print("Warning: Photos import failed.")
        if exc.stderr:
            print(exc.stderr.strip().splitlines()[-1])
        return False

    print(f"Imported {len(file_paths)} file(s) into Photos.")
    return True


def process_files(
    mp4_files: List[Path],
    ffmpeg_path: str,
    dry_run: bool = False,
    import_photos: bool = True,
    output_dir: Optional[Path] = None,
) -> Tuple[int, int, int]:
    """Convert and import a selected batch of files one by one."""
    converted = 0
    skipped = 0
    imported = 0
    if not mp4_files:
        return 0, 0, 0

    print(f"\nProcessing {len(mp4_files)} selected mp4 file(s)")

    for source in mp4_files:
        changed = convert_mp4_to_mov(source, ffmpeg_path, dry_run=dry_run, output_dir=output_dir)
        if changed:
            converted += 1
            mov_path = (output_dir or Path.cwd()) / source.with_suffix(".mov").name
            caption = make_caption(source)
            if import_photos:
                if import_into_photos([mov_path], captions=[caption], dry_run=dry_run):
                    imported += 1
        else:
            skipped += 1

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
    return parser.parse_args()


def main() -> int:
    """Main entry point: locate files, prompt for selection, then process them."""
    args = parse_args()
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
