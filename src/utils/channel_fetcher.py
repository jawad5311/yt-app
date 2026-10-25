"""Channel Video Fetcher.

Fetches video metadata directly from YouTube channel pages without external dependencies.
Extracts Video IDs, titles, and video durations using YouTube's embedded ytInitialData JSON
and standard HTML metadata.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FetchedVideo:
    """Represents a single video discovered on a YouTube channel."""

    video_id: str
    url: str
    title: str
    duration_text: str = ""
    duration_seconds: int = 0


@dataclass
class ChannelFetchResult:
    """Outcome of fetching video metadata from a channel."""

    channel_title: str = ""
    channel_url: str = ""
    videos: List[FetchedVideo] = field(default_factory=list)
    error: Optional[str] = None


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


def fetch_channel_title_online(channel_input: str, timeout: int = 10) -> Optional[str]:
    """Contact YouTube and fetch the official human-readable channel name.

    Returns the resolved name (e.g. 'Iamcarhacker' instead of '@iamcarhacker'),
    or None if the network request fails or the page cannot be reached.
    """
    clean_input = (channel_input or "").strip()
    if not clean_input:
        return None

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
    data: Dict[str, Any], max_results: int = 50
) -> List[FetchedVideo]:
    """Traverse ytInitialData and extract video items."""
    results: List[FetchedVideo] = []
    seen_ids: set = set()

    def _walk(obj: Any) -> None:
        if max_results > 0 and len(results) >= max_results:
            return

        if isinstance(obj, dict):
            # Format 1: Modern lockupViewModel (YouTube 2024-2026+)
            if "lockupViewModel" in obj and isinstance(obj["lockupViewModel"], dict):
                lum = obj["lockupViewModel"]
                vid = lum.get("contentId")
                title_obj = lum.get("metadata", {}).get("lockupMetadataViewModel", {}).get("title", {})
                title = title_obj.get("content") if isinstance(title_obj, dict) else None

                dur_str = ""
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
                    results.append(
                        FetchedVideo(
                            video_id=str(vid),
                            url=f"https://www.youtube.com/watch?v={vid}",
                            title=str(title).strip(),
                            duration_text=dur_str,
                            duration_seconds=parse_duration_str(dur_str),
                        )
                    )
                    if max_results > 0 and len(results) >= max_results:
                        return

            # Format 2: videoRenderer & gridVideoRenderer
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
                        if max_results > 0 and len(results) >= max_results:
                            return

            for value in obj.values():
                _walk(value)

        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return results


def fetch_channel_videos(
    channel_input: str,
    max_results: int = 50,
    timeout: int = 15,
) -> ChannelFetchResult:
    """Fetch video metadata from a channel URL or handle."""
    url = normalize_channel_url(channel_input)
    if not url:
        return ChannelFetchResult(error="Channel URL or handle is required.")

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

    videos = extract_videos_from_data(data, max_results=max_results) if data else []

    return ChannelFetchResult(
        channel_title=channel_title,
        channel_url=url,
        videos=videos,
    )
