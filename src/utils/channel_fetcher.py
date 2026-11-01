"""Channel Video Fetcher.

Fetches video metadata directly from YouTube channel pages without external dependencies.
Extracts Video IDs, titles, and video durations using YouTube's embedded ytInitialData JSON
and standard HTML metadata.
"""

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


@dataclass
class FetchedVideo:
    """Represents a single video discovered on a YouTube channel."""

    video_id: str
    url: str
    title: str
    duration_text: str = ""
    duration_seconds: int = 0
    is_short: bool = False


@dataclass
class ChannelFetchResult:
    """Outcome of fetching video metadata from a channel."""

    channel_title: str = ""
    channel_url: str = ""
    videos: List[FetchedVideo] = field(default_factory=list)
    error: Optional[str] = None
    requested_count: int = 0
    fetched_count: int = 0
    status: str = "complete"  # complete, limited, initial_page_only, partial, interrupted, error
    provider: str = "initial_page_scraper"
    warning: Optional[str] = None
    is_truncated: bool = False
    videos_fetched: int = 0
    shorts_fetched: int = 0
    total_videos: Optional[int] = None
    total_shorts: Optional[int] = None


def extract_fallback_channel_name(url_or_handle: str) -> str:
    """Extract a reasonable fallback channel name from a handle or URL."""
    clean = (url_or_handle or "").strip().rstrip("/")
    if not clean:
        return "YouTube Channel"
    clean = clean.split("?")[0].split("#")[0]
    for suffix in ("/videos", "/shorts", "/streams", "/featured"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    if "@" in clean:
        handle = clean.split("@")[-1].split("/")[0]
        return f"@{handle}"
    for prefix in ("/channel/", "/c/", "/user/"):
        if prefix in clean:
            part = clean.split(prefix)[-1].split("/")[0]
            if part:
                return part
    last_part = clean.split("/")[-1]
    return last_part if last_part else "YouTube Channel"


def normalize_channel_url(input_str: str) -> str:
    """Normalize a user-provided handle or URL into a canonical YouTube /videos URL."""
    clean = (input_str or "").strip()
    if not clean:
        return ""

    if not clean.startswith("http://") and not clean.startswith("https://"):
        if clean.startswith("@"):
            clean = f"https://www.youtube.com/{clean}"
        elif clean.startswith("youtube.com") or clean.startswith("www.youtube.com"):
            clean = f"https://{clean}"
        else:
            # Assume it's a handle without @ or a custom identifier
            clean = f"https://www.youtube.com/@{clean}"

    # Remove trailing slashes
    clean = clean.rstrip("/")

    # Ensure /videos is targeted so we get the full video list rather than home/shorts
    if not clean.endswith("/videos") and not clean.endswith("/shorts") and not clean.endswith("/streams"):
        clean = f"{clean}/videos"

    return clean


def parse_duration_str(duration_str: str) -> int:
    """Convert YouTube duration string (e.g. '8:20', '1:14:05') into seconds."""
    clean = (duration_str or "").strip()
    if not clean:
        return 0

    parts = clean.split(":")
    try:
        if len(parts) == 1:
            return max(0, int(parts[0]))
        if len(parts) == 2:
            return max(0, int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return max(0, int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except ValueError:
        return 0
    return 0


def extract_yt_initial_data(html: str) -> Optional[Dict[str, Any]]:
    """Extract and parse the ytInitialData JSON blob from YouTube page HTML."""
    if not html:
        return None

    # Marker-based extraction for real YouTube pages
    markers = [
        "var ytInitialData = ",
        "window[\"ytInitialData\"] = ",
        "ytInitialData = ",
    ]
    for m in markers:
        idx = html.find(m)
        if idx != -1:
            start = idx + len(m)
            end = html.find(";</script>", start)
            if end == -1:
                end = html.find("</script>", start)
            if end != -1:
                chunk = html[start:end].strip().rstrip(";")
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    pass

    patterns = [
        r"var ytInitialData\s*=\s*({.+?});</script>",
        r"ytInitialData\s*=\s*({.+?});",
        r"window\[\"ytInitialData\"\]\s*=\s*({.+?});",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    return None


def extract_channel_title(data: Dict[str, Any]) -> str:
    """Extract official channel title from ytInitialData."""
    if not isinstance(data, dict):
        return ""

    # Check microformat
    mf = data.get("microformat", {})
    if isinstance(mf, dict):
        mf_ren = mf.get("microformatDataRenderer", {})
        if isinstance(mf_ren, dict) and mf_ren.get("title"):
            return str(mf_ren["title"]).strip()

    # Check channelMetadataRenderer
    metadata = data.get("metadata", {})
    if isinstance(metadata, dict):
        cm = metadata.get("channelMetadataRenderer", {})
        if isinstance(cm, dict) and cm.get("title"):
            return str(cm["title"]).strip()

    # Check header
    header = data.get("header", {})
    if isinstance(header, dict):
        for header_key in ("pageHeaderRenderer", "c4TabbedHeaderRenderer"):
            h_obj = header.get(header_key)
            if isinstance(h_obj, dict):
                title_obj = h_obj.get("pageTitle") or h_obj.get("title")
                if isinstance(title_obj, str) and title_obj.strip():
                    return title_obj.strip()
                if isinstance(title_obj, dict):
                    runs = title_obj.get("runs")
                    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
                        text = runs[0].get("text")
                        if text:
                            return str(text).strip()
                    simple_text = title_obj.get("simpleText")
                    if simple_text:
                        return str(simple_text).strip()

    return ""


def extract_channel_title_from_html(html: str) -> str:
    """Extract channel title directly from HTML metadata (og:title, <title>, meta name=title, ytInitialData)."""
    if not html:
        return ""

    # 1. Check og:title meta tag
    og_m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not og_m:
        og_m = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:title["\']', html, re.IGNORECASE)
    if og_m and og_m.group(1).strip():
        name = og_m.group(1).strip()
        if name and not name.lower().endswith(" - youtube") and name.lower() != "youtube":
            return name

    # 2. Check <title> tag
    t_m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if t_m:
        name = t_m.group(1).strip()
        name = re.sub(r'\s*-\s*YouTube\s*$', '', name, flags=re.IGNORECASE).strip()
        if name and name.lower() != "youtube":
            return name

    # 3. Check ytInitialData
    data = extract_yt_initial_data(html)
    if data:
        t = extract_channel_title(data)
        if t:
            return t

    # 4. Check name="title" meta tag
    meta_m = re.search(r'<meta\s+name=["\']title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if meta_m and meta_m.group(1).strip():
        name = meta_m.group(1).strip()
        if name and name.lower() != "youtube":
            return name

    return ""


def fetch_channel_title_online(
    channel_input: str, timeout: int = 10, config_mgr: Optional[Any] = None
) -> Optional[str]:
    """Contact YouTube and fetch the official human-readable channel name.

    Returns the resolved name (e.g. 'Iamcarhacker' instead of '@iamcarhacker'),
    or None if the network request fails or the page cannot be reached.
    """
    clean_input = (channel_input or "").strip()
    if not clean_input:
        return None

    api_key = get_configured_youtube_api_key(config_mgr)
    if api_key:
        try:
            res = fetch_channel_data_api(
                clean_input,
                api_key=api_key,
                video_limit=None,
                shorts_limit=None,
                timeout=timeout,
            )
            if res.channel_title:
                return res.channel_title
        except Exception:
            pass

    # Build canonical / base channel URL
    if not clean_input.startswith("http://") and not clean_input.startswith("https://"):
        if clean_input.startswith("@"):
            url = f"https://www.youtube.com/{clean_input}"
        elif clean_input.startswith("youtube.com") or clean_input.startswith("www.youtube.com"):
            url = f"https://{clean_input}"
        else:
            url = f"https://www.youtube.com/@{clean_input}"
    else:
        url = clean_input

    # Strip subpaths like /videos or /shorts to fetch the main channel page
    clean_base = url.split("?")[0].split("#")[0].rstrip("/")
    for sub in ("/videos", "/shorts", "/streams", "/featured", "/community"):
        if clean_base.endswith(sub):
            clean_base = clean_base[: -len(sub)]

    req = urllib.request.Request(
        clean_base,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    title = extract_channel_title_from_html(html)
    return title if title else None


def extract_videos_from_data(
    data: Dict[str, Any], max_results: Optional[int] = 50
) -> List[FetchedVideo]:
    """Traverse ytInitialData and extract video and shorts items."""
    results: List[FetchedVideo] = []
    seen_ids: set = set()
    limit_val = 0 if max_results == 0 else (max_results if (max_results is not None and max_results > 0) else 50)

    def _walk(obj: Any) -> None:
        if limit_val > 0 and len(results) >= limit_val:
            return

        if isinstance(obj, dict):
            # Format 1: Modern lockupViewModel (YouTube 2024-2026+)
            if "lockupViewModel" in obj and isinstance(obj["lockupViewModel"], dict):
                lum = obj["lockupViewModel"]
                vid = lum.get("contentId")
                title_obj = lum.get("metadata", {}).get("lockupMetadataViewModel", {}).get("title", {})
                title = title_obj.get("content") if isinstance(title_obj, dict) else None

                is_short_item = False
                if lum.get("contentType") in ("LOCKUP_CONTENT_TYPE_SHORT", "SHORT"):
                    is_short_item = True
                if "reelWatchEndpoint" in str(lum) or "/shorts/" in str(lum):
                    is_short_item = True

                dur_str = "Short" if is_short_item else ""
                thumb_vm = lum.get("contentImage", {}).get("thumbnailViewModel", {})
                if isinstance(thumb_vm, dict):
                    for overlay in thumb_vm.get("overlays", []):
                        if not isinstance(overlay, dict):
                            continue
                        bottom_overlay = overlay.get("thumbnailBottomOverlayViewModel", {})
                        if isinstance(bottom_overlay, dict):
                            for badge in bottom_overlay.get("badges", []):
                                if isinstance(badge, dict):
                                    badge_vm = badge.get("thumbnailBadgeViewModel", {})
                                    txt = badge_vm.get("text") if isinstance(badge_vm, dict) else None
                                    if txt and any(c.isdigit() for c in str(txt)):
                                        dur_str = str(txt).strip()
                                        break

                if vid and title and vid not in seen_ids:
                    seen_ids.add(vid)
                    target_url = f"https://www.youtube.com/shorts/{vid}" if is_short_item else f"https://www.youtube.com/watch?v={vid}"
                    results.append(
                        FetchedVideo(
                            video_id=str(vid),
                            url=target_url,
                            title=str(title).strip(),
                            duration_text=dur_str or ("Short" if is_short_item else ""),
                            duration_seconds=parse_duration_str(dur_str) if dur_str != "Short" else 30,
                            is_short=is_short_item,
                        )
                    )
                    if limit_val > 0 and len(results) >= limit_val:
                        return

            # Format 2: reelItemRenderer & shortsLockupViewModel (dedicated Shorts shelf/tab)
            if "reelItemRenderer" in obj and isinstance(obj["reelItemRenderer"], dict):
                r_item = obj["reelItemRenderer"]
                vid = r_item.get("videoId")
                title = ""
                hl = r_item.get("headline", {})
                if isinstance(hl, dict):
                    title = hl.get("simpleText") or ""
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    results.append(
                        FetchedVideo(
                            video_id=str(vid),
                            url=f"https://www.youtube.com/shorts/{vid}",
                            title=str(title).strip() or "Short",
                            duration_text="Short",
                            duration_seconds=30,
                            is_short=True,
                        )
                    )
                    if limit_val > 0 and len(results) >= limit_val:
                        return

            if "shortsLockupViewModel" in obj and isinstance(obj["shortsLockupViewModel"], dict):
                slm = obj["shortsLockupViewModel"]
                vid = slm.get("entityId", "")
                if vid.startswith("shorts-shelf-item-"):
                    vid = vid.replace("shorts-shelf-item-", "")
                if not vid or len(vid) != 11:
                    ep = slm.get("onTap", {}).get("innertubeCommand", {}).get("reelWatchEndpoint", {})
                    if isinstance(ep, dict) and ep.get("videoId"):
                        vid = ep["videoId"]
                title = ""
                pm = slm.get("overlayMetadata", {}).get("primaryText", {})
                if isinstance(pm, dict):
                    title = pm.get("content") or ""
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    results.append(
                        FetchedVideo(
                            video_id=str(vid),
                            url=f"https://www.youtube.com/shorts/{vid}",
                            title=str(title).strip() or "Short",
                            duration_text="Short",
                            duration_seconds=30,
                            is_short=True,
                        )
                    )
                    if limit_val > 0 and len(results) >= limit_val:
                        return

            # Format 3: videoRenderer & gridVideoRenderer
            for renderer_key in ("videoRenderer", "gridVideoRenderer", "compactVideoRenderer"):
                if renderer_key in obj and isinstance(obj[renderer_key], dict):
                    vr = obj[renderer_key]
                    vid = vr.get("videoId")
                    title = ""
                    title_obj = vr.get("title", {})
                    if isinstance(title_obj, dict):
                        if title_obj.get("simpleText"):
                            title = str(title_obj["simpleText"]).strip()
                        elif isinstance(title_obj.get("runs"), list) and title_obj["runs"]:
                            first_run = title_obj["runs"][0]
                            if isinstance(first_run, dict) and first_run.get("text"):
                                title = str(first_run["text"]).strip()

                    dur_str = ""
                    length_obj = vr.get("lengthText", {})
                    if isinstance(length_obj, dict):
                        if length_obj.get("simpleText"):
                            dur_str = str(length_obj["simpleText"]).strip()

                    # Fallback duration from thumbnailOverlays
                    if not dur_str:
                        for overlay in vr.get("thumbnailOverlays", []):
                            if isinstance(overlay, dict):
                                time_status = overlay.get("thumbnailOverlayTimeStatusRenderer", {})
                                if isinstance(time_status, dict):
                                    text_obj = time_status.get("text", {})
                                    if isinstance(text_obj, dict) and text_obj.get("simpleText"):
                                        dur_str = str(text_obj["simpleText"]).strip()
                                        break

                    if vid and title and vid not in seen_ids:
                        seen_ids.add(vid)
                        results.append(
                            FetchedVideo(
                                video_id=str(vid),
                                url=f"https://www.youtube.com/watch?v={vid}",
                                title=title,
                                duration_text=dur_str,
                                duration_seconds=parse_duration_str(dur_str),
                            )
                        )
                        if limit_val > 0 and len(results) >= limit_val:
                            return

            for value in obj.values():
                _walk(value)

        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return results


def sanitize_api_url(url: str) -> str:
    """Sanitize URLs to strip API keys from logs and error messages."""
    return re.sub(r"key=[^&]+", "key=REDACTED", url)


def load_env_file(env_path: Optional[Any] = None, populate_environ: bool = True) -> Dict[str, str]:
    """Load key-value pairs from a .env file into a dictionary and populate os.environ if not set."""
    try:
        from pathlib import Path

        from .helpers import get_project_root
        root = get_project_root()
        # In automated unit tests, skip loading the real project .env file unless explicitly allowed or patched
        import sys
        if ("pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get("USE_REAL_API_KEY"):
            if env_path is None and "site-packages" not in str(root) and (root / "main.py").exists() and (root / ".git").exists():
                return {}
        target = Path(env_path) if env_path is not None else (root / ".env")
        if not target.exists() or not target.is_file():
            return {}
        loaded: Dict[str, str] = {}
        with open(target, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    loaded[key] = val
                    if populate_environ and key not in os.environ:
                        os.environ[key] = val
        return loaded
    except Exception:
        return {}


def get_configured_youtube_api_key(config_mgr: Optional[Any] = None) -> Optional[str]:
    """Retrieve YouTube Data API key from environment, .env file, or configuration without exposing it."""
    # 1. Process environment variables
    env_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if env_key:
        return env_key

    # 2. .env file in project root
    env_vars = load_env_file()
    file_key = env_vars.get("YOUTUBE_API_KEY", "").strip()
    if file_key:
        return file_key

    # 3. settings.json configuration fallback
    if config_mgr is not None and hasattr(config_mgr, "get_setting"):
        cfg_key = str(config_mgr.get_setting("YouTubeApiKey", "") or "").strip()
        if cfg_key:
            return cfg_key
    return None


def parse_iso8601_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration string (e.g. 'PT1H2M10S', 'PT45S', 'PT3M') into seconds."""
    if not duration_str or not duration_str.startswith("P"):
        return 0
    pattern = re.compile(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
    )
    match = pattern.match(duration_str)
    if not match:
        return 0
    parts = match.groupdict()
    days = int(parts.get("days") or 0)
    hours = int(parts.get("hours") or 0)
    minutes = int(parts.get("minutes") or 0)
    seconds = int(parts.get("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_seconds_to_duration(seconds: int) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds <= 0:
        return "0:00"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def enrich_video_durations(
    videos: List[FetchedVideo],
    api_key: str,
    timeout: int = 15,
) -> None:
    """Batch-query YouTube Data API v3 videos endpoint to populate duration fields."""
    if not videos or not api_key:
        return

    for i in range(0, len(videos), 50):
        chunk = videos[i : i + 50]
        ids = [v.video_id for v in chunk if v.video_id]
        if not ids:
            continue
        ids_param = ",".join(ids)
        url = f"https://www.googleapis.com/youtube/v3/videos?part=contentDetails&id={ids_param}&key={api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "yt-tool/2.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = data.get("items", [])
            dur_map = {}
            for item in items:
                vid = item.get("id")
                iso_dur = item.get("contentDetails", {}).get("duration", "")
                if vid and iso_dur:
                    sec = parse_iso8601_duration(iso_dur)
                    dur_map[vid] = (sec, format_seconds_to_duration(sec))

            for v in chunk:
                if v.video_id in dur_map:
                    sec, text_d = dur_map[v.video_id]
                    v.duration_seconds = sec
                    v.duration_text = text_d
        except Exception:
            pass


def get_playlist_total_results(
    playlist_id: str,
    api_key: str,
    timeout: int = 10,
) -> Optional[int]:
    """Retrieve total items in a YouTube playlist (1 quota unit) via pageInfo.totalResults."""
    if not playlist_id or not api_key:
        return None
    url = (
        f"https://www.googleapis.com/youtube/v3/playlistItems"
        f"?part=id&playlistId={playlist_id}&maxResults=1&key={api_key}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "yt-tool/2.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            page_info = data.get("pageInfo", {})
            if "totalResults" in page_info:
                return int(page_info["totalResults"])
    except Exception:
        pass
    return None


def fetch_playlist_items_api(
    playlist_id: str,
    api_key: str,
    limit: Optional[int],
    is_short: bool,
    timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[List[FetchedVideo], Optional[str], Optional[str], bool, bool, Optional[int]]:
    """Fetch items from a playlist using YouTube Data API v3.
    
    limit: None = skip, 0 = unlimited (fetch all), >0 = max items.
    Returns (videos, warning, error, is_truncated, is_interrupted, total_count).
    """
    if limit is None:
        return [], None, None, False, False, None

    videos: List[FetchedVideo] = []
    seen_ids: Set[str] = set()
    seen_tokens: Set[str] = set()
    next_page_token: Optional[str] = None
    warning: Optional[str] = None
    is_truncated = False
    is_interrupted = False
    total_count: Optional[int] = None

    while True:
        if cancel_event is not None and cancel_event.is_set():
            warning = "Channel video fetch interrupted by user."
            is_truncated = True
            is_interrupted = True
            break

        if limit > 0 and len(videos) >= limit:
            is_truncated = True
            break

        fetch_size = min(50, limit - len(videos)) if limit > 0 else 50
        p_url = (
            f"https://www.googleapis.com/youtube/v3/playlistItems"
            f"?part=snippet,contentDetails&playlistId={playlist_id}&maxResults={fetch_size}&key={api_key}"
        )
        if next_page_token:
            p_url += f"&pageToken={urllib.parse.quote(next_page_token)}"

        req = urllib.request.Request(p_url, headers={"User-Agent": "yt-tool/2.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                page_data = json.loads(response.read().decode("utf-8"))
                if total_count is None:
                    p_info = page_data.get("pageInfo", {})
                    if "totalResults" in p_info:
                        try:
                            total_count = int(p_info["totalResults"])
                        except (ValueError, TypeError):
                            pass
        except Exception as exc:
            if videos:
                warning = f"Pagination interrupted by network error on later page: {sanitize_api_url(str(exc))}"
                is_truncated = True
                break
            else:
                return [], None, f"YouTube API playlist items fetch failed: {sanitize_api_url(str(exc))}", False, False, None

        page_items = page_data.get("items", [])
        for item in page_items:
            vid = (
                item.get("contentDetails", {}).get("videoId")
                or item.get("snippet", {}).get("resourceId", {}).get("videoId")
            )
            title = item.get("snippet", {}).get("title", "")
            if vid and title and vid not in seen_ids:
                seen_ids.add(vid)
                url = f"https://www.youtube.com/shorts/{vid}" if is_short else f"https://www.youtube.com/watch?v={vid}"
                videos.append(
                    FetchedVideo(
                        video_id=str(vid),
                        url=url,
                        title=str(title).strip(),
                        is_short=is_short,
                    )
                )
                if limit > 0 and len(videos) >= limit:
                    is_truncated = True
                    break

        if limit > 0 and len(videos) >= limit:
            break

        if progress_callback:
            try:
                progress_callback(len(videos), "shorts" if is_short else "videos")
            except Exception:
                pass

        next_token = page_data.get("nextPageToken")
        if not next_token:
            break

        if next_token in seen_tokens:
            warning = "Repeated page token detected; terminating pagination safely."
            is_truncated = True
            break

        seen_tokens.add(next_token)
        next_page_token = next_token

    return videos, warning, None, is_truncated, is_interrupted, total_count


def fetch_channel_data_api(
    channel_input: str,
    api_key: str,
    video_limit: Optional[int] = 50,
    shorts_limit: Optional[int] = 50,
    timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> ChannelFetchResult:
    """Fetch recent long-form videos and/or shorts from a channel via YouTube Data API v3."""
    clean_input = (channel_input or "").strip()
    if not clean_input:
        return ChannelFetchResult(error="Channel URL or handle is required.", status="error")

    norm_url = normalize_channel_url(clean_input)

    # Check for early cancellation
    if cancel_event is not None and cancel_event.is_set():
        return ChannelFetchResult(
            channel_url=norm_url,
            status="interrupted",
            warning="Channel video fetch interrupted by user.",
            is_truncated=True,
            provider="youtube_api_v3",
        )

    # 1. Resolve channel identifier to obtain channel ID, title, and uploads playlist
    clean_base = clean_input.split("?")[0].split("#")[0].rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams", "/featured", "/community"):
        if clean_base.endswith(suffix):
            clean_base = clean_base[: -len(suffix)]

    channel_req_url = ""
    if "/channel/" in clean_base:
        cid = clean_base.split("/channel/")[-1].split("/")[0]
        channel_req_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails,statistics&id={cid}&key={api_key}"
    elif clean_base.startswith("UC") and len(clean_base) >= 20:
        channel_req_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails,statistics&id={clean_base}&key={api_key}"
    elif "/user/" in clean_base:
        uname = clean_base.split("/user/")[-1].split("/")[0]
        channel_req_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails,statistics&forUsername={urllib.parse.quote(uname)}&key={api_key}"
    else:
        handle = ""
        if "@" in clean_base:
            handle = "@" + clean_base.split("@")[-1].split("/")[0]
        else:
            handle = "@" + clean_base.lstrip("/").split("/")[0]
        channel_req_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails,statistics&forHandle={urllib.parse.quote(handle)}&key={api_key}"

    req = urllib.request.Request(channel_req_url, headers={"User-Agent": "yt-tool/2.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            meta_json = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return ChannelFetchResult(
            channel_url=norm_url,
            error=f"YouTube API channel lookup failed: {sanitize_api_url(str(exc))}",
            status="error",
            provider="youtube_api_v3",
        )

    items = meta_json.get("items", [])
    if not items:
        return ChannelFetchResult(
            channel_url=norm_url,
            error=f"Channel not found for '{clean_input}' via YouTube API.",
            status="error",
            provider="youtube_api_v3",
        )

    channel_id = items[0].get("id", "")
    channel_title = items[0].get("snippet", {}).get("title", "")
    uploads_playlist_id = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    if not uploads_playlist_id and channel_id.startswith("UC"):
        uploads_playlist_id = "UU" + channel_id[2:]

    # Long-form videos: UULF + channel_id[2:] if channel_id known, else uploads_playlist_id
    longform_playlist_id = ("UULF" + channel_id[2:]) if (channel_id and channel_id.startswith("UC")) else uploads_playlist_id
    shorts_playlist_id = ("UUSH" + channel_id[2:]) if (channel_id and channel_id.startswith("UC")) else ""

    stats = items[0].get("statistics", {})
    total_public_videos = None
    if "videoCount" in stats:
        try:
            total_public_videos = int(stats["videoCount"])
        except (ValueError, TypeError):
            pass

    fetched_videos_list: List[FetchedVideo] = []
    fetched_shorts_list: List[FetchedVideo] = []
    warnings: List[str] = []
    is_truncated = False
    is_interrupted = False
    is_partial = False
    v_total: Optional[int] = None
    s_total: Optional[int] = None

    # A. Fetch Videos
    if video_limit is not None and (video_limit >= 0):
        target_pid = longform_playlist_id or uploads_playlist_id
        v_items, v_warn, v_err, v_trunc, v_intr, v_total = fetch_playlist_items_api(
            playlist_id=target_pid,
            api_key=api_key,
            limit=video_limit,
            is_short=False,
            timeout=timeout,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if v_err and "404" in v_err and uploads_playlist_id and target_pid != uploads_playlist_id:
            # Fallback to standard uploads playlist
            v_items, v_warn, v_err, v_trunc, v_intr, v_total = fetch_playlist_items_api(
                playlist_id=uploads_playlist_id,
                api_key=api_key,
                limit=video_limit,
                is_short=False,
                timeout=timeout,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
        if v_err and not v_items:
            return ChannelFetchResult(
                channel_title=channel_title,
                channel_url=norm_url,
                error=v_err,
                status="error",
                provider="youtube_api_v3",
            )
        if v_warn:
            warnings.append(v_warn)
            if "network error" in v_warn:
                is_partial = True
        if v_trunc:
            is_truncated = True
        if v_intr:
            is_interrupted = True
        fetched_videos_list.extend(v_items)

    # B. Fetch Shorts
    if shorts_limit is not None and (shorts_limit >= 0) and shorts_playlist_id:
        s_items, s_warn, s_err, s_trunc, s_intr, s_total = fetch_playlist_items_api(
            playlist_id=shorts_playlist_id,
            api_key=api_key,
            limit=shorts_limit,
            is_short=True,
            timeout=timeout,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if s_err and "404" in s_err and uploads_playlist_id and shorts_playlist_id != uploads_playlist_id:
            # Fallback to standard uploads playlist
            s_items, s_warn, s_err, s_trunc, s_intr, s_total = fetch_playlist_items_api(
                playlist_id=uploads_playlist_id,
                api_key=api_key,
                limit=shorts_limit,
                is_short=True,
                timeout=timeout,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
        if s_warn:
            warnings.append(s_warn)
            if "network error" in s_warn:
                is_partial = True
        if s_trunc:
            is_truncated = True
        if s_intr:
            is_interrupted = True
        fetched_shorts_list.extend(s_items)

    # Ensure shorts take precedence so they retain is_short=True and /shorts/ URL
    shorts_ids = {s.video_id for s in fetched_shorts_list if s.video_id}
    for v in fetched_videos_list:
        if v.video_id in shorts_ids:
            v.is_short = True
            v.url = f"https://www.youtube.com/shorts/{v.video_id}"

    # Deduplicate in order (shorts first to guarantee short URL format)
    combined: List[FetchedVideo] = []
    seen_all_ids: Set[str] = set()
    for item in fetched_shorts_list + fetched_videos_list:
        if item.video_id not in seen_all_ids:
            seen_all_ids.add(item.video_id)
            combined.append(item)

    # Enrich durations via videos.list endpoint
    enrich_video_durations(combined, api_key=api_key, timeout=timeout)

    total_requested = (video_limit or 0) + (shorts_limit or 0)
    total_fetched = len(combined)

    if is_interrupted:
        status = "interrupted"
    elif is_partial:
        status = "partial"
    elif is_truncated or (total_requested > 0 and total_fetched >= total_requested):
        status = "limited"
        is_truncated = True
    else:
        status = "complete"
        is_truncated = False

    # Compute accurate channel video totals
    total_videos = v_total
    total_shorts = s_total

    if total_shorts is None and shorts_playlist_id:
        total_shorts = get_playlist_total_results(shorts_playlist_id, api_key=api_key, timeout=timeout)

    if total_videos is None and longform_playlist_id:
        total_videos = get_playlist_total_results(longform_playlist_id, api_key=api_key, timeout=timeout)

    if total_videos is None and total_public_videos is not None:
        if total_shorts is not None:
            total_videos = max(0, total_public_videos - total_shorts)
        else:
            total_videos = total_public_videos

    return ChannelFetchResult(
        channel_title=channel_title,
        channel_url=norm_url,
        videos=combined,
        requested_count=total_requested,
        fetched_count=total_fetched,
        videos_fetched=len(fetched_videos_list),
        shorts_fetched=len(fetched_shorts_list),
        status=status,
        provider="youtube_api_v3",
        warning="; ".join(warnings) if warnings else None,
        is_truncated=is_truncated,
        total_videos=total_videos,
        total_shorts=total_shorts,
    )


def fetch_channel_data(
    channel_input: str,
    video_limit: Optional[int] = 50,
    shorts_limit: Optional[int] = 50,
    timeout: int = 15,
    config_mgr: Optional[Any] = None,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> ChannelFetchResult:
    """Fetch recent videos and shorts using YouTube Data API v3, with scraper fallback when API key is missing."""
    api_key = get_configured_youtube_api_key(config_mgr)
    if api_key:
        return fetch_channel_data_api(
            channel_input=channel_input,
            api_key=api_key,
            video_limit=video_limit,
            shorts_limit=shorts_limit,
            timeout=timeout,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    # Scraper fallback when API key is not configured
    clean_input = (channel_input or "").strip()
    norm_url = normalize_channel_url(clean_input)
    has_shorts = shorts_limit is not None and (shorts_limit >= 0)
    has_videos = video_limit is not None and (video_limit >= 0)

    if has_shorts and not has_videos:
        shorts_url = norm_url.replace("/videos", "/shorts")
        s_res = fetch_channel_videos(
            shorts_url,
            max_results=shorts_limit,
            timeout=timeout,
            config_mgr=config_mgr,
            cancel_event=cancel_event,
        )
        for v in s_res.videos:
            v.is_short = True
            if "/watch?v=" in v.url:
                v.url = f"https://www.youtube.com/shorts/{v.video_id}"
        return s_res
    elif has_videos and not has_shorts:
        return fetch_channel_videos(
            norm_url,
            max_results=video_limit,
            timeout=timeout,
            config_mgr=config_mgr,
            cancel_event=cancel_event,
        )
    elif has_shorts and has_videos:
        v_res = fetch_channel_videos(
            norm_url,
            max_results=video_limit,
            timeout=timeout,
            config_mgr=config_mgr,
            cancel_event=cancel_event,
        )
        shorts_url = norm_url.replace("/videos", "/shorts")
        s_res = fetch_channel_videos(
            shorts_url,
            max_results=shorts_limit,
            timeout=timeout,
            config_mgr=config_mgr,
            cancel_event=cancel_event,
        )
        for v in s_res.videos:
            v.is_short = True
            if "/watch?v=" in v.url:
                v.url = f"https://www.youtube.com/shorts/{v.video_id}"

        combined = list(s_res.videos) + list(v_res.videos)
        seen_ids = set()
        deduped = []
        for item in combined:
            if item.video_id not in seen_ids:
                seen_ids.add(item.video_id)
                deduped.append(item)

        title = v_res.channel_title or s_res.channel_title
        warn = (
            "Fetched initial page only (no YouTube Data API key configured). "
            "Configure a YouTube API key in settings or YOUTUBE_API_KEY environment variable for full pagination."
        )
        return ChannelFetchResult(
            channel_title=title,
            channel_url=norm_url,
            videos=deduped,
            requested_count=(video_limit or 0) + (shorts_limit or 0),
            fetched_count=len(deduped),
            status="initial_page_only",
            provider="initial_page_scraper",
            warning=warn,
            is_truncated=True,
        )
    else:
        return ChannelFetchResult(
            channel_url=norm_url,
            videos=[],
            status="complete",
        )


def fetch_channel_videos_api(
    channel_input: str,
    api_key: str,
    max_results: Optional[int] = 50,
    timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
) -> ChannelFetchResult:
    """Fetch videos via YouTube Data API v3 (delegates to fetch_channel_data_api)."""
    eff_max = 50 if max_results is None else max_results
    return fetch_channel_data_api(
        channel_input=channel_input,
        api_key=api_key,
        video_limit=eff_max,
        shorts_limit=None,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def fetch_channel_videos(
    channel_input: str,
    max_results: Optional[int] = 50,
    timeout: int = 15,
    config_mgr: Optional[Any] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ChannelFetchResult:
    """Fetch video metadata from a channel URL or handle with supported pagination."""
    eff_max = 50 if max_results is None else max_results
    api_key = get_configured_youtube_api_key(config_mgr)
    if api_key:
        return fetch_channel_videos_api(
            channel_input=channel_input,
            api_key=api_key,
            max_results=eff_max,
            timeout=timeout,
            cancel_event=cancel_event,
        )

    url = normalize_channel_url(channel_input)
    if not url:
        return ChannelFetchResult(error="Channel URL or handle is required.", status="error")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        return ChannelFetchResult(
            channel_url=url,
            error=f"HTTP Error {exc.code}: {exc.reason}",
        )
    except urllib.error.URLError as exc:
        return ChannelFetchResult(
            channel_url=url,
            error=f"Network Error: {exc.reason}",
        )
    except TimeoutError:
        return ChannelFetchResult(
            channel_url=url,
            error="Connection timed out while reaching YouTube.",
        )
    except Exception as exc:
        return ChannelFetchResult(
            channel_url=url,
            error=f"Failed to fetch channel: {exc}",
        )

    data = extract_yt_initial_data(html)
    channel_title = extract_channel_title(data) if data else ""
    if not channel_title:
        channel_title = extract_channel_title_from_html(html)

    if not data and not channel_title:
        return ChannelFetchResult(
            channel_url=url,
            error="Could not parse YouTube page response (bot verification or changed page structure).",
        )

    limit_val = 0 if eff_max == 0 else (eff_max if (eff_max is not None and eff_max > 0) else 50)
    videos = extract_videos_from_data(data, max_results=limit_val) if data else []

    status = "complete"
    warning = None
    is_truncated = False
    if limit_val == 0 or limit_val > len(videos):
        status = "initial_page_only"
        is_truncated = True
        warning = (
            "Fetched initial page only (no YouTube Data API key configured). "
            "Configure a YouTube API key in settings or YOUTUBE_API_KEY environment variable for full pagination."
        )
    elif limit_val > 0 and len(videos) >= limit_val:
        status = "limited"
        is_truncated = False

    return ChannelFetchResult(
        channel_title=channel_title,
        channel_url=url,
        videos=videos,
        requested_count=limit_val,
        fetched_count=len(videos),
        status=status,
        provider="initial_page_scraper",
        warning=warning,
        is_truncated=is_truncated,
    )
