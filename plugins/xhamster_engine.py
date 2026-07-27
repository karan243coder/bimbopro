# -*- coding: utf-8 -*-
# ============================================================
#  xHamster custom engine for Telegram bot
#  - yt-dlp info extractor bypass
#  - Finds plaintext/escaped/encrypted HLS
#  - Builds h264 HLS quality URLs
#  - 429 rate-limit fix: mirror rotation + retry + UA rotation
# ============================================================

import re
import json
import html as html_lib
import logging
import random
import time
from urllib.parse import urlparse, unquote

import requests

logger = logging.getLogger(__name__)

# ============================================================
# 429 FIX: Multiple User-Agents for rotation
# ============================================================
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# Default UA (backward compat)
UA = _UA_POOL[0]

# ============================================================
# 429 FIX: Mirror domains - har domain ka ALAG rate limit hota hai
# Agar ek 429 deta hai toh doosra try karo!
# ============================================================
_MIRROR_HOSTS = [
    "xhamster46.desi",
    "xhamster.desi",
    "xhamster19.desi",
    "xhamster.com",
    "xhamster.one",
    "xhamster2.com",
    "xhamster3.com",
    "xhamster.tv",
]

# Retry config
MAX_RETRIES_PER_DOMAIN = 2   # har domain pe max 2 baar try
BASE_DELAY = 2
MAX_DELAY = 20
REQUEST_TIMEOUT = 30
DOMAIN_SWITCH_DELAY = 1.5    # domain switch pe chhota delay

def _random_ua():
    """Har request pe alag User-Agent do"""
    return random.choice(_UA_POOL)

_XH_BRANDS = (
    "xhamster", "xhms", "xhday", "xhvid", "xhwide", "xhwebcam",
    "xhopen", "xhtab", "xhtotal", "xhofficial", "xhaccess", "xhmoon",
    "xhbig", "xhbranch", "xhchannel", "xhdate", "xhlease", "xhcdn",
)
_XH_TLDS = (
    ".com", ".desi", ".one", ".tv", ".pro", ".net", ".to",
    ".xxx", ".porn", ".sex", ".mobi", ".cc", ".org",
)

QLABEL = {
    144: "144p", 240: "240p", 360: "360p", 480: "480p (SD)",
    720: "720p (HD)", 1080: "1080p (FHD)", 1440: "1440p", 2160: "4K",
}


def is_xhamster(url: str) -> bool:
    try:
        host = (urlparse(str(url)).hostname or "").lower()
    except Exception:
        host = str(url or "").lower()
    host = re.sub(r"^(www|m|mobile|de|fr|es|it|pt|nl|ru|jp|en)\.", "", host)
    if "xhamster" in host:
        return True
    for brand in _XH_BRANDS:
        if host == brand or host.startswith(brand + ".") or f".{brand}." in host:
            return True
        for tld in _XH_TLDS:
            if host == brand + tld or host.endswith("." + brand + tld):
                return True
    if re.match(r"^xh[a-z0-9]{1,12}\.(com|desi|one|tv|pro|net|to|xxx|porn|cc)$", host):
        return True
    return False


def _clean_xhamster_page_url(url: str) -> str:
    """xHamster share URL se tracking query hatao."""
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

def _to_desktop(url: str) -> str:
    return re.sub(r"^(https?://(?:.+?\.)?)m\.", r"\1", str(url or "").strip())


def _base_of(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname}"
    except Exception:
        return "https://xhamster.com"


# ============================================================
# 429 FIX: URL ko multiple mirror domains pe convert karo
# ============================================================
def _get_mirror_urls(url: str):
    """Ek URL se sabhi mirror URLs banao. Har mirror ka alag rate limit."""
    url = _clean_xhamster_page_url(_to_desktop(url))
    try:
        parsed = urlparse(url)
        path = parsed.path
        original_host = (parsed.hostname or "").lower()
    except Exception:
        return [url]

    # Sirf video pages ke liye mirrors try karo
    if "/videos/" not in path and "/movies/" not in path:
        return [url]

    mirror_urls = []
    seen = set()

    # Pehle original URL (highest priority)
    if url not in seen:
        mirror_urls.append(url)
        seen.add(url)

    # Phir sabhi mirrors
    for host in _MIRROR_HOSTS:
        # Skip if this is the original host
        if host == original_host or host.replace(".desi", "").replace(".com", "").replace(".one", "") == original_host.replace(".desi", "").replace(".com", "").replace(".one", ""):
            # Already have original, but add if different TLD
            pass

        mirror_url = f"https://{host}{path}"
        if mirror_url not in seen:
            mirror_urls.append(mirror_url)
            seen.add(mirror_url)

    # Shuffle mirrors (except first) to distribute load
    if len(mirror_urls) > 2:
        rest = mirror_urls[1:]
        random.shuffle(rest)
        mirror_urls = [mirror_urls[0]] + rest

    return mirror_urls


def _normalize_html_for_urls(text: str) -> str:
    if not text:
        return ""
    out = html_lib.unescape(str(text))
    out = out.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    out = out.replace("\\u0026", "&").replace("\\u003D", "=").replace("\\u003d", "=")
    try:
        out2 = unquote(out)
        if out2 != out:
            out = out + "\n" + out2
    except Exception:
        pass
    return out


def _find_m3u8_candidates(text: str):
    text = _normalize_html_for_urls(text)
    candidates = []
    for m in re.finditer(r'https?://[^"\'\s<>]+?\.m3u8[^"\'\s<>]*', text, re.I):
        u = m.group(0).rstrip('\\,;)}]')
        if u not in candidates:
            candidates.append(u)
    return candidates


def _pick_best_master(candidates):
    if not candidates:
        return None

    def score(u):
        lu = u.lower()
        sc = 0
        if "_tpl_" in lu:
            sc += 100
        if "hls" in lu:
            sc += 40
        if "h264" in lu:
            sc += 30
        if "av1" in lu:
            sc += 10
        if "multi=" in lu:
            sc += 20
        if "/seg-" in lu:
            sc -= 100
        return sc

    return sorted(candidates, key=score, reverse=True)[0]


def _ytdlp_decipher():
    """yt-dlp ka xHamster decipher reuse. Sirf encrypted HLS ke liye."""
    try:
        import yt_dlp
        from yt_dlp.extractor.xhamster import XHamsterIE
        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        ie = XHamsterIE()
        ie.set_downloader(ydl)

        def dec(u, fid="hls"):
            try:
                return ie._decipher_format_url(u, fid)
            except Exception:
                return None
        return dec
    except Exception as e:
        logger.warning("xhamster: yt-dlp decipher unavailable: %s", e)
        return None


def _extract_window_initials(html: str):
    if not html:
        return None
    idx = html.find("window.initials")
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None

    depth = 0
    in_str = False
    quote = ""
    esc = False
    end = None
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None

    raw = html[start:end]
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning("xhamster: window.initials json load failed: %s", e)
        return None


def _walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def _walk_key_values(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, k, v
            yield from _walk_key_values(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_key_values(v, f"{path}[{i}]")


def _decipher_candidates(values):
    if not values:
        return None
    dec = _ytdlp_decipher()
    seen = set()
    cleaned = []
    for val in values:
        if not isinstance(val, str):
            continue
        val = _normalize_html_for_urls(val).strip().strip('"\'')
        if val and val not in seen:
            seen.add(val)
            cleaned.append(val)

    direct = []
    for val in cleaned:
        if ".m3u8" in val or "m3u8" in val.lower():
            direct.extend(_find_m3u8_candidates(val))
            if val.startswith("http") and ".m3u8" in val:
                direct.append(val)
    picked = _pick_best_master(direct)
    if picked:
        return picked

    if not dec:
        return None

    for val in cleaned:
        low = val.lower()
        looks_candidate = (
            re.fullmatch(r"[0-9a-fA-F]{40,}", val)
            or (val.startswith("http") and re.search(r"/[0-9a-fA-F]{40,}(?:[/,]|$)", val))
            or ("hls" in low and len(val) > 30)
        )
        if not looks_candidate:
            continue
        for fid in ("h264", "av1", "hls"):
            out = dec(val, fid)
            if out and ".m3u8" in out:
                return out
    return None


def _find_hls_from_initials(initials):
    if not isinstance(initials, dict):
        return None

    direct = []
    for val in _walk_strings(initials):
        if ".m3u8" in val or "m3u8" in val.lower():
            direct.extend(_find_m3u8_candidates(val))
            if val.startswith("http") and ".m3u8" in val:
                direct.append(val)
    picked = _pick_best_master(direct)
    if picked:
        return picked

    candidates = []
    priority = []
    for path, key, value in _walk_key_values(initials):
        p = path.lower()
        k = str(key).lower()
        if isinstance(value, str):
            if any(w in p for w in ("hls", "source", "sources", "h264", "av1", "fallback", "video")):
                candidates.append(value)
                if any(w in p for w in ("hls", "h264", "fallback")):
                    priority.append(value)
            elif k in ("url", "fallback", "src", "file") and len(value) > 30:
                candidates.append(value)
        elif isinstance(value, dict) and any(w in p for w in ("hls", "h264", "av1", "source", "sources")):
            for sv in _walk_strings(value):
                candidates.append(sv)
                priority.append(sv)

    out = _decipher_candidates(priority)
    if out:
        return out
    out = _decipher_candidates(candidates)
    if out:
        return out

    broad = [v for v in _walk_strings(initials) if len(v) > 40]
    return _decipher_candidates(broad)


def _heights_from_master(master_text: str):
    hs = set()
    for m in re.finditer(r"RESOLUTION=\d+x(\d+)", master_text or ""):
        hs.add(int(m.group(1)))
    return sorted(hs)


def _build_variant_url(master_url: str, height: int) -> str:
    u = master_url or ""
    u = u.replace(".av1.mp4.m3u8", ".h264.mp4.m3u8")
    u = u.replace("/av1/", "/h264/")
    u = u.replace(".av1.", ".h264.")
    if "_TPL_" in u:
        u = u.replace("_TPL_", f"{height}p")
    if f"{height}p" not in u and re.search(r"/[^/?]+\.h264\.mp4\.m3u8", u):
        u = re.sub(r"/[^/?]+\.h264\.mp4\.m3u8", f"/{height}p.h264.mp4.m3u8", u)
    return u


def _clean_cookie_domain(dom: str) -> str:
    dom = str(dom or "").strip()
    m = re.search(r"([a-z0-9.-]*xhamster[a-z0-9.-]*)", dom, re.I)
    if m:
        dom = m.group(1)
    dom = dom.replace("http://", "").replace("https://", "")
    dom = dom.split("/")[0]
    return dom.lstrip(".").lower()


def _load_netscape_cookies(cookies_file: str):
    jar = {}
    cookie_header_parts = []
    if not cookies_file:
        return None, None
    try:
        with open(cookies_file, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    parts = re.split(r"\s+", line, maxsplit=6)
                if len(parts) < 7:
                    continue
                dom = _clean_cookie_domain(parts[0])
                name = parts[5].strip()
                value = parts[6].strip()
                if not name:
                    continue
                if ("xhamster" in dom) or dom.startswith("xh") or ("xhcdn" in dom):
                    jar[name] = value
                    cookie_header_parts.append(f"{name}={value}")
        return (jar or None), ("; ".join(cookie_header_parts) if cookie_header_parts else None)
    except Exception as e:
        logger.warning("xhamster: cookies load failed: %s", e)
        return None, None


def _candidate_video_urls_from_initials(initials, current_url: str):
    """Limited/share page ke initials['urls'] ya kisi bhi nested value se playable video URLs nikalo."""
    out = []

    def add_clean(cand):
        cand = _clean_xhamster_page_url(cand)
        if cand and is_xhamster(cand) and ("/videos/" in cand or "/movies/" in cand) and cand not in out:
            out.append(cand)

    def add(u):
        if not isinstance(u, str):
            return
        u = _normalize_html_for_urls(u).strip()
        for m in re.finditer(r"https?://[^\s<>\"']+", u):
            cand = m.group(0).strip().strip("`'\"<>[]()")
            cand = cand.replace("&amp;", "&")
            add_clean(cand)

    if isinstance(initials, dict):
        urls_node = initials.get("urls")
        for val in _walk_strings(urls_node):
            add(val)
        for path, key, val in _walk_key_values(initials):
            p = path.lower()
            if isinstance(val, str) and any(w in p for w in ("url", "link", "fallback", "canonical", "pagehidden")):
                add(val)

    cur = _clean_xhamster_page_url(current_url)
    add_clean(cur)

    try:
        parsed = urlparse(cur)
        path = parsed.path
        if "/videos/" in path or "/movies/" in path:
            preferred_hosts = [
                "xhamster46.desi",
                "xhamster.desi",
                "xhamster19.desi",
                "xhamster.com",
                "xhamster.one",
            ]
            for host in preferred_hosts:
                add_clean(f"https://{host}{path}")
    except Exception:
        pass

    out = [u for u in out if "/my/favorites/" not in u and "/watch-later" not in u]
    return out


def _has_player_data(html: str):
    initials = _extract_window_initials(html)
    if not isinstance(initials, dict):
        return False, initials
    if isinstance(initials.get("videoModel"), dict):
        return True, initials
    try:
        if isinstance(initials.get("xplayerSettings", {}).get("sources"), dict):
            return True, initials
    except Exception:
        pass
    return False, initials


def _title_has_cjk_or_japanese(text: str) -> bool:
    """Japanese/Chinese/Korean title detect."""
    return any(
        ('\u3040' <= ch <= '\u30ff') or
        ('\u3400' <= ch <= '\u4dbf') or
        ('\u4e00' <= ch <= '\u9fff') or
        ('\uf900' <= ch <= '\ufaff')
        for ch in str(text or '')
    )


def _title_from_url_slug(page_url: str):
    """URL slug se readable English-style title banao."""
    try:
        slug = urlparse(page_url).path.rstrip('/').split('/')[-1]
        if not slug:
            return None
        parts = slug.split('-')
        if len(parts) > 1 and re.match(r'^(?:xh)?[A-Za-z0-9]{5,}$', parts[-1]):
            slug = '-'.join(parts[:-1])
        slug = unquote(slug).replace('-', ' ')
        slug = re.sub(r'\s+', ' ', slug).strip()
        if not slug:
            return None
        return slug.title()
    except Exception:
        return None


def _clean_title(title: str, page_url: str):
    """Filename ke liye clean title; Japanese/localized title aaye to slug title use karo."""
    title = html_lib.unescape(str(title or '')).strip()
    title = re.sub(r'\s+', ' ', title)
    slug_title = _title_from_url_slug(page_url)
    if (not title) or _title_has_cjk_or_japanese(title):
        title = slug_title or title or 'xHamster video'
    title = re.sub(r'[\x00-\x1f\x7f]+', ' ', title).strip()
    return title or 'xHamster video'


def _extract_from_html(html: str, page_url: str):
    base = _base_of(page_url)
    ua = _random_ua()
    headers = {"User-Agent": ua, "Referer": page_url, "Origin": base}

    title = None
    duration = None
    initials = _extract_window_initials(html)
    if isinstance(initials, dict):
        vm = initials.get("videoModel")
        if isinstance(vm, dict):
            title = vm.get("title") or title
            if isinstance(vm.get("duration"), (int, float)):
                duration = int(vm["duration"])

    if not title:
        tm = (
            re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html, re.I)
            or re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        )
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()

    candidates = _find_m3u8_candidates(html)
    master = _pick_best_master(candidates)
    if not master and isinstance(initials, dict):
        master = _find_hls_from_initials(initials)

    if not master:
        logger.warning(
            "xhamster: master not found (initials_parsed=%s, m3u8_candidates=%s, top_keys=%s)",
            isinstance(initials, dict), len(candidates), list(initials.keys())[:12] if isinstance(initials, dict) else []
        )
        return None

    heights = []
    try:
        mh = {"User-Agent": _random_ua(), "Referer": page_url, "Origin": base}
        for _attempt in range(3):
            r = requests.get(master, headers=mh, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                heights = _heights_from_master(r.text)
                break
            elif r.status_code == 429:
                wait = BASE_DELAY * (2 ** _attempt) + random.uniform(0.5, 2)
                logger.warning("xh master 429, retry %d after %.1fs", _attempt + 1, wait)
                time.sleep(wait)
                mh["User-Agent"] = _random_ua()
            else:
                break
    except Exception as e:
        logger.warning("xh master fetch fail: %s", e)

    if not heights:
        for m2 in re.finditer(r":(\d{3,4})p", master):
            heights.append(int(m2.group(1)))
        heights = sorted(set(heights))
    if not heights:
        heights = [144, 240, 480, 720]

    qualities = []
    for h in sorted(set(heights)):
        qualities.append({
            "height": h,
            "label": QLABEL.get(h, f"{h}p"),
            "m3u8": _build_variant_url(master, h),
        })

    return {
        "title": _clean_title(title, page_url),
        "duration": duration,
        "webpage_url": page_url,
        "base": base,
        "master_m3u8": master,
        "qualities": qualities,
        "headers": {"User-Agent": ua, "Referer": page_url, "Origin": base},
    }


def _build_browser_headers(ua, page_url, page_base, cookie_header=None):
    """Real browser jaisa headers banao."""
    h = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": page_url,
        "Origin": page_base,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    if cookie_header:
        h["Cookie"] = cookie_header
    return h


def extract(url: str, cookies_file: str = None):
    desktop = _clean_xhamster_page_url(_to_desktop(url))

    cookies, cookie_header = _load_netscape_cookies(cookies_file)
    if cookie_header:
        logger.info("xhamster: cookies loaded count=%s", len(cookies or {}))

    # ============================================================
    # 429 FIX: MIRROR ROTATION STRATEGY
    # Sabse pehle saare mirror domains banao, phir ek-ek try karo.
    # Ek domain pe 429 aaye toh TURANT doosra domain try karo
    # (kyunki har domain ka ALAG rate limit hota hai)
    # ============================================================
    mirror_urls = _get_mirror_urls(desktop)
    logger.info("xhamster: trying %d mirror domains for %s", len(mirror_urls), desktop[:80])

    session = requests.Session()
    tried = []
    best_response = None
    best_html = None
    best_url = None

    for mirror_idx, page_url in enumerate(mirror_urls[:5]):  # max 5 mirrors try
        page_url = _clean_xhamster_page_url(page_url)
        page_base = _base_of(page_url)

        if page_url in tried:
            continue

        success = False
        for attempt in range(MAX_RETRIES_PER_DOMAIN):
            ua = _random_ua()
            h = _build_browser_headers(ua, page_url, page_base, cookie_header)

            # Small random delay before request
            if attempt == 0 and mirror_idx == 0:
                time.sleep(random.uniform(0.5, 1.5))
            elif attempt > 0:
                # Retry on same domain - exponential backoff
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0.5, 2)
                logger.info("xhamster: retry domain=%s attempt=%d delay=%.1fs",
                           urlparse(page_url).hostname, attempt + 1, delay)
                time.sleep(delay)
                # New session for retry
                session.close()
                session = requests.Session()
                ua = _random_ua()
                h = _build_browser_headers(ua, page_url, page_base, cookie_header)
            else:
                # Domain switch - short delay
                time.sleep(random.uniform(0.3, DOMAIN_SWITCH_DELAY))

            try:
                r = session.get(
                    page_url, headers=h, cookies=cookies,
                    timeout=REQUEST_TIMEOUT, allow_redirects=True
                )
            except requests.exceptions.Timeout:
                logger.warning("xhamster: timeout mirror=%s attempt=%d",
                             urlparse(page_url).hostname, attempt + 1)
                continue
            except requests.exceptions.ConnectionError as ce:
                logger.warning("xhamster: connection error mirror=%s: %s",
                             urlparse(page_url).hostname, str(ce)[:80])
                break  # try next mirror
            except Exception as e:
                logger.warning("xhamster: fetch error mirror=%s: %s",
                             urlparse(page_url).hostname, str(e)[:80])
                break  # try next mirror

            status = r.status_code
            tried.append(page_url)

            # ============================================================
            # 429 HANDLING
            # ============================================================
            if status == 429:
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait_time = int(retry_after)
                    except ValueError:
                        wait_time = BASE_DELAY * (2 ** (attempt + 1))
                else:
                    wait_time = BASE_DELAY * (2 ** (attempt + 1)) + random.uniform(1, 3)

                logger.warning(
                    "xhamster: 429 on %s (attempt %d/%d), wait=%.1fs -> switching mirror",
                    urlparse(page_url).hostname, attempt + 1, MAX_RETRIES_PER_DOMAIN, wait_time
                )
                # Is domain pe dobara try nahi karna agar bahut 429 aa raha hai
                # TURANT next mirror try karo (different domain = different rate limit)
                if attempt >= MAX_RETRIES_PER_DOMAIN - 1:
                    # Exhausted retries on this domain, break and try next mirror
                    break
                continue

            # 403 = possible Cloudflare block
            if status == 403:
                logger.warning("xhamster: 403 on %s -> trying next mirror",
                             urlparse(page_url).hostname)
                break  # try next mirror

            # 200 = SUCCESS (ya koi aur non-error status)
            if status == 200:
                html_text = r.text
                if not html_text:
                    logger.warning("xhamster: empty html from %s", urlparse(page_url).hostname)
                    break

                # Check if video is closed/removed
                if re.search(r"videoClosed", html_text):
                    logger.info("xhamster: video closed/removed on %s", urlparse(page_url).hostname)
                    # Try next mirror - shayad doosre pe available ho
                    break

                # Check if this is a limited page (no player data)
                has_player, initials0 = _has_player_data(html_text)

                if has_player:
                    # GOT PLAYER DATA! This is the good response
                    logger.info("xhamster: SUCCESS mirror=%s html_len=%d has_player=True",
                              urlparse(page_url).hostname, len(html_text))
                    best_response = r
                    best_html = html_text
                    best_url = page_url
                    success = True
                    break
                else:
                    # Limited page - save it but keep trying for a better response
                    logger.info("xhamster: limited page from %s, trying next mirror",
                              urlparse(page_url).hostname)
                    if best_html is None:
                        best_response = r
                        best_html = html_text
                        best_url = page_url

                    # Check if initials have candidate video URLs
                    candidates_from_initials = _candidate_video_urls_from_initials(initials0, page_url)
                    # Don't try these as separate mirrors - they're already in our mirror list
                    break  # try next mirror
            else:
                # Some other error status
                logger.warning("xhamster: status %d from %s", status, urlparse(page_url).hostname)
                break

        if success:
            break  # We got a good response, stop trying mirrors

    # ============================================================
    # POST-PROCESSING
    # ============================================================
    if best_html is None:
        logger.error("xhamster: ALL mirrors failed (tried %d URLs)", len(tried))
        return None

    # If the best response was a limited page, try candidate URLs from initials
    has_player, initials0 = _has_player_data(best_html)
    if not has_player:
        candidates = _candidate_video_urls_from_initials(initials0, best_url)
        logger.info("xhamster: limited page fallback, %d candidate URLs", len(candidates))
        for cand in candidates:
            if cand in tried:
                continue
            try:
                ua = _random_ua()
                page_base = _base_of(cand)
                h = _build_browser_headers(ua, cand, page_base, cookie_header)
                time.sleep(random.uniform(0.5, 1.5))
                rr = session.get(cand, headers=h, cookies=cookies,
                               timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if rr.status_code in (429, 403):
                    logger.warning("xhamster: candidate %s -> %d, skip", urlparse(cand).hostname, rr.status_code)
                    continue
                hh = rr.text
                tried.append(cand)
                ok, _ = _has_player_data(hh)
                logger.info("xhamster: candidate url=%s status=%s html_len=%s player=%s",
                          cand[:80], rr.status_code, len(hh or ""), ok)
                if ok:
                    best_response, best_html, best_url = rr, hh, cand
                    break
            except Exception as e:
                logger.warning("xhamster: candidate retry failed url=%s err=%s", cand[:60], e)

    try:
        res = _extract_from_html(best_html, best_url)
        if not res:
            logger.warning(
                "xhamster: no m3u8 found; final_url=%s html_len=%s has_initials=%s tried=%s",
                best_url, len(best_html), "window.initials" in best_html, tried[-5:]
            )
        return res
    except Exception as e:
        logger.warning("xh extract error: %s", e)
        return None
    finally:
        session.close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    u = sys.argv[1] if len(sys.argv) > 1 else None
    if not u:
        print("Usage: python xhamster_engine.py <xhamster-url>")
        sys.exit(0)
    res = extract(u, "cookies.txt")
    if not res:
        print("FAIL: kuch nahi mila")
    else:
        print("TITLE   :", res["title"])
        print("DURATION:", res["duration"])
        print("MASTER  :", res["master_m3u8"][:120])
        for q in res["qualities"]:
            print(f"  [{q['height']:>5}] {q['label']:14} {q['m3u8'][:100]}")
