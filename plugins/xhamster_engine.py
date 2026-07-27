# -*- coding: utf-8 -*-
# ============================================================
#  xHamster custom engine for Telegram bot
#  - yt-dlp info extractor bypass
#  - Finds plaintext/escaped/encrypted HLS
#  - Builds h264 HLS quality URLs
#  - 429 fix: mirror rotation + retry + UA rotation + PROXY
# ============================================================

import re
import json
import html as html_lib
import logging
import random
import time
import os
import threading
from urllib.parse import urlparse, unquote

import requests

logger = logging.getLogger(__name__)

# ============================================================
#  User-Agents pool for rotation
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

UA = _UA_POOL[0]

# ============================================================
#  Mirror domains
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
MAX_RETRIES_PER_DOMAIN = 2
BASE_DELAY = 2
MAX_DELAY = 20
REQUEST_TIMEOUT = 25
DOMAIN_SWITCH_DELAY = 1.0

# ============================================================
#  FREE PROXY POOL - IP block ka permanent solution!
#  GitHub se maintained proxy lists fetch karo
# ============================================================
_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
]

_PROXY_CACHE_FILE = os.path.join(
    os.environ.get("BIMBO_DOWNLOAD_LOCATION", "/tmp"),
    "xh_proxy_cache.txt"
)
_PROXY_CACHE_MAX_AGE = 3600   # 1 hour cache
_PROXY_TEST_TIMEOUT = 5       # 5 sec me proxy test karo
_PROXY_FETCH_TIMEOUT = 10     # proxy list fetch timeout
_MAX_PROXIES_IN_POOL = 200    # max proxies rakho pool me
_MAX_WORKING_PROXIES = 15     # max working proxies test karo

# Global proxy state (thread-safe)
_proxy_lock = threading.Lock()
_proxy_pool = []               # list of working proxy strings "ip:port"
_proxy_last_refresh = 0       # timestamp of last refresh
_proxy_raw_cache = []          # raw (untested) proxies from GitHub


def _random_ua():
    return random.choice(_UA_POOL)


def _download_proxy_list():
    """GitHub se fresh proxy list download karo."""
    all_proxies = set()
    for src in _PROXY_SOURCES:
        try:
            r = requests.get(src, timeout=_PROXY_FETCH_TIMEOUT,
                           headers={"User-Agent": _random_ua()})
            if r.status_code == 200:
                lines = r.text.strip().splitlines()
                for line in lines:
                    line = line.strip()
                    # Basic validation: ip:port format
                    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}$', line):
                        all_proxies.add(line)
                logger.info("xhamster proxy: fetched %d from %s", len(lines), src[:60])
        except Exception as e:
            logger.warning("xhamster proxy: fetch failed %s: %s", src[:50], e)

    proxies = list(all_proxies)
    random.shuffle(proxies)
    return proxies[:_MAX_PROXIES_IN_POOL]


def _test_proxy(proxy_str, test_url="https://xhamster.com"):
    """Ek proxy ko test karo - kya yeh xhamster tak pahunch sakta hai?"""
    try:
        proxies = {"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}
        r = requests.head(
            test_url,
            proxies=proxies,
            timeout=_PROXY_TEST_TIMEOUT,
            headers={"User-Agent": _random_ua()},
            allow_redirects=True,
        )
        # 200, 301, 302, 403 sab theek hain - 429 nahi hona chahiye
        if r.status_code in (200, 301, 302, 303, 307, 308, 403):
            return True
        if r.status_code == 429:
            return False  # proxy bhi rate limited hai
        # Other errors
        return r.status_code < 500
    except Exception:
        return False


def _refresh_proxy_pool(force=False):
    """Proxy pool refresh karo - cache se ya fresh download."""
    global _proxy_pool, _proxy_last_refresh, _proxy_raw_cache

    now = time.time()

    with _proxy_lock:
        # Cache valid hai toh mat refresh karo
        if not force and _proxy_pool and (now - _proxy_last_refresh) < _PROXY_CACHE_MAX_AGE:
            return

        # Check file cache first
        if not force and os.path.exists(_PROXY_CACHE_FILE):
            try:
                mtime = os.path.getmtime(_PROXY_CACHE_FILE)
                if (now - mtime) < _PROXY_CACHE_MAX_AGE:
                    with open(_PROXY_CACHE_FILE, "r") as f:
                        cached = [l.strip() for l in f if l.strip()]
                    if cached:
                        _proxy_pool = cached
                        _proxy_last_refresh = now
                        logger.info("xhamster proxy: loaded %d from cache file", len(cached))
                        return
            except Exception:
                pass

    # Fresh download (outside lock for network I/O)
    logger.info("xhamster proxy: downloading fresh proxy list...")
    raw_proxies = _download_proxy_list()

    if not raw_proxies:
        logger.warning("xhamster proxy: no proxies downloaded!")
        return

    # Test proxies (only test a subset to save time)
    working = []
    to_test = raw_proxies[:60]  # 60 proxies test karo, kaafi hain

    logger.info("xhamster proxy: testing %d proxies...", len(to_test))
    for proxy in to_test:
        if len(working) >= _MAX_WORKING_PROXIES:
            break
        if _test_proxy(proxy):
            working.append(proxy)
            logger.info("xhamster proxy: WORKING %s (%d/%d)",
                       proxy, len(working), _MAX_WORKING_PROXIES)

    with _proxy_lock:
        if working:
            _proxy_pool = working
            _proxy_last_refresh = time.time()
            # Save to cache file
            try:
                os.makedirs(os.path.dirname(_PROXY_CACHE_FILE), exist_ok=True)
                with open(_PROXY_CACHE_FILE, "w") as f:
                    f.write("\n".join(working))
            except Exception:
                pass
            logger.info("xhamster proxy: %d working proxies ready!", len(working))
        else:
            logger.warning("xhamster proxy: NO working proxies found from %d tested", len(to_test))
            # Keep old pool if available
            if not _proxy_pool:
                _proxy_pool = []


def _get_proxy():
    """Ek random working proxy lo pool se."""
    with _proxy_lock:
        if not _proxy_pool:
            return None
        proxy = random.choice(_proxy_pool)
        return {"http": f"http://{proxy}", "https": f"http://{proxy}"}


def _remove_bad_proxy(proxy_dict):
    """Jo proxy kaam nahi kar rahi usko pool se hatao."""
    if not proxy_dict:
        return
    # Extract proxy string from dict
    proxy_str = proxy_dict.get("http", "").replace("http://", "")
    with _proxy_lock:
        if proxy_str in _proxy_pool:
            _proxy_pool.remove(proxy_str)
            logger.info("xhamster proxy: removed bad proxy %s (%d remaining)",
                       proxy_str, len(_proxy_pool))


def _start_proxy_refresh():
    """Background me proxy pool refresh karo (non-blocking)."""
    def _bg_refresh():
        try:
            _refresh_proxy_pool()
        except Exception as e:
            logger.warning("xhamster proxy: background refresh error: %s", e)
    t = threading.Thread(target=_bg_refresh, daemon=True)
    t.start()


# ============================================================
#  xHamster detection
# ============================================================
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


def _get_mirror_urls(url: str):
    url = _clean_xhamster_page_url(_to_desktop(url))
    try:
        parsed = urlparse(url)
        path = parsed.path
        original_host = (parsed.hostname or "").lower()
    except Exception:
        return [url]

    if "/videos/" not in path and "/movies/" not in path:
        return [url]

    mirror_urls = []
    seen = set()

    if url not in seen:
        mirror_urls.append(url)
        seen.add(url)

    for host in _MIRROR_HOSTS:
        mirror_url = f"https://{host}{path}"
        if mirror_url not in seen:
            mirror_urls.append(mirror_url)
            seen.add(mirror_url)

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
        if "_tpl_" in lu: sc += 100
        if "hls" in lu: sc += 40
        if "h264" in lu: sc += 30
        if "av1" in lu: sc += 10
        if "multi=" in lu: sc += 20
        if "/seg-" in lu: sc -= 100
        return sc
    return sorted(candidates, key=score, reverse=True)[0]


def _ytdlp_decipher():
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
            for host in _MIRROR_HOSTS:
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
    return any(
        ('\u3040' <= ch <= '\u30ff') or
        ('\u3400' <= ch <= '\u4dbf') or
        ('\u4e00' <= ch <= '\u9fff') or
        ('\uf900' <= ch <= '\ufaff')
        for ch in str(text or '')
    )


def _title_from_url_slug(page_url: str):
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
    title = html_lib.unescape(str(title or '')).strip()
    title = re.sub(r'\s+', ' ', title)
    slug_title = _title_from_url_slug(page_url)
    if (not title) or _title_has_cjk_or_japanese(title):
        title = slug_title or title or 'xHamster video'
    title = re.sub(r'[\x00-\x1f\x7f]+', ' ', title).strip()
    return title or 'xHamster video'


def _build_browser_headers(ua, page_url, page_base, cookie_header=None):
    h = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": page_url,
        "Origin": page_base,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }
    if cookie_header:
        h["Cookie"] = cookie_header
    return h


def _extract_from_html(html: str, page_url: str):
    base = _base_of(page_url)
    ua = _random_ua()

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
            "xhamster: master not found (initials_parsed=%s, m3u8_candidates=%s)",
            isinstance(initials, dict), len(candidates)
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


def extract(url: str, cookies_file: str = None):
    desktop = _clean_xhamster_page_url(_to_desktop(url))

    cookies, cookie_header = _load_netscape_cookies(cookies_file)
    if cookie_header:
        logger.info("xhamster: cookies loaded count=%s", len(cookies or {}))

    mirror_urls = _get_mirror_urls(desktop)
    logger.info("xhamster: trying %d mirror domains", len(mirror_urls))

    session = requests.Session()
    tried = []
    best_html = None
    best_url = None

    # ============================================================
    #  PHASE 1: Direct requests (no proxy) with mirror rotation
    # ============================================================
    for mirror_idx, page_url in enumerate(mirror_urls[:5]):
        page_url = _clean_xhamster_page_url(page_url)
        page_base = _base_of(page_url)

        if page_url in tried:
            continue

        for attempt in range(MAX_RETRIES_PER_DOMAIN):
            ua = _random_ua()
            h = _build_browser_headers(ua, page_url, page_base, cookie_header)

            if attempt == 0 and mirror_idx == 0:
                time.sleep(random.uniform(0.3, 1.0))
            elif attempt > 0:
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0.5, 2)
                time.sleep(delay)
                session.close()
                session = requests.Session()
                ua = _random_ua()
                h = _build_browser_headers(ua, page_url, page_base, cookie_header)
            else:
                time.sleep(random.uniform(0.2, DOMAIN_SWITCH_DELAY))

            try:
                r = session.get(page_url, headers=h, cookies=cookies,
                              timeout=REQUEST_TIMEOUT, allow_redirects=True)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                logger.warning("xhamster: direct fetch error mirror=%s", urlparse(page_url).hostname)
                break
            except Exception as e:
                logger.warning("xhamster: direct fetch error: %s", str(e)[:80])
                break

            tried.append(page_url)

            if r.status_code == 429:
                logger.warning("xhamster: 429 on %s -> next mirror", urlparse(page_url).hostname)
                break

            if r.status_code == 403:
                logger.warning("xhamster: 403 on %s -> next mirror", urlparse(page_url).hostname)
                break

            if r.status_code == 200 and r.text:
                if re.search(r"videoClosed", r.text):
                    break
                has_player, initials0 = _has_player_data(r.text)
                if has_player:
                    logger.info("xhamster: SUCCESS direct mirror=%s", urlparse(page_url).hostname)
                    best_html = r.text
                    best_url = page_url
                    break
                else:
                    if best_html is None:
                        best_html = r.text
                        best_url = page_url
                    break
            else:
                break

        if best_html and _has_player_data(best_html)[0]:
            break

    # ============================================================
    #  PHASE 2: PROXY fallback (agar sab direct mirrors fail)
    # ============================================================
    if not best_html or not _has_player_data(best_html)[0]:
        logger.info("xhamster: direct failed, trying PROXY route...")

        # Background me proxy refresh start karo (agar pool empty)
        _start_proxy_refresh()

        # Thoda wait karo proxies load hone ka
        time.sleep(2)
        _refresh_proxy_pool()

        # Proxy se try karo
        proxy_attempts = 0
        MAX_PROXY_ATTEMPTS = 8

        while proxy_attempts < MAX_PROXY_ATTEMPTS:
            proxy = _get_proxy()
            if not proxy:
                logger.warning("xhamster: no proxies available in pool!")
                # Try to refresh one more time
                _refresh_proxy_pool(force=True)
                proxy = _get_proxy()
                if not proxy:
                    break

            # Random mirror choose karo proxy ke liye
            page_url = random.choice(mirror_urls[:4])
            page_url = _clean_xhamster_page_url(page_url)
            page_base = _base_of(page_url)
            ua = _random_ua()
            h = _build_browser_headers(ua, page_url, page_base, cookie_header)

            proxy_str = proxy.get("http", "").replace("http://", "")
            logger.info("xhamster: proxy attempt %d/%d proxy=%s mirror=%s",
                       proxy_attempts + 1, MAX_PROXY_ATTEMPTS,
                       proxy_str, urlparse(page_url).hostname)

            try:
                # Naya session for proxy (no cookie leak)
                ps = requests.Session()
                r = ps.get(
                    page_url, headers=h, cookies=cookies,
                    proxies=proxy, timeout=15, allow_redirects=True
                )
                ps.close()
            except requests.exceptions.ProxyError:
                logger.warning("xhamster: proxy error %s", proxy_str)
                _remove_bad_proxy(proxy)
                proxy_attempts += 1
                continue
            except requests.exceptions.Timeout:
                logger.warning("xhamster: proxy timeout %s", proxy_str)
                _remove_bad_proxy(proxy)
                proxy_attempts += 1
                continue
            except Exception as e:
                logger.warning("xhamster: proxy fetch error %s: %s", proxy_str, str(e)[:60])
                _remove_bad_proxy(proxy)
                proxy_attempts += 1
                continue

            proxy_attempts += 1

            if r.status_code == 429:
                logger.warning("xhamster: proxy %s also got 429, removing", proxy_str)
                _remove_bad_proxy(proxy)
                continue

            if r.status_code in (403, 500, 502, 503):
                logger.warning("xhamster: proxy %s got %d, removing", proxy_str, r.status_code)
                _remove_bad_proxy(proxy)
                continue

            if r.status_code == 200 and r.text:
                if re.search(r"videoClosed", r.text):
                    continue
                has_player, _ = _has_player_data(r.text)
                if has_player:
                    logger.info("xhamster: SUCCESS via proxy=%s mirror=%s",
                              proxy_str, urlparse(page_url).hostname)
                    best_html = r.text
                    best_url = page_url
                    break
                else:
                    if best_html is None:
                        best_html = r.text
                        best_url = page_url

            time.sleep(random.uniform(0.5, 1.5))

    # ============================================================
    #  POST-PROCESSING: candidate URLs from initials
    # ============================================================
    if best_html:
        has_player, initials0 = _has_player_data(best_html)
        if not has_player:
            candidates = _candidate_video_urls_from_initials(initials0, best_url)
            logger.info("xhamster: limited page, %d candidates", len(candidates))
            for cand in candidates[:3]:
                if cand in tried:
                    continue
                try:
                    ua = _random_ua()
                    h = _build_browser_headers(ua, cand, _base_of(cand), cookie_header)
                    time.sleep(random.uniform(0.3, 1.0))
                    rr = session.get(cand, headers=h, cookies=cookies,
                                   timeout=REQUEST_TIMEOUT, allow_redirects=True)
                    if rr.status_code in (429, 403):
                        continue
                    if rr.status_code == 200 and rr.text:
                        ok, _ = _has_player_data(rr.text)
                        if ok:
                            best_html, best_url = rr.text, cand
                            break
                except Exception:
                    pass

    # ============================================================
    #  EXTRACT
    # ============================================================
    if not best_html:
        logger.error("xhamster: ALL methods failed (direct + proxy)")
        return None

    try:
        res = _extract_from_html(best_html, best_url)
        if not res:
            logger.warning("xhamster: extraction failed final_url=%s", best_url[:80])
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
