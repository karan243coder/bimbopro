# -*- coding: utf-8 -*-
# ============================================================
#  xHamster custom engine for Telegram bot
#  - Mirror rotation + UA rotation + cookies
#  - Free web proxy API fallback (fast, no SOCKS needed)
# ============================================================

import re
import json
import html as html_lib
import logging
import os
import random
import time
from urllib.parse import urlparse, unquote, quote

import requests

# cloudscraper: Cloudflare 429 bypass (optional but recommended)
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

logger = logging.getLogger(__name__)

# ============================================================
#  User-Agents pool
# ============================================================
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

UA = _UA_POOL[0]

# ============================================================
#  BUILT-IN COOKIES (xhamster19.com + xhamster46.desi)
# ============================================================
_BUILTIN_COOKIES_RAW = """\
.xhamster19.com\tTRUE\t/\tTRUE\t1785214900\tstats_src_last\txhamster19.com
.xhamster19.com\tTRUE\t/\tFALSE\t1816664479\tcookie_accept_v2\t%7B%22e%22%3A1%2C%22f%22%3A1%2C%22t%22%3A1%2C%22a%22%3A1%7D
.xhamster19.com\tTRUE\t/\tFALSE\t1816664493\tff_thumb_offset\t2
.xhamster19.com\tTRUE\t/\tFALSE\t1785129393\trecs_show_time\t1785128491
.xhamster19.com\tTRUE\t/\tFALSE\t1785130255\tlast_video_search\tnew
.xhamster19.com\tTRUE\t/\tFALSE\t1816664462\tsettings\teyJpc1dlYnBTdXBwb3J0ZWQiOnRydWUsImlzV2VibVN1cHBvcnRlZCI6dHJ1ZSwiZXh0RGV0ZWN0ZWRWMiI6ZmFsc2UsIm1vbWVudHNJc0hpZGRlbiI6bnVsbCwidHJ1c3RVUkxzIjpbInhoYW1zdGVyMTkuY29tIl0sImlzU2lkZWJhckhpZGRlbiI6bnVsbCwiZXhwaXJlcyI6eyJ0cnVzdFVSTHMiOjE3ODUxMzU2NTgsImV4dERldGVjdGVkVjIiOjE3ODUxMjg0NjJ9LCJ0c1Nwb3RDb3VudGVycyI6W3sic3BvdCI6Im1hc3Rlcl9jdWJlIiwidGltZSI6MTc4NTEyODQ1MywiY291bnQiOjF9LHsic3BvdCI6Im1hc3Rlcl9mb290ZXIiLCJ0aW1lIjoxNzg1MTI4NDUzLCJjb3VudCI6MX1dfQ%3D%3D
.xhamster19.com\tTRUE\t/\tFALSE\t1787720455\tsearch_last_list\t%5B%22new%22%5D
.xhamster19.com\tTRUE\t/\tTRUE\t1787720501\t_cfg\t0d2ef32fe1da1c13d4fd6f172ba5599c
.xhamster19.com\tTRUE\t/\tFALSE\t0\tx_csrf_token\t1
.xhamster19.com\tTRUE\t/\tFALSE\t1816664459\tparental-control\tyes
.xhamster19.com\tTRUE\t/\tTRUE\t1816664479\t_id\t516d861417ad9cb698f811b3da4e322aac091270
.xhamster19.com\tTRUE\t/\tFALSE\t1816664504\tUID\t377371408
.xhamster19.com\tTRUE\t/\tFALSE\t1819688480\tx_tgt\t%7B%22login%22%3A%2227-07-2026%22%7D
.xhamster19.com\tTRUE\t/\tTRUE\t1819688482\t_ga_T40T5YFNVL\tGS2.1.s1785128481$o1$g0$t1785128481$j60$l0$h1650839162
.xhamster19.com\tTRUE\t/\tTRUE\t1819688482\t_ga\tGA1.1.533356184.1785128482
.xhamster19.com\tTRUE\t/\tFALSE\t1816664493\tmoments_listing_ad_offset\t1
.xhamster19.com\tTRUE\t/\tFALSE\t1785733300\tx_viewes\t%5B26149458%5D
.xhamster19.com\tTRUE\t/\tFALSE\t1787720500\tx_content_preference_index\tstraight
.xhamster19.com\tTRUE\t/\tFALSE\t1787720502\th_v4_straight\t%7B%22v%22%3A%5B%5D%2C%22l%22%3A%5B%5D%2C%22f%22%3A%5B%5D%2C%22pv%22%3A%5B26149458%5D%7D
.xhamster19.com\tTRUE\t/\tTRUE\t1785130243\tx_preroll\t1
.xhamster19.com\tTRUE\t/\tTRUE\t1785130243\tx_preroll_shown\t1
.xhamster46.desi\tTRUE\t/one-tap-auth/iframe\tTRUE\t1813387635\t_id\t5684d8fbfa3db9db41598d7aebf9560e7543bce4
.xhamster46.desi\tTRUE\t/\tTRUE\t1787495580\t_cfg\tc0166ebae77528fb6593762db55ebe6f
.xhamster46.desi\tTRUE\t/\tFALSE\t1813387635\tcookie_accept_v2\t%7B%22e%22%3A1%2C%22f%22%3A1%2C%22t%22%3A1%2C%22a%22%3A1%7D
.xhamster46.desi\tTRUE\t/\tFALSE\t1787495580\tx_content_preference_index\tstraight
.xhamster46.desi\tTRUE\t/\tFALSE\t1813387594\tparental-control\tno
.xhamster46.desi\tTRUE\t/\tFALSE\t1813387686\tff_thumb_offset\t1
.xhamster46.desi\tTRUE\t/\tFALSE\t1813387686\tmoments_listing_ad_offset\t1
.xhamster46.desi\tTRUE\t/\tFALSE\t1819463122\tx_tgt\t%5B%5D
.xhamster46.desi\tTRUE\t/\tTRUE\t1784989980\tstats_src_last\tamp-xhamster-com.cdn.ampproject.org
.xhamster46.desi\tTRUE\t/\tFALSE\t1785507922\tx_viewes\t%5B12589312%5D
.xhamster46.desi\tTRUE\t/\tFALSE\t1816439581\tsettings\teyJpc1dlYnBTdXBwb3J0ZWQiOnRydWUsImlzV2VibVN1cHBvcnRlZCI6dHJ1ZSwiZXh0RGV0ZWN0ZWRWMiI6ZmFsc2UsIm1vbWVudHNJc0hpZGRlbiI6bnVsbCwidHJ1c3RVUkxzIjpbInhoYW1zdGVyNDYuZGVzaSIsImhpbC54aGFtc3RlcjQ2LmRlc2kiXSwiaXNTaWRlYmFySGlkZGVuIjpudWxsLCJleHBpcmVzIjp7ImV4dERldGVjdGVkVjIiOjE3ODQ5MDMxNTIsInRydXN0VVJMcyI6MTc4NDkxMDc4MX0sInRzU3BvdENvdW50ZXJzIjpbeyJzcG90IjoibmF0aXZlX3RodW1iIiwidGltZSI6MTc4NDkwMzEyMiwiY291bnQiOjJ9XX0%3D
.xhamster46.desi\tTRUE\t/\tFALSE\t1787495619\th_v4_straight\t%7B%22v%22%3A%5B12589312%5D%2C%22l%22%3A%5B%5D%2C%22f%22%3A%5B%5D%2C%22pv%22%3A%5B12589312%5D%7D
.xhamster46.desi\tTRUE\t/\tFALSE\t0\tx_csrf_token\t1
.xhamster46.desi\tTRUE\t/\tFALSE\t1784904487\trecs_show_time\t1784903588
.xhamster46.desi\tTRUE\t/\tTRUE\t1813387635\t_id\t5684d8fbfa3db9db41598d7aebf9560e7543bce4
.xhamster46.desi\tTRUE\t/\tFALSE\t1816439589\tUID\t351795720
"""

# ============================================================
#  Mirror domains (only real xhamster mirrors that serve same content)
# ============================================================
_MIRROR_HOSTS = [
    "xhamster46.desi",
    "xhamster.desi",
    "xhamster.com",
    "xhamster.one",
    "xhamster2.com",
    "xhamster3.com",
]

# ============================================================
#  FREE WEB PROXY APIs (FAST - no SOCKS, no testing needed!)
#  Ye APIs apne server se page fetch karti hain
#  = different IP = no rate limit!
# ============================================================
_WEB_PROXIES = [
    lambda url: f"https://api.codetabs.com/v1/proxy?quest={quote(url, safe='')}",
    lambda url: f"https://api.allorigins.win/raw?url={quote(url, safe='')}",
    lambda url: f"https://corsproxy.io/?{quote(url, safe='')}",
]

# ============================================================
#  CLOUDFLARE WORKER PROXY (BEST - set env var on Koyeb!)
#  Koyeb dashboard -> Environment -> XH_WORKER_URL = "https://your-worker.workers.dev"
# ============================================================
CF_WORKER_URL = os.environ.get("XH_WORKER_URL", "").strip()

# Config
REQUEST_TIMEOUT = 20
WEB_PROXY_TIMEOUT = 15
MAX_WEB_PROXY_ATTEMPTS = 6

def _random_ua():
    return random.choice(_UA_POOL)

# ============================================================
#  xHamster detection
# ============================================================
_XH_BRANDS = (
    "xhamster", "xhms", "xhday", "xhvid", "xhwide", "xhwebcam",
    "xhopen", "xhtab", "xhtotal", "xh_official", "xhaccess", "xhmoon",
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


def _parse_cookie_lines(lines):
    jar = {}
    parts_list = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = re.split(r"\s+", line, maxsplit=6)
        if len(parts) < 7:
            continue
        name = parts[5].strip()
        value = parts[6].strip()
        if not name:
            continue
        if name not in jar:
            jar[name] = value
            parts_list.append(f"{name}={value}")
    cookie_header = "; ".join(parts_list) if parts_list else None
    return (jar or None), cookie_header


def _load_cookies(cookies_file=None):
    if cookies_file:
        try:
            with open(cookies_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            jar, header = _parse_cookie_lines(lines)
            if jar:
                logger.info("xhamster: loaded %d cookies from file %s", len(jar), cookies_file)
                return jar, header
        except Exception as e:
            logger.warning("xhamster: file cookies load failed: %s", e)
    lines = _BUILTIN_COOKIES_RAW.strip().splitlines()
    jar, header = _parse_cookie_lines(lines)
    if jar:
        logger.info("xhamster: loaded %d BUILT-IN cookies", len(jar))
    return jar, header


def _candidate_video_urls_from_initials(initials, current_url):
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


def _has_player_data(html):
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


def _title_has_cjk_or_japanese(text):
    return any(
        ('\u3040' <= ch <= '\u30ff') or
        ('\u3400' <= ch <= '\u4dbf') or
        ('\u4e00' <= ch <= '\u9fff') or
        ('\uf900' <= ch <= '\ufaff')
        for ch in str(text or '')
    )


def _title_from_url_slug(page_url):
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


def _clean_title(title, page_url):
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


def _extract_from_html(html, page_url, cookie_header=None):
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
        logger.warning("xhamster: master not found")
        return None

    heights = []
    try:
        # Use cloudscraper for master m3u8 fetch (bypasses 429)
        if HAS_CLOUDSCRAPER:
            _m_session = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        else:
            _m_session = requests.Session()
        mh = _build_browser_headers(_random_ua(), page_url, base, cookie_header)
        for _attempt in range(3):
            r = _m_session.get(master, headers=mh, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                heights = _heights_from_master(r.text)
                break
            elif r.status_code == 429:
                time.sleep(2)
                mh = _build_browser_headers(_random_ua(), page_url, base, cookie_header)
            else:
                break
        _m_session.close()
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


def _fetch_via_cf_worker(url, cookie_header=None):
    """Cloudflare Worker proxy se page fetch karo. FASTEST option!"""
    if not CF_WORKER_URL:
        return None

    worker_url = f"{CF_WORKER_URL}?url={quote(url, safe='')}"

    # Try 1: WITHOUT cookies first (cookies kabhi kabhi error dete hain)
    for attempt, use_cookies in enumerate([False, True]):
        try:
            h = {"User-Agent": _random_ua()}
            if use_cookies and cookie_header:
                h["X-Forward-Cookies"] = cookie_header

            r = requests.get(worker_url, headers=h, timeout=WEB_PROXY_TIMEOUT, allow_redirects=True)

            if r.status_code == 200 and len(r.text) > 500:
                html_text = r.text
                if "window.initials" in html_text or "videoModel" in html_text:
                    logger.info("xhamster: CF WORKER SUCCESS (cookies=%s, html_len=%d)",
                              use_cookies, len(html_text))
                    return html_text
                elif "xhamster" in html_text.lower():
                    logger.info("xhamster: CF worker got xhamster page but no player data (cookies=%s)", use_cookies)
                    if not use_cookies:
                        continue  # Try with cookies
                    return html_text  # Return anyway
                else:
                    logger.warning("xhamster: CF worker returned non-xhamster content")
            elif r.status_code in (520, 521, 522, 523, 524):
                # Cloudflare origin errors - xhamster might be blocking this request
                logger.warning("xhamster: CF worker got origin error %d (cookies=%s)", r.status_code, use_cookies)
                if not use_cookies:
                    continue  # Try with cookies
            elif r.status_code == 403:
                logger.warning("xhamster: CF worker got 403 (cookies=%s)", use_cookies)
                if not use_cookies:
                    continue
            elif r.status_code == 400:
                logger.warning("xhamster: CF worker got 400 (bad URL)")
                return None  # Don't retry with cookies
            else:
                logger.warning("xhamster: CF worker status=%d len=%d", r.status_code, len(r.text))
                if not use_cookies:
                    continue
        except Exception as e:
            logger.warning("xhamster: CF worker error: %s", str(e)[:80])
            return None

    return None


def _fetch_via_web_proxy(url, cookie_header=None):
    """Free web proxy API se page fetch karo. Ye FAST hai (no SOCKS testing needed)."""
    proxies = list(_WEB_PROXIES)
    random.shuffle(proxies)

    for proxy_fn in proxies:
        proxy_url = proxy_fn(url)
        try:
            # Use cloudscraper for proxy requests too
            if HAS_CLOUDSCRAPER:
                _s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
            else:
                _s = requests.Session()
            h = {"User-Agent": _random_ua()}
            if cookie_header:
                h["X-Cookie"] = cookie_header
            r = _s.get(proxy_url, headers=h, timeout=WEB_PROXY_TIMEOUT, allow_redirects=True)
            _s.close()

            if r.status_code == 200 and len(r.text) > 500:
                html_text = r.text
                if "window.initials" in html_text or "xhamster" in html_text.lower():
                    logger.info("xhamster: web proxy SUCCESS via %s (html_len=%d)",
                              proxy_url[:60], len(html_text))
                    return html_text
                else:
                    logger.warning("xhamster: web proxy returned non-xhamster content")
            elif r.status_code == 200:
                logger.warning("xhamster: web proxy response too small (%d bytes)", len(r.text))
            else:
                logger.warning("xhamster: web proxy status=%d", r.status_code)
        except Exception as e:
            logger.warning("xhamster: web proxy error: %s", str(e)[:80])

    return None


def extract(url, cookies_file=None):
    desktop = _clean_xhamster_page_url(_to_desktop(url))

    cookies, cookie_header = _load_cookies(cookies_file)
    mirror_urls = _get_mirror_urls(desktop)
    logger.info("xhamster: %d mirrors, cookies=%s, cloudscraper=%s",
               len(mirror_urls), "YES" if cookie_header else "NO",
               "YES" if HAS_CLOUDSCRAPER else "NO")

    # Use cloudscraper if available (bypasses Cloudflare 429)
    if HAS_CLOUDSCRAPER:
        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    else:
        session = requests.Session()
    tried = []
    best_html = None
    best_url = None
    success = False

    # ============================================================
    #  PHASE 1: Direct requests with mirror rotation
    # ============================================================
    for mirror_idx, page_url in enumerate(mirror_urls[:4]):
        page_url = _clean_xhamster_page_url(page_url)
        page_base = _base_of(page_url)

        if page_url in tried:
            continue

        ua = _random_ua()
        h = _build_browser_headers(ua, page_url, page_base, cookie_header)

        if mirror_idx > 0:
            time.sleep(random.uniform(0.3, 0.8))

        try:
            r = session.get(page_url, headers=h, cookies=cookies,
                          timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except Exception as e:
            logger.warning("xhamster: fetch error %s: %s", urlparse(page_url).hostname, str(e)[:60])
            break

        status = r.status_code
        tried.append(page_url)
        host = urlparse(page_url).hostname

        if status in (429, 400, 403):
            logger.warning("xhamster: %d on %s -> next", status, host)
            continue

        if status == 200 and r.text:
            if re.search(r"videoClosed", r.text):
                continue
            has_player, _ = _has_player_data(r.text)
            if has_player:
                logger.info("xhamster: SUCCESS direct mirror=%s", host)
                best_html = r.text
                best_url = page_url
                success = True
                break
            else:
                if best_html is None:
                    best_html = r.text
                    best_url = page_url
                logger.info("xhamster: limited page from %s", host)

    # ============================================================
    #  PHASE 2: CLOUDFLARE WORKER proxy (FASTEST! Different IP)
    # ============================================================
    if not success and CF_WORKER_URL:
        logger.info("xhamster: trying Cloudflare Worker proxy...")

        proxy_urls = [desktop]
        for mu in mirror_urls[:3]:
            if mu not in proxy_urls:
                proxy_urls.append(mu)

        for purl in proxy_urls:
            html_text = _fetch_via_cf_worker(purl, cookie_header)
            if html_text:
                has_player, initials0 = _has_player_data(html_text)
                if has_player:
                    best_html = html_text
                    best_url = purl
                    success = True
                    break
                else:
                    candidates = _candidate_video_urls_from_initials(initials0, purl)
                    for cand in candidates[:3]:
                        if cand == purl:
                            continue
                        cand_html = _fetch_via_cf_worker(cand, cookie_header)
                        if cand_html:
                            ok, _ = _has_player_data(cand_html)
                            if ok:
                                best_html = cand_html
                                best_url = cand
                                success = True
                                break
                    if success:
                        break
                    if best_html is None:
                        best_html = html_text
                        best_url = purl

    # ============================================================
    #  PHASE 3: FREE WEB PROXY fallback
    # ============================================================
    if not success:
        logger.info("xhamster: trying free web proxy APIs...")

        # Try original URL first via web proxy
        proxy_urls = [desktop]
        # Add some mirrors
        for mu in mirror_urls[:3]:
            if mu not in proxy_urls:
                proxy_urls.append(mu)

        for purl in proxy_urls:
            html_text = _fetch_via_web_proxy(purl, cookie_header)
            if html_text:
                has_player, initials0 = _has_player_data(html_text)
                if has_player:
                    best_html = html_text
                    best_url = purl
                    success = True
                    break
                else:
                    # Limited page from proxy - try candidate URLs
                    candidates = _candidate_video_urls_from_initials(initials0, purl)
                    for cand in candidates[:3]:
                        if cand == purl:
                            continue
                        cand_html = _fetch_via_web_proxy(cand, cookie_header)
                        if cand_html:
                            ok, _ = _has_player_data(cand_html)
                            if ok:
                                best_html = cand_html
                                best_url = cand
                                success = True
                                break
                    if success:
                        break
                    if best_html is None:
                        best_html = html_text
                        best_url = purl

    # ============================================================
    #  EXTRACT
    # ============================================================
    if not best_html:
        logger.error("xhamster: ALL methods failed (direct + web proxy)")
        return None

    try:
        res = _extract_from_html(best_html, best_url, cookie_header)
        if not res:
            logger.warning("xhamster: extraction failed url=%s", best_url[:80])
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
        print("Usage: python xhamster_engine.py <url>")
        sys.exit(0)
    res = extract(u)
    if not res:
        print("FAIL")
    else:
        print("TITLE   :", res["title"])
        print("DURATION:", res["duration"])
        print("MASTER  :", res["master_m3u8"][:120])
        for q in res["qualities"]:
            print(f"  [{q['height']:>5}] {q['label']:14} {q['m3u8'][:100]}")
