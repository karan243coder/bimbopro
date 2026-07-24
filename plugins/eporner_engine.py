# -*- coding: utf-8 -*-
# ============================================================
#  Eporner custom engine for Telegram bot
#  - yt-dlp info extractor bypass via Eporner XHR API
#  - Extracts direct MP4/HLS qualities (240p up to 4K/60fps)
# ============================================================

import re
import json
import html as html_lib
import logging
from urllib.parse import urlparse, urljoin, quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

QLABEL = {
    144: "144p", 240: "240p", 360: "360p", 480: "480p (SD)",
    720: "720p (HD)", 1080: "1080p (FHD)", 1440: "1440p (2K)", 2160: "4K UHD",
}

import string

def _encode_base_n(num, n):
    chars = string.digits + string.ascii_lowercase
    if num == 0:
        return chars[0]
    result = []
    while num > 0:
        result.append(chars[num % n])
        num //= n
    return "".join(reversed(result))

def _calc_hash(s):
    return "".join(_encode_base_n(int(s[lb:lb + 8], 16), 36) for lb in range(0, 32, 8))


def is_eporner(url: str) -> bool:
    try:
        host = (urlparse(str(url)).hostname or "").lower()
    except Exception:
        host = str(url or "").lower()
    host = re.sub(r"^(www|m|mobile|de|fr|es|it|pt|nl|ru|jp|en)\.", "", host)
    if "eporner" in host:
        return True
    return False


def _clean_eporner_page_url(url: str) -> str:
    url = html_lib.unescape(str(url or "").strip())
    m = re.search(r"https?://[^\s<>\"']+", url)
    if m:
        url = m.group(0)
    url = url.strip().strip("`'\"<>[]()")
    try:
        p = urlparse(url)
        return p._replace(query="", fragment="").geturl()
    except Exception:
        return url.split("?", 1)[0].split("#", 1)[0]


def _base_of(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname}"
    except Exception:
        return "https://www.eporner.com"


def _clean_title(title, page_url):
    if not title:
        return "eporner_video"
    t = re.sub(r"\s*[-|]\s*(EPORNER|Eporner).*$", "", title, flags=re.I)
    t = re.sub(r'[\\/:*?"<>|]+', ' ', t).strip()
    return t[:100] or "eporner_video"


def _parse_duration_sec(dur: str) -> int:
    if not dur:
        return 999999
    s = str(dur).strip().lower()
    try:
        if ":" in s:
            parts = [int(p) for p in s.split(":") if p.strip().isdigit()]
            if len(parts) == 3:
                return parts[0]*3600 + parts[1]*60 + parts[2]
            if len(parts) == 2:
                return parts[0]*60 + parts[1]
            if len(parts) == 1:
                return parts[0]
        total = 0
        hm = re.match(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", s)
        if hm:
            h, m, sec = hm.groups()
            total = (int(h or 0))*3600 + (int(m or 0))*60 + int(sec or 0)
            if total > 0:
                return total
    except Exception:
        pass
    return 999999


def extract(url: str, cookies_file: str = None):
    desktop = _clean_eporner_page_url(url)
    base = _base_of(desktop)
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": desktop,
        "Origin": base,
    }

    session = requests.Session()
    try:
        r = session.get(desktop, headers=headers, timeout=25, allow_redirects=True)
        html = r.text
        final_url = r.url
    except Exception as e:
        logger.warning("eporner page fetch fail: %s", e)
        return None

    if not html:
        logger.warning("eporner: empty html")
        return None

    m_id = (
        re.search(r'/(?:hd-porn|embed)/([a-zA-Z0-9]+)', final_url)
        or re.search(r'/video-([a-zA-Z0-9]+)', final_url)
        or re.search(r'/(?:hd-porn|embed)/([a-zA-Z0-9]+)', desktop)
        or re.search(r'/video-([a-zA-Z0-9]+)', desktop)
    )
    if not m_id:
        logger.warning("eporner: video id not found in url %s", final_url)
        return None
    video_id = m_id.group(1)

    m_hash = re.search(r'hash\s*[:=]\s*["\']([\da-f]{32})["\']', html)
    if not m_hash:
        logger.warning("eporner: hash not found in webpage")
        return None
    vid_hash = m_hash.group(1)
    ch = _calc_hash(vid_hash)

    title = None
    tm = (
        re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html, re.I)
        or re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    )
    if tm:
        title = re.sub(r"\s+", " ", tm.group(1)).strip()

    duration = None
    dm = re.search(r'<meta[^>]+property=["\']video:duration["\'][^>]+content=["\'](\d+)["\']', html, re.I)
    if dm:
        try:
            duration = int(dm.group(1))
        except Exception:
            pass

    api_url = f"https://www.eporner.com/xhr/video/{video_id}"
    try:
        api_res = session.get(
            api_url,
            params={
                "hash": ch,
                "device": "generic",
                "domain": "www.eporner.com",
                "fallback": "false",
            },
            headers=headers,
            timeout=20,
        )
        if api_res.status_code != 200:
            logger.warning("eporner API status code %s", api_res.status_code)
            return None
        video_data = api_res.json()
    except Exception as e:
        logger.warning("eporner API fetch error: %s", e)
        return None

    if video_data.get("available") is False:
        logger.warning("eporner video not available: %s", video_data.get("message"))
        return None

    sources = video_data.get("sources", {})
    mp4_sources = sources.get("mp4", {})
    if not mp4_sources and not sources:
        logger.warning("eporner: no sources found in API response")
        return None

    qualities = []
    found_heights = []
    for fmt_key, fmt_dict in mp4_sources.items():
        if not isinstance(fmt_dict, dict):
            continue
        src = fmt_dict.get("src")
        if not src or not src.startswith("http"):
            continue
        hm = re.search(r'(\d{3,4})[pP]', fmt_key)
        h = int(hm.group(1)) if hm else 720
        found_heights.append((h, fmt_key, src))

    hls_sources = sources.get("hls", {})
    master_hls = None
    if isinstance(hls_sources, dict):
        for hk, hd in hls_sources.items():
            if isinstance(hd, dict) and hd.get("src"):
                master_hls = hd.get("src")
                break

    if not found_heights and master_hls:
        qualities.append({
            "height": 720,
            "label": "720p (HD)",
            "m3u8": master_hls,
            "url": master_hls,
        })

    found_heights.sort(key=lambda x: x[0], reverse=True)
    seen_h = set()
        
    for h, fk, src in found_heights:
        if h in seen_h:
            continue
        seen_h.add(h)
        lbl = QLABEL.get(h, f"{h}p")
        if "60fps" in fk.lower():
            lbl += " 60fps"
        qualities.append({
            "height": h,
            "label": lbl,
            "m3u8": src,
            "url": src,
        })

    if not qualities:
        return None

    return {
        "title": _clean_title(title, desktop),
        "duration": duration,
        "webpage_url": desktop,
        "master_m3u8": qualities[0]["url"],
        "qualities": qualities,
        "headers": {"User-Agent": UA, "Referer": desktop, "Origin": base},
    }


def extract_video(url: str, cookies_file: str = None):
    return extract(url, cookies_file)


def extract_listing(url: str):
    desktop = _clean_eporner_page_url(url)
    base = _base_of(desktop)
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": desktop,
        "Origin": base,
    }
    session = requests.Session()
    try:
        r = session.get(desktop, headers=headers, timeout=25, allow_redirects=True)
        if r.status_code != 200:
            return [], None, "HTTP error"
        html = r.text
        final_url = r.url
    except Exception as e:
        return [], None, str(e)

    soup = BeautifulSoup(html, "lxml")
    items = []
    seen = set()

    for a in soup.select("a[href*='/video-']"):
        href = a.get("href")
        if not href:
            continue
        u = urljoin(base, href)
        if u in seen:
            continue
        seen.add(u)
        title = a.get("title") or a.get("aria-label") or ""
        if not title:
            img = a.find("img")
            if img:
                title = img.get("alt") or img.get("title") or ""
        if not title:
            txt = a.get_text(" ", strip=True)
            if txt and len(txt) > 3:
                title = txt
        if not title:
            slug = urlparse(u).path.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").strip().title() or "Video"

        thumb = ""
        img = a.find("img")
        if img:
            thumb = img.get("src") or img.get("data-src") or img.get("data-original") or ""

        dur = ""
        parent = a.parent
        for _ in range(3):
            if parent is None:
                break
            dur_el = parent.select_one(".duration, .mbtim, [class*='duration']")
            if dur_el:
                dur = dur_el.get_text(strip=True)
                break
            parent = parent.parent

        items.append({
            "title": title[:120],
            "url": u,
            "thumb": thumb,
            "duration": dur,
            "duration_sec": _parse_duration_sec(dur),
        })

    next_page = None
    for a in soup.select("a.next, a[rel='next'], .pagination a.ar, a[class*='next']"):
        href = a.get("href")
        if href:
            next_page = urljoin(base, href)
            break

    return items, next_page, None
