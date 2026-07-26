# -*- coding: utf-8 -*-
# ============================================================
#  xHamster custom engine for Telegram bot
#  - yt-dlp info extractor bypass
#  - Finds plaintext/escaped/encrypted HLS
#  - Builds h264 HLS quality URLs
# ============================================================

import re
import json
import html as html_lib
import logging
from urllib.parse import urlparse, unquote

import requests

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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
    """xHamster share URL se tracking query hatao. Koyeb par ?utm_source=ext_shared page me sources nahi aate."""
    url = html_lib.unescape(str(url or "").strip())
    m = re.search(r"https?://[^\s<>\"']+", url)
    if m:
        url = m.group(0)
    url = url.strip().strip("`'\"<>[]()")
    try:
        p = urlparse(url)
        # xHamster video pages ke liye query/fragment hamesha remove karo
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

    # IMPORTANT: xHamster mirror normalize fallback.
    # Koyeb par xhamster46.desi kabhi limited page deta hai, same slug xhamster.desi/com par playable ho sakta hai.
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

    # Favorites/watch-later links ko remove karo; ye playable video page nahi hote
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
        ('\u3040' <= ch <= '\u30ff') or  # Hiragana/Katakana
        ('\u3400' <= ch <= '\u4dbf') or  # CJK Ext A
        ('\u4e00' <= ch <= '\u9fff') or  # CJK
        ('\uf900' <= ch <= '\ufaff')     # CJK Compatibility
        for ch in str(text or '')
    )


def _title_from_url_slug(page_url: str):
    """URL slug se readable English-style title banao."""
    try:
        slug = urlparse(page_url).path.rstrip('/').split('/')[-1]
        if not slug:
            return None
        # Last xhID part remove: title-xhAbCd / title-12345
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
    # control chars remove
    title = re.sub(r'[\x00-\x1f\x7f]+', ' ', title).strip()
    return title or 'xHamster video'


def _extract_from_html(html: str, page_url: str):
    base = _base_of(page_url)
    headers = {"User-Agent": UA, "Referer": page_url, "Origin": base}

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
        r = requests.get(master, headers=headers, timeout=20)
        if r.status_code == 200:
            heights = _heights_from_master(r.text)
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
        "headers": {"User-Agent": UA, "Referer": page_url, "Origin": base},
    }


def extract(url: str, cookies_file: str = None):
    desktop = _clean_xhamster_page_url(_to_desktop(url))
    base = _base_of(desktop)
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": desktop,
        "Origin": base,
    }

    cookies, cookie_header = _load_netscape_cookies(cookies_file)
    if cookie_header:
        headers["Cookie"] = cookie_header
        logger.info("xhamster: cookies loaded count=%s", len(cookies or {}))

    session = requests.Session()

    def fetch_page(page_url):
        page_url = _clean_xhamster_page_url(page_url)
        page_base = _base_of(page_url)
        h = dict(headers)
        h["Referer"] = page_url
        h["Origin"] = page_base
        if cookie_header:
            h["Cookie"] = cookie_header
        return session.get(page_url, headers=h, cookies=cookies, timeout=25, allow_redirects=True)

    tried = []
    try:
        r = fetch_page(desktop)
        html = r.text
        tried.append(desktop)
    except Exception as e:
        logger.warning("xh page fetch fail: %s", e)
        return None

    if not html:
        logger.warning("xhamster: empty html")
        return None
    if re.search(r"videoClosed", html):
        logger.info("xhamster: video closed/removed")
        return None

    # Koyeb pe kabhi share/limited page milta hai jisme videoModel/xplayerSettings nahi hota,
    # par initials['urls'] me canonical/fallback playable URLs hote hain. Unko retry karo.
    has_player, initials0 = _has_player_data(html)
    if not has_player:
        candidates = _candidate_video_urls_from_initials(initials0, desktop)
        logger.info("xhamster: limited page, retry candidate urls count=%s", len(candidates))
        for cand in candidates:
            if cand in tried:
                continue
            try:
                rr = fetch_page(cand)
                hh = rr.text
                tried.append(cand)
                ok, _ = _has_player_data(hh)
                logger.info("xhamster: retry url=%s status=%s html_len=%s player=%s", cand, getattr(rr, "status_code", None), len(hh or ""), ok)
                if ok:
                    r, html, desktop = rr, hh, cand
                    break
            except Exception as e:
                logger.warning("xhamster: retry failed url=%s err=%s", cand, e)

    try:
        res = _extract_from_html(html, desktop)
        if not res:
            logger.warning(
                "xhamster: no m3u8 found; status=%s final_url=%s html_len=%s has_initials=%s tried=%s",
                getattr(r, "status_code", None), getattr(r, "url", desktop), len(html), "window.initials" in html, tried[-5:]
            )
        return res
    except Exception as e:
        logger.warning("xh extract error: %s", e)
        return None


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
