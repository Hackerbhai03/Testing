# Audio extraction helpers: format selection, cache lookup and cache pruning.
#
# Two backends are available (config.AUDIO_BACKEND):
#
#   native (default)
#       Downloads YouTube's own Opus stream (itag 251, ~130-160 kbps VBR) as-is.
#       There is NO transcoding step, so ffmpeg is not used for extraction and
#       there is no generation loss: what YouTube serves is what you hear, and
#       files stay around 1 MB per minute.
#
#   ffmpeg
#       Old style: download best audio, then re-encode with ffmpeg to Opus 128k.
#       Slower and slightly lossy (lossy -> lossy), kept only as an escape hatch.


import os
import time
from pathlib import Path

DOWNLOAD_DIR = "downloads"
AUDIO_EXTS = ("webm", "opus", "m4a", "ogg")
VIDEO_EXTS = ("mp4",)
_PARTIAL = (".part", ".ytdl", ".temp")

# Opus first (best quality per byte), then AAC, then anything.
NATIVE_AUDIO_FORMAT = (
    "bestaudio[acodec=opus][abr<=160]"
    "/bestaudio[acodec=opus]"
    "/bestaudio[ext=m4a]"
    "/bestaudio"
)
VIDEO_FORMAT = "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio)"


def ydl_options(video: bool, cookie: str | None, backend: str = "native") -> dict:
    """Build yt-dlp options for the chosen backend."""
    opts = {
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "geo_bypass": True,
        "no_warnings": True,
        "overwrites": False,
        "nocheckcertificate": True,
        "cookiefile": cookie,
        "remote_components": ["ejs:github"],
        # Faster, un-throttled downloads on googlevideo.
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 4,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 15,
    }
    if video:
        opts.update(format=VIDEO_FORMAT, merge_output_format="mp4")
        return opts

    opts["format"] = NATIVE_AUDIO_FORMAT
    if backend == "ffmpeg":
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "opus",
                "preferredquality": "128",
            }
        ]
    return opts


def find_cached(video_id: str, video: bool = False) -> str | None:
    """Return an already downloaded file for this id, if any (and mark it as recently used)."""
    for ext in VIDEO_EXTS if video else AUDIO_EXTS:
        path = f"{DOWNLOAD_DIR}/{video_id}.{ext}"
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            try:
                os.utime(path)
            except OSError:
                pass
            return path
    return None


def prune(limit_mb: int, protected: set[str], grace: int = 600) -> int:
    """
    Delete least-recently-used files until downloads/ fits in limit_mb.
    Files in protected (queued/playing) and files touched in the last grace
    seconds are never removed. Returns the number of files deleted.
    """
    if limit_mb <= 0:
        return 0
    root = Path(DOWNLOAD_DIR)
    if not root.is_dir():
        return 0

    keep = {os.path.normpath(p) for p in protected if p}
    now = time.time()
    files = []
    total = 0
    for f in root.iterdir():
        if not f.is_file() or f.name.endswith(_PARTIAL):
            continue
        st = f.stat()
        total += st.st_size
        if os.path.normpath(str(f)) in keep or now - st.st_mtime < grace:
            continue
        files.append((st.st_mtime, st.st_size, f))

    limit = limit_mb * 1024 * 1024
    deleted = 0
    for _, size, f in sorted(files):
        if total <= limit:
            break
        try:
            f.unlink()
            total -= size
            deleted += 1
        except OSError:
            pass
    return deleted