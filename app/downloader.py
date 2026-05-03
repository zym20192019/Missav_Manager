import os
import re
import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import requests
import m3u8
from Crypto.Cipher import AES

from app.tasks import task_manager

DOWNLOAD_DIR = Path("/root/jable-downloader/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Global HTTP session with connection pooling
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=3)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
})


# ============================================================
# Site Parsers
# ============================================================

def detect_site(url: str) -> Optional[str]:
    """Detect which site a URL belongs to."""
    if "jable.tv" in url or "fs1.app" in url:
        return "jable"
    if "missav" in url:
        return "missav"
    return None


def parse_jable(url: str) -> dict:
    """Parse JableTV page to extract video info and M3U8 URL."""
    import cloudscraper
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
        delay=10,
    )
    resp = scraper.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    title_match = re.search(r'og:title"\s+content="([^"]+)"', html)
    title = title_match.group(1).strip() if title_match else "Unknown"

    thumb_match = re.search(r'og:image"\s+content="([^"]+)"', html)
    thumbnail = thumb_match.group(1).strip() if thumb_match else ""

    m3u8_match = re.search(r'https?://[^"\'\s]+\.m3u8[^"\'\s]*', html)
    if not m3u8_match:
        raise ValueError("Cannot find M3U8 URL from JableTV page")
    m3u8_url = m3u8_match.group(0)

    # Extract video ID from URL
    vid_match = re.search(r'/videos/([^/]+)/', url)
    video_id = vid_match.group(1) if vid_match else "unknown"

    return {
        "title": title,
        "thumbnail": thumbnail,
        "m3u8_url": m3u8_url,
        "video_id": video_id,
        "headers": {"Referer": url, "Origin": "https://jable.tv"},
    }


def _unpack_js_eval(script_text: str) -> str:
    """Decode Dean Edwards p,a,c,k,e,d packer (used by MissAV).
    
    MissAV uses base-16 (hex) digit-to-key mapping:
    - Tokens like '0','1',...,'9','a','b',...,'f' map to keys[int(token, 16)]
    - e.g. '8' -> keys[8] = 'https', '7' -> keys[7] = 'surrit'
    """
    match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',\s*(\d+),\s*(\d+),\s*'([^']*)'\s*\.split\('\|'\)",
        script_text, re.DOTALL,
    )
    if not match:
        return ""
    packed = match.group(1)
    a = int(match.group(2))
    c = int(match.group(3))
    keys = match.group(4).split("|")

    def replace_token(m):
        token = m.group(0)
        try:
            idx = int(token, a)
            if idx < len(keys):
                return keys[idx]
        except ValueError:
            pass
        return token

    return re.sub(r'\b[0-9a-fA-F]+\b', replace_token, packed)


def parse_missav(url: str) -> dict:
    """Parse MissAV page to extract video info and M3U8 URL."""
    from curl_cffi import requests as cf_requests

    resp = None
    for attempt in range(3):
        try:
            resp = cf_requests.get(
                url,
                impersonate="chrome",
                headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6",
                    "Referer": "https://missav.live/",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                break
        except Exception:
            pass

    if not resp or resp.status_code != 200:
        raise ValueError(f"Cannot access MissAV page (HTTP {resp.status_code if resp else 'N/A'})")

    html = resp.text

    title_match = re.search(r'og:title"\s+content="([^"]+)"', html)
    title = title_match.group(1).strip() if title_match else "Unknown"

    thumb_match = re.search(r'og:image"\s+content="([^"]+)"', html)
    thumbnail = thumb_match.group(1).strip() if thumb_match else ""

    # Extract M3U8 from packed JavaScript
    m3u8_url = None
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for script in scripts:
        if "eval(function" not in script or "m3u8" not in script:
            continue
        unpacked = _unpack_js_eval(script)
        if not unpacked:
            continue
        m3u8_match = re.search(r'(https?://[^"\'\\;\s]+\.m3u8[^"\'\\;\s]*)', unpacked)
        if m3u8_match:
            m3u8_url = m3u8_match.group(1)
            break

    if not m3u8_url:
        m3u8_match = re.search(r'https?://[^"\'\s]+\.m3u8[^"\'\s]*', html)
        if m3u8_match:
            m3u8_url = m3u8_match.group(0)

    if not m3u8_url:
        raise ValueError("Cannot find M3U8 URL from MissAV page")

    vid_match = re.search(r'(?:videos|cn|en)/([^/]+?)(?:/|$)', url.rstrip("/"))
    video_id = vid_match.group(1) if vid_match else "unknown"

    return {
        "title": title,
        "thumbnail": thumbnail,
        "m3u8_url": m3u8_url,
        "video_id": video_id,
        "headers": {"Referer": "https://missav.live/", "Origin": "https://missav.live"},
    }


def parse_url(url: str) -> dict:
    """Parse URL and return video info dict."""
    site = detect_site(url)
    if site == "jable":
        return parse_jable(url)
    elif site == "missav":
        return parse_missav(url)
    else:
        # Generic M3U8 fallback
        return {
            "title": "Unknown Video",
            "thumbnail": "",
            "m3u8_url": url if url.endswith(".m3u8") else "",
            "video_id": "generic",
            "headers": {},
        }


# ============================================================
# M3U8 Download Engine
# ============================================================

class M3U8Downloader:
    """Download M3U8/HLS streams with AES decryption and multi-threaded segment fetching."""

    def __init__(self, m3u8_url: str, dest_dir: str, extra_headers: dict = None):
        self.m3u8_url = m3u8_url
        self.dest_dir = dest_dir
        self.extra_headers = extra_headers or {}
        self.ts_list: list[str] = []
        self.key_content: Optional[bytes] = None
        self.key_iv: Optional[bytes] = None
        self.temp_dir: Optional[str] = None
        self.max_workers = 8
        self._downloaded = 0
        self._total = 0
        self._bytes_downloaded = 0
        self._start_time = 0
        self._cancelled = False
        self._progress_callback = None

    def _get_headers(self) -> dict:
        h = {
            "User-Agent": _session.headers["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        h.update(self.extra_headers)
        return h

    def _parse_playlist(self):
        """Parse M3U8 master/media playlist and build segment URL list."""
        headers = self._get_headers()
        playlist = m3u8.load(self.m3u8_url, headers=headers)

        # If master playlist, pick highest bandwidth variant
        if playlist.playlists:
            best = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
            variant_uri = best.uri
            if not variant_uri.startswith("http"):
                base = self.m3u8_url.rsplit("/", 1)[0] + "/"
                variant_uri = base + variant_uri
            playlist = m3u8.load(variant_uri, headers=headers)
            self.m3u8_url = variant_uri

        # Extract AES key
        for key in playlist.keys:
            if key and key.uri:
                key_uri = key.uri
                if not key_uri.startswith("http"):
                    base = self.m3u8_url.rsplit("/", 1)[0] + "/"
                    key_uri = base + key_uri
                resp = _session.get(key_uri, headers=headers, timeout=15)
                self.key_content = resp.content
                if key.iv:
                    iv_hex = key.iv.replace("0x", "").replace("0X", "")
                    self.key_iv = bytes.fromhex(iv_hex.zfill(32))
                break

        # Build segment URL list
        base_url = self.m3u8_url.rsplit("/", 1)[0] + "/"
        self.ts_list = []
        for seg in playlist.segments:
            ts_url = seg.uri
            if not ts_url.startswith("http"):
                ts_url = base_url + ts_url
            self.ts_list.append(ts_url)

        self._total = len(self.ts_list)

    def _make_cipher(self, seq_num: int):
        """Create AES cipher for a segment (thread-safe)."""
        if not self.key_content:
            return None
        iv = self.key_iv if self.key_iv else seq_num.to_bytes(16, "big")
        return AES.new(self.key_content, AES.MODE_CBC, iv)

    def _download_segment(self, task: tuple) -> bool:
        """Download and decrypt one TS segment. task=(seq_num, url)."""
        if self._cancelled:
            return False

        seq_num, url = task
        filename = f"{seq_num:06d}.ts"
        filepath = os.path.join(self.temp_dir, filename)

        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self._downloaded += 1
            return True

        try:
            resp = _session.get(url, headers=self._get_headers(), timeout=30)
            resp.raise_for_status()
            content = resp.content

            if self.key_content:
                cipher = self._make_cipher(seq_num)
                content = cipher.decrypt(content)

            with open(filepath, "wb") as f:
                f.write(content)

            self._downloaded += 1
            self._bytes_downloaded += len(content)

            if self._progress_callback:
                elapsed = time.time() - self._start_time
                speed = self._bytes_downloaded / elapsed if elapsed > 0 else 0
                remaining = self._total - self._downloaded
                eta = (remaining * (elapsed / self._downloaded)) if self._downloaded > 0 else 0
                pct = (self._downloaded / self._total * 100) if self._total > 0 else 0
                self._progress_callback(pct, speed, eta, self._downloaded, self._total)

            return True
        except Exception as e:
            return False

    def download(self, progress_callback=None) -> Optional[str]:
        """Download the M3U8 stream and return the final MP4 path."""
        self._progress_callback = progress_callback
        self._start_time = time.time()

        # Parse playlist
        self._parse_playlist()
        if not self.ts_list:
            raise ValueError("No segments found in M3U8 playlist")

        # Create temp dir for segments
        self.temp_dir = tempfile.mkdtemp(dir=str(DOWNLOAD_DIR), prefix=".m3u8_")

        # Download segments with thread pool
        tasks = list(enumerate(self.ts_list))
        pending = set(range(len(tasks)))

        for round_num in range(5):  # Max 5 retry rounds
            if not pending or self._cancelled:
                break
            current_tasks = [tasks[i] for i in pending]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                results = list(executor.map(self._download_segment, current_tasks))
            failed = {i for i, r in zip(pending, results) if not r}
            pending = failed

        if self._cancelled:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            return None

        # Merge segments into final MP4
        output_path = os.path.join(str(DOWNLOAD_DIR), f"{int(time.time())}.mp4")
        with open(output_path, "wb") as outfile:
            for i in range(len(self.ts_list)):
                seg_path = os.path.join(self.temp_dir, f"{i:06d}.ts")
                if os.path.exists(seg_path):
                    with open(seg_path, "rb") as seg:
                        outfile.write(seg.read())

        # Cleanup
        shutil.rmtree(self.temp_dir, ignore_errors=True)

        return output_path

    def cancel(self):
        self._cancelled = True


# ============================================================
# High-level download function
# ============================================================

async def download_video(task_id: str, url: str):
    """Parse URL, download M3U8 stream, update task status."""
    loop = asyncio.get_event_loop()

    # Step 1: Parse page
    task_manager.update_task(task_id, status="parsing", progress=0)
    try:
        info = await loop.run_in_executor(None, parse_url, url)
    except Exception as e:
        task_manager.set_error(task_id, f"解析失败: {str(e)}")
        return

    task_manager.update_task(
        task_id,
        title=info["title"],
        thumbnail=info["thumbnail"],
        site=info.get("site", ""),
    )

    # Step 2: Download M3U8
    task_manager.update_task(task_id, status="downloading", progress=0.1)
    downloader = M3U8Downloader(
        m3u8_url=info["m3u8_url"],
        dest_dir=str(DOWNLOAD_DIR),
        extra_headers=info.get("headers"),
    )

    def on_progress(pct, speed, eta, done, total):
        speed_str = _format_bytes(speed) + "/s" if speed else ""
        eta_str = _format_seconds(eta) if eta else ""
        task_manager.update_task(
            task_id,
            progress=min(pct, 99.9),
            speed=speed_str,
            eta=eta_str,
            downloaded_segments=done,
            total_segments=total,
        )

    try:
        output_path = await loop.run_in_executor(None, downloader.download, on_progress)
        if output_path:
            filesize = os.path.getsize(output_path)
            # Rename to title-based filename
            safe_title = re.sub(r'[<>:"/\\|?*]', "_", info["title"])[:100]
            final_path = os.path.join(str(DOWNLOAD_DIR), f"{safe_title}.mp4")
            if os.path.exists(final_path) and final_path != output_path:
                base, ext = os.path.splitext(final_path)
                counter = 1
                while os.path.exists(f"{base}_{counter}{ext}"):
                    counter += 1
                final_path = f"{base}_{counter}{ext}"
            if output_path != final_path:
                os.rename(output_path, final_path)
            task_manager.set_done(task_id, final_path, filesize)
        else:
            task_manager.set_error(task_id, "下载已取消")
    except Exception as e:
        task_manager.set_error(task_id, f"下载失败: {str(e)}")


async def move_to_cloud_drive(task_id: str, target_path: str, target_name: str = None) -> Optional[tuple]:
    """Move downloaded file to target directory (FUSE-safe: cp + verify + rm)."""
    task = task_manager.get_task(task_id)
    if not task or not task.get("filepath"):
        return None

    src = task["filepath"]
    if not os.path.exists(src):
        return None

    dest_dir = target_path
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))

    if os.path.exists(dest):
        base, ext = os.path.splitext(dest)
        counter = 1
        while os.path.exists(f"{base}_{counter}{ext}"):
            counter += 1
        dest = f"{base}_{counter}{ext}"

    task_manager.update_task(task_id, status="moving")

    def _safe_move():
        import subprocess
        result = subprocess.run(["cp", "-p", src, dest], capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"Copy failed: {result.stderr}")
        if not os.path.exists(dest):
            raise RuntimeError("Destination file not created")
        src_size = os.path.getsize(src)
        dest_size = os.path.getsize(dest)
        if dest_size != src_size:
            os.remove(dest)
            raise RuntimeError(f"Size mismatch: src={src_size} dest={dest_size}")
        os.remove(src)
        return (src, dest)

    try:
        loop = asyncio.get_event_loop()
        original_src, final_dest = await loop.run_in_executor(None, _safe_move)
        task_manager.update_task(task_id, status="moved", filepath=final_dest)
        return (original_src, final_dest)
    except Exception as e:
        task_manager.update_task(task_id, status="error", error=f"Move failed: {str(e)}")
        return None


def _format_bytes(bytes_val: float) -> str:
    if not bytes_val:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


def _format_seconds(seconds: float) -> str:
    if not seconds:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
