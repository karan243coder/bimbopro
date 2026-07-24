# -*- coding: utf-8 -*-
# BIMBO v4.0 — xHamster ULTIMATE Plugin
# Supports:
#   • Single videos (already via xhamster_engine.py)
#   • Photo galleries (/gallery/<id>-<slug>)  → photos zip + send as album
#   • Albums / creator videos (/creators/<u>/videos, /users/<u>/videos-porn)
#   • Pornstars / channels / model profile  → list videos paginated
#   • Search (/search?q=<query>)             → inline buttons to pick video
#   • Categories / tags                     → top videos with pagination
#
# Commands:
#   /xh <url>           — auto-detect URL type (video/gallery/profile/search)
#   /xhs <query>        — search xHamster
#   /xhg <gallery url>  — gallery photos download
#   /xhp <profile url>  — list videos from a user/profile/pornstar
#   /xhalbum <album url> — download all videos from a creator's album
#
# This plugin uses existing xhamster_engine.extract() for video HLS extraction
# and adds aiohttp-based scraping for galleries/profiles/search.
import os
import re
import json
import math
import time
import asyncio
import logging
import html as html_lib
import zipfile
import functools
import gc
from urllib.parse import urlparse, urljoin, quote, parse_qs

import aiohttp
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, CallbackQuery,
)

from config import Config
from utils import (
    safe_filename, user_download_dir, cleanup_dir, humanbytes, run_cmd,
    is_admin, is_premium, rate_limit_check, get_url,
)
from database.xhamster_queue import create_job, get_job, claim_next_item, finish_item, update_job, cancel_job, pause_job, resume_job
from plugins.xhamster_engine import (
    is_xhamster, _extract_window_initials, _clean_xhamster_page_url,
    _base_of, UA, QLABEL, extract as xh_extract, _normalize_html_for_urls,
)


# --------------- CROSS-VERSION SAFE COMMAND FILTER (same as pro_settings) ---------------
def _cmd(*names):
    """Custom command filter — works with any Pyrogram version (tuple/list bug free)."""
    names = [n.lower().lstrip("/") for n in names]
    def f(_flt, _client, m: Message):
        if not m or not getattr(m, "text", None):
            return False
        if m.media:
            return False
        t = (m.text or "").strip()
        if not t.startswith("/"):
            return False
        first = t.split()[0][1:].split("@")[0].lower()
        return first in names
    return filters.create(f)


# ------------------------------------------------------------------
# Premium gate: advanced xHamster features (search/profile/gallery/
# listings) SIRF admin ya premium users ke liye. Normal users ko
# bas direct video link bhejne ka simple flow rahega (jo pehle se
# youtube_dl_echo.py me hai — quality buttons via apna custom engine).
# ------------------------------------------------------------------
async def _xh_vip_allowed(m: Message) -> bool:
    uid = m.from_user.id if m.from_user else 0
    if is_admin(uid):
        return True
    try:
        if await is_premium(uid):
            return True
    except Exception:
        pass
    return False


_XH_VIP_DENY_TEXT = (
    "🔒 **xHamster Advanced Features Premium Only!** 💎\n\n"
    "Ye features (🔍 Search, 👤 Profile videos, 🖼️ Gallery download, "
    "📃 Paginated listings) sirf **Premium users** ya **Admin** ke liye hain.\n\n"
    "✅ Normal users ke liye: bas kisi xHamster **single video ka direct link** "
    "bhejo — main auto quality select karke download kar dunga (pehle ki tarah)! "
    "Kisi special command ki zarurat nahi hai.\n\n"
    "Premium lene ke liye owner se sampark karo: @bimbobot69"
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
}

# URL type patterns
RE_GALLERY = re.compile(r"/gallery(?:ies)?/(\d+)", re.I)
RE_PHOTO   = re.compile(r"/photos/|/gallery/", re.I)
RE_VIDEO   = re.compile(r"/videos/|/movies/", re.I)
RE_CREATOR = re.compile(r"/(?:creators|users|pornstars?|channels?|models?|pornstar-channels)/([^/]+)", re.I)
RE_SEARCH  = re.compile(r"/(?:search|find|s)\b", re.I)
RE_TAG     = re.compile(r"/(?:tags|categories)/([^/]+)", re.I)


def _xh_type(url: str) -> str:
    url = url or ""
    if RE_GALLERY.search(url):
        return "gallery"
    if RE_VIDEO.search(url):
        return "video"
    if RE_CREATOR.search(url) and "/videos" in url.lower():
        return "creator_videos"
    if RE_CREATOR.search(url):
        return "profile"
    if RE_SEARCH.search(url):
        return "search"
    if RE_TAG.search(url):
        return "tag"
    if RE_PHOTO.search(url):
        return "gallery"
    return "unknown"


async def _xh_get(session: aiohttp.ClientSession, url: str, referer=None, try_endpoint_fallback=True):
    """Fetch xHamster URL; if any creator/listing page returns 4xx, auto-try
    multiple listing endpoints (/videos, /videos-porn, /new-videos, /videos/premium, etc)."""
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer

    async def _fetch(u):
        try:
            async with session.get(u, headers=h, ssl=False,
                                   timeout=aiohttp.ClientTimeout(total=30),
                                   allow_redirects=True) as r:
                return r.status, await r.text(), str(r.url)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            # A dead mirror/DNS failure must not abort the whole listing.
            logger.warning("xh fetch failed for %s: %s", u[:140], exc)
            return 0, "", u

    # Normalize URL: strip hash/query for fallback detection
    code, body, final_url = await _fetch(url)

    # If we got a non-2xx response on a creator/pornstar/channel/user URL,
    # try adding standard listing suffixes.
    if try_endpoint_fallback and (code == 0 or code >= 400):
        p = urlparse(url)
        path_parts = [s for s in p.path.rstrip("/").split("/") if s]
        if not path_parts:
            return code, body, final_url
        last_seg = path_parts[-1].lower()
        creator_sections = {"creators", "users", "pornstars", "channels", "models", "pornstar-channels"}

        # Case 1: URL ends with a section name (e.g., /creators/xxx/videos) -> try alternate listing endpoints
        possible_tries = []
        if last_seg in ("videos", "videos-porn", "new-videos", "videos-premium", "popular"):
            # xHamster mirrors do not expose the same endpoint consistently.
            # Try the profile root as well as all known listing suffixes.
            root_url = "/".join(url.rstrip("/").split("/")[:-1])
            possible_tries.append(root_url)
            alt_list = ["videos", "videos-porn", "new-videos", "popular"]
            for alt_seg in alt_list:
                if alt_seg != last_seg:
                    possible_tries.append(root_url + f"/{alt_seg}")
        elif len(path_parts) >= 2 and path_parts[-2].lower() in creator_sections:
            # Case 2: URL is profile root (e.g., /creators/xxx, no /videos suffix) -> try all listing endpoints
            username = last_seg
            section = path_parts[-2].lower()
            if section in ("pornstar-channels",):
                section = "channels"
            for seg in ("videos", "videos-porn", "new-videos", "popular"):
                possible_tries.append(f"{p.scheme}://{p.netloc}/{section}/{username}/{seg}")

        # Try each candidate URL
        for alt_url in possible_tries:
            logger.info(f"xh endpoint fallback: {code} on {url[:90]} -> trying {alt_url[:100]}")
            try:
                code2, body2, final2 = await _fetch(alt_url)
                if code2 == 200:
                    return code2, body2, final2
                code, body, final_url = code2, body2, final2
            except Exception:
                continue

    return code, body, final_url


# ================== GALLERY (photo) ==================
def _extract_gallery(html: str, base: str):
    """Extract image URLs from gallery page HTML."""
    soup = BeautifulSoup(html, "lxml")
    title = None
    tm = soup.find("meta", property="og:title")
    if tm:
        title = tm.get("content")
    if not title and soup.title:
        title = soup.title.string
    title = html_lib.unescape((title or "xHamster Gallery").strip())
    title = re.sub(r"\s+[\|\-–—]+\s*xHamster.*$", "", title, flags=re.I).strip()

    images = []
    # Try window.initials first (modern pages)
    initials = _extract_window_initials(html)
    if isinstance(initials, dict):
        # Walk for image URLs ending with .jpg/.jpeg/.png
        def _walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    yield from _walk(v)
            elif isinstance(o, list):
                for v in o:
                    yield from _walk(v)
            elif isinstance(o, str):
                yield o
        for s in _walk(initials):
            for m in re.finditer(r'https?://[^"\'\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', s, re.I):
                u = m.group(0).replace("\\/", "/")
                # image URLs usually contain 'thumb'/'gallery'/'image'/'ep'/'lh' CDN
                if any(k in u.lower() for k in ("gallery", "image", "thumb", "ep.xhcdn",
                                                "xhcdn.com/photos", "xhcdn.com/p",
                                                "m5p7", "img.")) and u not in images:
                    # remove thumbnail suffix to get full-res if possible
                    images.append(u)

    # BS fallback: <a rel="galleryPhotos"> or <img> in gallery container
    for img in soup.select("a.gallery-photo img, a[data-lightbox] img, .photo-thumb img, "
                           ".gallery-slider img, .xhc-gallery img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src:
            src = urljoin(base, src)
            if src not in images:
                images.append(src)

    # Open-graph image is single; find any CDN URLs in raw HTML
    for m in re.finditer(r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png)(?:\?[^"\'\s<>]*)?', html, re.I):
        u = m.group(0).replace("\\/", "/")
        if "xhamster" in u or "xhcdn" in u or "m5p7" in u:
            if u not in images:
                images.append(u)

    # Dedupe & promote big-res versions
    cleaned = []
    for u in images:
        # xhcdn thumbnail pattern: ..._<w>.jpg or ..._160.jpg; try replacing with _1000 / full
        big = re.sub(r"_\d+\.(jpg|jpeg|png|webp)(\?|$)", r"_1000.\1\2", u)
        if big not in cleaned:
            cleaned.append(big)
    return title[:120] if title else "xHamster Gallery", cleaned[:200]  # cap at 200 photos


async def _download_gallery_images(session: aiohttp.ClientSession, urls, out_dir: str, progress=None):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    sem = asyncio.Semaphore(6)  # 6 parallel downloads

    async def _one(i: int, u: str):
        async with sem:
            for attempt in range(3):
                try:
                    ext = ".jpg"
                    m = re.search(r"\.(jpg|jpeg|png|webp)", u, re.I)
                    if m:
                        ext = "." + m.group(1).lower().replace("jpeg", "jpg")
                    fn = os.path.join(out_dir, f"{i+1:03d}{ext}")
                    async with session.get(u, headers=HEADERS, ssl=False,
                                           timeout=aiohttp.ClientTimeout(total=30)) as r:
                        if r.status == 200:
                            data = await r.read()
                            if len(data) < 500:
                                continue
                            with open(fn, "wb") as f:
                                f.write(data)
                            paths.append((i, fn))
                            if progress:
                                await progress(len(paths), len(urls))
                            return
                except Exception as e:
                    await asyncio.sleep(1 + attempt)

    await asyncio.gather(*[_one(i, u) for i, u in enumerate(urls)])
    paths.sort()
    return [p for _, p in paths]


# ================== VIDEO LIST (profile/creator/search/tag) ==================
def _discover_next_from_initials(obj, base: str):
    """Find pagination/load-more URL or cursor in nested window.initials data.
    This is intentionally conservative: only values under pagination-like keys
    are accepted, so unrelated CDN URLs are never followed.
    """
    url_keys = {"next", "nextpage", "nextpageurl", "nexturl", "next_href", "nexturl",
                "loadmoreurl", "loadmore_url", "moreurl", "more_url", "href", "url"}
    container_keys = ("pagination", "paging", "pager", "loadmore", "load_more",
                      "infinite", "infinite_scroll", "pageinfo", "page_info", "navigation")
    found = []
    def walk(node, under=False):
        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).replace("-", "_").lower()
                active = under or kl in container_keys or "pagination" in kl or "loadmore" in kl or "nextpage" in kl
                if active and isinstance(v, str):
                    val = html_lib.unescape(v).replace("\\/", "/").strip()
                    if (kl in url_keys or "next" in kl or "load" in kl or "more" in kl) and (val.startswith("/") or val.startswith("http")):
                        u = urljoin(base, val)
                        if u not in found and is_xhamster(u): found.append(u)
                if isinstance(v, (dict, list)):
                    walk(v, active)
        elif isinstance(node, list):
            for v in node: walk(v, under)
    walk(obj)
    return found[0] if found else None


def _discover_cursor_endpoint(html: str, base: str):
    """Fallback for common public load-more endpoint declarations."""
    text = _normalize_html_for_urls(html)
    patterns = (
        r'(?i)(?:loadMoreUrl|load_more_url|nextPageUrl|next_page_url|paginationUrl|pagination_url)\s*["\']?\s*[:=]\s*["\']([^"\']+)',
        r'(?i)(?:href|url)\s*[=:]\s*["\']([^"\']*(?:page|load-more|loadmore|pagination)[^"\']*)["\']',
    )
    for pat in patterns:
        for m in re.finditer(pat, text):
            u = urljoin(base, html_lib.unescape(m.group(1)).replace("\\/", "/"))
            if is_xhamster(u): return u
    return None


def _extract_video_cards(html: str, base: str):
    """Extract list of videos (title, url, duration, thumb) from any listing page."""
    items = []
    seen = set()
    initials = _extract_window_initials(html)
    if isinstance(initials, dict):
        for key in ("videosList", "videos", "content", "galleriesList", "listingVideos",
                    "relatedVideos", "recommendedVideos", "searchResult", "verticalGallery",
                    "ajaxifyVideos", "homeVideosList", "modelVideos", "pornstarVideos",
                    "channelVideos", "newVideos", "topVideos"):
            node = initials.get(key)
            if isinstance(node, dict):
                for lst_key in ("items", "videos", "content", "results", "data", "list"):
                    if isinstance(node.get(lst_key), list):
                        for v in node[lst_key]:
                            _add_video_card(v, base, items, seen)
            elif isinstance(node, list):
                for v in node:
                    _add_video_card(v, base, items, seen)

    soup = BeautifulSoup(html, "lxml")

    # Try multiple selectors (xHamster changes classes often)
    selectors = [
        "a.video-thumb__image-container",
        "a.thumb-image-container",
        "a.video-thumb",
        "div.video-thumb a",
        "a[href*='/videos/']",
        ".thumb-list__item a.video-thumb",
        ".gf-video-item a",
        "div.video-item a",
        "div[class*='thumb'] a[href*='/videos/']",
    ]
    seen_selectors = set()
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href")
            if not href:
                continue
            if not RE_VIDEO.search(href):
                continue
            u = urljoin(base, href)
            if u in seen or u in seen_selectors:
                continue
            seen_selectors.add(u)
            title = ""
            # Try to find title from parent/nearby elements
            # 1. Check parent
            parent = a.parent
            for _ in range(4):
                if parent is None:
                    break
                # title attribute
                if a.get("title"):
                    title = a.get("title"); break
                # aria-label
                if a.get("aria-label"):
                    title = a["aria-label"]; break
                # text nodes
                txt = parent.get_text(" ", strip=True)
                if txt and len(txt) > 5 and len(txt) < 200:
                    # avoid long "views/duration" junk
                    junk_words = ("views", "subscribers", "rating", "%", "HD", "4K",
                                  "verified", "subs", "duration")
                    if not any(w in txt.lower() for w in junk_words):
                        title = txt
                # Look for nearby name/title class
                name_el = parent.select_one(
                    ".video-thumb-info__name, .video-thumb-title, .thumb-image-container__title, "
                    ".video-title, a.video-thumb-info__name, [class*='title'], .gf-video-title, .name"
                )
                if name_el:
                    t = name_el.get_text(" ", strip=True)
                    if t and len(t) > 3:
                        title = t; break
                parent = parent.parent

            img = a.find("img")
            thumb = ""
            dur = ""
            if img:
                thumb = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            dur_el = a.select_one(
                ".duration, .thumb-image-container__duration, .video-thumb-duration, "
                "[class*='duration'], .thumb-duration"
            )
            if dur_el:
                dur = dur_el.get_text(strip=True)
            # Last fallback: parse title from URL slug
            if not title:
                slug = urlparse(u).path.rstrip("/").split("/")[-1]
                slug = re.sub(r"-xh[a-zA-Z0-9]{5,}$", "", slug)
                title = slug.replace("-", " ").strip().title()
            title = re.sub(r"\s+", " ", title).strip()
            if not title:
                title = "Video"
            if u not in seen:
                seen.add(u)
                items.append({
                    "title": title,
                    "url": _clean_xhamster_page_url(u),
                    "thumb": thumb,
                    "duration": dur,
                    "duration_sec": _parse_duration_sec(dur),
                    "views": "",
                })

    # Sort videos SHORTEST first (as user requested: long videos baad me, short pehle)
    items = _sort_videos_by_duration(items)

    # Pagination / load-more. Prefer explicit nested state before HTML links.
    next_page = _discover_next_from_initials(initials, base) if isinstance(initials, dict) else None
    if not next_page:
        next_page = _discover_cursor_endpoint(html, base)
    for a in soup.select("a[rel='next'], a.next, li.next a, a[class*='next'], a.pagination-next, a[aria-label='Next']"):
        href = a.get("href")
        if href:
            next_page = urljoin(base, href)
            break
    if not next_page and isinstance(initials, dict):
        try:
            for path in [("pagination", "nextPageURL"), ("paging", "next"), ("nextPageURL", None),
                          ("nextPage", None), ("next", None)]:
                node = initials
                ok = True
                for key in path:
                    if key is None: break
                    if isinstance(node, dict) and key in node:
                        node = node[key]
                    else:
                        ok = False; break
                if ok and isinstance(node, str):
                    next_page = urljoin(base, node); break
                if ok and isinstance(node, dict):
                    nu = node.get("url") or node.get("href")
                    if nu:
                        next_page = urljoin(base, nu); break
        except Exception:
            pass

    # Normalize mirror domain in next_page (xhamster.com → xhamster46.desi)
    if next_page:
        next_page = _preferred_mirror(next_page)

    return items[:60], next_page


def _parse_duration_sec(dur: str) -> int:
    """Parse 'MM:SS' or 'HH:MM:SS' or '12m 34s' to seconds (for sorting)."""
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
        # "1h 12m" / "3m 45s"
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


def _sort_videos_by_duration(items):
    """Sort videos LONGEST first (badi video pehle, short last me) — as user requested."""
    try:
        return sorted(items, key=lambda v: -v.get("duration_sec", _parse_duration_sec(v.get("duration", ""))))
    except Exception:
        return items


def _add_video_card(v, base, items, seen):
    """Add a video card from JSON/initials if not already seen."""
    if not isinstance(v, dict):
        return
    u = v.get("pageURL") or v.get("url") or v.get("link") or v.get("canonical")
    if not u or not isinstance(u, str):
        return
    u = _clean_xhamster_page_url(urljoin(base, u))
    if u in seen or not is_xhamster(u) or not RE_VIDEO.search(u):
        return
    seen.add(u)
    title = v.get("title") or v.get("name") or v.get("videoTitle") or ""
    if not title:
        slug = urlparse(u).path.rstrip("/").split("/")[-1]
        slug = re.sub(r"-xh[a-zA-Z0-9]{5,}$", "", slug)
        title = slug.replace("-", " ").strip().title() or "Video"
    dur = v.get("duration") or v.get("durationLabel") or ""
    items.append({
        "title": title[:120],
        "url": _preferred_mirror(u),
        "thumb": v.get("thumbURL") or v.get("imageURL") or v.get("previewUrl")
                 or v.get("coverUrl") or v.get("thumbnailUrl") or "",
        "duration": dur,
        "duration_sec": _parse_duration_sec(dur),
        "views": v.get("views") or v.get("viewsCount") or "",
    })


# Preferred mirror for xHamster (xhamster.com often blocks, xhamster46.desi works)
_XH_MIRRORS = [
    "xhamster46.desi",
    "xhamster.desi",
    "xhamster19.desi",
    "xhamster.com",
    "xhamster.one",
]


def _preferred_mirror(url: str) -> str:
    """Replace xhamster host with a working mirror."""
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if not is_xhamster(url):
            return url
        # If already a known mirror keep it
        for m in _XH_MIRRORS:
            if host == m or host.endswith("." + m):
                return url
        # Do not silently replace a user-supplied xHamster domain. Different
        # domains expose different profile pagination; _xh_get handles fallback.
        return url
    except Exception:
        return url


def _extract_search(html: str, base: str):
    return _extract_video_cards(html, base)


def _extract_profile(html: str, base: str):
    soup = BeautifulSoup(html, "lxml")
    name = None
    # Find creator name
    tm = soup.find("h1") or soup.find("title")
    if tm:
        name = tm.get_text(strip=True)
    name = re.sub(r"\s+[\|\-–—]+\s*xHamster.*$", "", name or "Creator", flags=re.I).strip()
    videos, next_page = _extract_video_cards(html, base)
    return name, videos, next_page


# ================== COMMANDS ==================

@Client.on_message(filters.private & _cmd("xhs", "xhsearch"))
async def cmd_xh_search(client: Client, m: Message):
    if not await _xh_vip_allowed(m):
        return await m.reply_text(_XH_VIP_DENY_TEXT, disable_web_page_preview=True)
    parts = (m.text or "").split(None, 1)
    if len(parts) < 2:
        return await m.reply_text("Usage: <code>/xhs search query</code>")
    q = parts[1].strip()
    # /xhs also accepts a direct public profile/channel/search URL.
    # Previously a URL was incorrectly encoded as a search query, which
    # caused the same limited search page to be returned.
    if is_xhamster(q) and re.match(r"^https?://", q, re.I):
        clean = q.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if RE_CREATOR.search(clean):
            m_cr = RE_CREATOR.search(clean)
            username = m_cr.group(1).rstrip("/")
            sec_m = re.search(r"/(creators|users|pornstars|channels|models|pornstar-channels)/", clean, re.I)
            section = (sec_m.group(1).lower() if sec_m else "pornstars")
            if section == "pornstar-channels":
                section = "channels"
            url = f"{urlparse(clean).scheme}://{urlparse(clean).netloc}/{section}/{username}/videos"
            return await _send_listing(client, m, url, title="🔞 Creator Profile")
        return await _send_listing(client, m, clean, title="🔞 xHamster Listing")
    url = f"https://xhamster46.desi/search/{quote(q)}"
    await _send_listing(client, m, url, title=f"🔞 xHamster Search: {q}")


@Client.on_message(filters.private & _cmd("xhg", "xhgallery"))
async def cmd_xh_gallery(client: Client, m: Message):
    if not await _xh_vip_allowed(m):
        return await m.reply_text(_XH_VIP_DENY_TEXT, disable_web_page_preview=True)
    parts = (m.text or "").split(None, 1)
    url = parts[1] if len(parts) > 1 else ""
    if m.reply_to_message and m.reply_to_message.text:
        url = m.reply_to_message.text
    url = get_url(url)
    if not url or not is_xhamster(url) or "gallery" not in url.lower():
        return await m.reply_text("Usage: <code>/xhg https://xhamster.com/gallery/...</code>")
    uid = m.from_user.id
    work = user_download_dir(uid) + f"/xh_g_{int(time.time())}"
    os.makedirs(work, exist_ok=True)
    msg = await m.reply_text("🔞 Downloading xHamster gallery...")
    try:
        async with aiohttp.ClientSession() as s:
            code, html, final_url = await _xh_get(s, url)
            if code != 200:
                return await msg.edit_text(f"❌ xHamster HTTP {code}\n\n🌐 Page: <code>{final_url[:180]}</code>")
            title, imgs = _extract_gallery(html, final_url)
            if not imgs:
                return await msg.edit_text("❌ Gallery me photos nahi mile (page may be 18+ login wall or blocked).")
            await msg.edit_text(f"🖼️ **{title[:80]}**\n📥 Downloading {len(imgs)} photos...")
            done = {"n": 0}

            async def prog(done_n, total):
                done["n"] = done_n
                if done_n % 10 == 0 or done_n == total:
                    try:
                        await msg.edit_text(
                            f"🖼️ **{title[:60]}**\n📥 Photos: {done_n}/{total}"
                        )
                    except Exception:
                        pass

            paths = await _download_gallery_images(s, imgs, work, progress=prog)
            if not paths:
                return await msg.edit_text("❌ Photos download nahi ho paaye.")
            await msg.edit_text(f"📤 Uploading {len(paths)} photos as album...")
            # Send as media groups (max 10 per message)
            for i in range(0, len(paths), 10):
                group = [InputMediaPhoto(p) for p in paths[i:i+10]]
                await client.send_media_group(m.chat.id, group, reply_to_message_id=m.id)
                await asyncio.sleep(1)
            await msg.delete()
            await m.reply_text(f"✅ **{title[:60]}**\n📸 {len(paths)} photos sent.")
    except Exception as e:
        logger.exception("xhg")
        await msg.edit_text(f"❌ Error: <code>{e}</code>")
    finally:
        asyncio.create_task(cleanup_dir(work))


@Client.on_message(filters.private & _cmd("xhp", "xhprofile", "xhuser"))
async def cmd_xh_profile(client: Client, m: Message):
    if not await _xh_vip_allowed(m):
        return await m.reply_text(_XH_VIP_DENY_TEXT, disable_web_page_preview=True)
    parts = (m.text or "").split(None, 1)
    url = parts[1] if len(parts) > 1 else ""
    if m.reply_to_message and m.reply_to_message.text:
        url = m.reply_to_message.text
    url = get_url(url)
    if not url or not is_xhamster(url):
        return await m.reply_text("Usage: <code>/xhp https://xhamster.com/creators/NAME</code>\n"
                                   "Or /xhp https://xhamster.com/pornstars/NAME or /channels/NAME")
    # Normalize: extract creator path and add /videos endpoint (auto-fallback in _xh_get handles 404 to videos-porn)
    # Remove query/hash
    url = url.split("?")[0].split("#")[0]
    m_cr = RE_CREATOR.search(url)
    if m_cr:
        username = m_cr.group(1).rstrip("/")
        # Determine section (creators/pornstars/channels/users)
        section_match = re.search(r"/(creators|users|pornstars|channels|models|pornstar-channels)/", url, re.I)
        section = section_match.group(1).lower() if section_match else "creators"
        if section in ("pornstar-channels", "channels", "models"):
            section = "channels" if section != "users" else "users"
        # Remove trailing subpath if user sent deep link (e.g., /creators/name/about -> /creators/name/videos)
        # Keep the domain supplied by the user first. _xh_get will try
        # compatible endpoints and only use mirror fallback after failure.
        parsed = urlparse(url)
        host_base = f"{parsed.scheme or 'https'}://{parsed.netloc}" if parsed.netloc else "https://xhamster.com"
        url = f"{host_base}/{section}/{username}/videos"
    await _send_listing(client, m, url, title="🔞 Creator Profile")


@Client.on_message(filters.private & _cmd("xh", "xhamster"))
async def cmd_xh_auto(client: Client, m: Message):
    parts = (m.text or "").split(None, 1)
    url = parts[1] if len(parts) > 1 else ""
    if m.reply_to_message and m.reply_to_message.text:
        url = m.reply_to_message.text
    url = get_url(url)

    is_vip = await _xh_vip_allowed(m)

    if not url or not is_xhamster(url):
        base = (
            "🔞 **xHamster**\n\n"
            "✅ <b>Sabke liye (Free):</b> Bas kisi xHamster single video ka direct link "
            "yaha bhej do — main auto quality select karke download + upload kar dunga "
            "(<code>/xh</code> command ki zarurat NAHI hai, direct link bhejo).\n"
        )
        if is_vip:
            base += (
                "\n💎 <b>Premium/Admin Extra:</b>\n"
                "<code>/xhs query</code> — search\n"
                "<code>/xhg gallery_url</code> — gallery photos\n"
                "<code>/xhp profile_url</code> — creator/pornstar videos\n"
                "<code>/xh url</code> — auto-detect (gallery/profile/search/video)\n"
            )
        else:
            base += (
                "\n💎 <b>Premium Features (Locked):</b>\n"
                "Search, Profile, Gallery, Paginated listings — sirf premium/admin ke liye. "
                "Owner @bimbobot69 se sampark karo."
            )
        return await m.reply_text(base, disable_web_page_preview=True)

    t = _xh_type(url)
    # Single video: SABKE LIYE available (free users bhi use kar sakte hain).
    # NOTE: youtube_dl_echo.py apne aap `/xh <url>` wale message ko (usme http:// hai)
    # pakad lega aur xhamster_engine se qualities nikal ke quality buttons show kar dega.
    # Yaha bas confirmation dete hain aur wapas aate hain — extra echo NAHI bhejte (warna duplicate aayega).
    if t == "video":
        await m.reply_text(
            f"🔞 <b>xHamster Video Detected</b>\n"
            f"<code>{url[:150]}</code>\n\n"
            f"🔄 Quality buttons load ho rahe hain, thoda wait karo..."
        )
        return

    # Non-video types (gallery/profile/search/tag) => VIP only
    if not is_vip:
        return await m.reply_text(_XH_VIP_DENY_TEXT, disable_web_page_preview=True)

    if t == "gallery":
        m.text = f"/xhg {url}"; m.command = ["xhg", url]
        await cmd_xh_gallery(client, m)
    elif t in ("profile", "creator_videos"):
        m.text = f"/xhp {url}"; m.command = ["xhp", url]
        await cmd_xh_profile(client, m)
    elif t in ("search", "tag"):
        await _send_listing(client, m, url)
    else:
        await m.reply_text(f"🔞 URL type samajh nahi aaya: {url}\n\nSingle video link bhejo ya premium features ke liye /xhs /xhg /xhp use karo.")


# ================== In-memory store for paginated listings (to keep cb_data < 64 bytes) ==================
_LISTING_STORE = {}
_LIST_TTL = 1800  # 30 min





# ================== LISTING UI (inline keyboard with pages) ==================
def _listing_kbd(token, items, has_next, current_page=1, sort_mode="long"):
    rows = []
    # Sorting controls are kept at the top so users can change order without leaving the listing.
    rows.append([
        InlineKeyboardButton("⬇️ Longest first" + (" ✅" if sort_mode == "long" else ""), callback_data=f"xh_sort::{token}::long"),
        InlineKeyboardButton("⬆️ Shortest first" + (" ✅" if sort_mode == "short" else ""), callback_data=f"xh_sort::{token}::short"),
    ])
    page_items = items[:10]
    for i, v in enumerate(page_items, 1):
        title = re.sub(r"\s+", " ", v["title"])[:24]
        dur = f" ⏱{v['duration']}" if v.get("duration") else ""
        # Single button: opens quality picker for this video
        rows.append([InlineKeyboardButton(
            f"▶️ {i}. {title}{dur}", callback_data=f"xh_q::{token}::{i-1}"
        )])
    # Keep page download separate from the persistent full-channel queue.
    rows.append([InlineKeyboardButton(
        f"⬇️ Download Page ({len(page_items)} videos)",
        callback_data=f"xh_pageall::{token}::best"
    )])
    rows.append([InlineKeyboardButton(
        "📥 Download Entire Channel — MAX QUALITY",
        callback_data=f"xh_all::{token}::max"
    )])
    # Navigation
    nav = []
    if current_page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"xh_prevpg::{token}"))
    nav.append(InlineKeyboardButton("❌ Close", callback_data="close"))
    if has_next:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"xh_pg::{token}"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _quality_kbd(token, idx, qlts):
    """Build quality selection keyboard for a single video.
    qlts = [{"height": h, "label": "...", "m3u8": "..."}] (sorted desc)."""
    rows = []
    if not qlts:
        qlts = [{"height": 720, "label": "720p (HD)", "m3u8": None}]
    # 2 qualities per row
    row = []
    for i, q in enumerate(sorted(qlts, key=lambda x: -x["height"])):
        h = q["height"]
        label = q.get("label", f"{h}p")
        cb_dl = f"xh_vid::{token}::{idx}::{h}::video"
        cb_fi = f"xh_vid::{token}::{idx}::{h}::file"
        row.append(InlineKeyboardButton(f"🎬 {label}", callback_data=cb_dl))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    # MP3 row
    rows.append([InlineKeyboardButton("🎵 MP3 (audio)", callback_data=f"xh_vid::{token}::{idx}::0::audio")])
    # Back to listing
    rows.append([InlineKeyboardButton("⬅️ Back to list", callback_data=f"xh_back::{token}")])
    return InlineKeyboardMarkup(rows)


async def _send_listing(client: Client, m: Message, url: str, title: str = "🔞 xHamster"):
    msg = await m.reply_text(f"{title}\n🔍 Loading...")
    try:
        async with aiohttp.ClientSession() as s:
            code, html, final_url = await _xh_get(s, url)
            if code != 200:
                return await msg.edit_text(f"❌ HTTP {code}")
            items, next_page = _extract_search(html, final_url)
            pname = None
            if RE_CREATOR.search(final_url):
                pname, items, next_page = _extract_profile(html, final_url)
            if pname:
                title = f"🔞 {pname}"
            # Always sort shortest-first
            items = _sort_videos_by_duration(items)
            if not items:
                return await msg.edit_text(
                    f"❌ Koi video nahi mila.\n\n"
                    f"HTTP: <code>{code}</code>\n"
                    f"Page: <code>{final_url[:180]}</code>\n"
                    f"Detected: <code>0</code> | Next page: <code>{'yes' if next_page else 'no'}</code>"
                )
            token = _store_listing(items, next_page, title=title, current_url=final_url)
            text = (f"{title}\n\n"
                    f"📋 {len(items)} videos detected\n"
                    f"🌐 HTTP: {code} | Next page: {'✅' if next_page else '❌'}\n"
                    f"⏱ Default: longest first\n\n")
            for i, v in enumerate(items[:10], 1):
                t = re.sub(r"\s+", " ", v["title"])[:35]
                dur = f" ⏱{v['duration']}" if v.get("duration") else ""
                text += f"\n{i}. {t}{dur}"
            await msg.edit_text(text, reply_markup=_listing_kbd(token, items, bool(next_page), current_page=1, sort_mode="long"))
    except Exception as e:
        logger.exception("xh listing")
        await msg.edit_text(f"❌ Error: <code>{e}</code>")


def _store_listing(items, next_page, title="🔞 xHamster", current_url="", prev_url=""):
    import secrets
    for _ in range(5):
        token = secrets.token_hex(5)
        if token not in _LISTING_STORE:
            break
    _LISTING_STORE[token] = {
        "items": items, "next": next_page, "prev": prev_url,
        "ts": time.time(), "title": title, "page": 1,
        "current_url": current_url, "sort": "long",
    }
    now = time.time()
    for k in [k for k, v in _LISTING_STORE.items() if now - v["ts"] > _LIST_TTL]:
        _LISTING_STORE.pop(k, None)
    return token


# Safe answer helper (catches QUERY_ID_INVALID / timeout errors silently)
async def _safe_answer(c: CallbackQuery, text: str = "", show_alert: bool = False, cache_time: int = 3):
    try:
        await c.answer(text=text, show_alert=show_alert, cache_time=cache_time)
    except Exception:
        pass


# Concurrency limiter for Download All (max 2 simultaneous downloads on Koyeb free 512MB RAM)
_XH_DOWNLOAD_SEM = asyncio.Semaphore(2)


async def _xh_fetch_qualities_for_item(item, session: aiohttp.ClientSession):
    """Fetch best quality info for a single video item (used in Download All)."""
    url = _preferred_mirror(item.get("url", ""))
    try:
        code, html, final_url = await _xh_get(session, url, try_endpoint_fallback=True)
        if code == 200:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None, functools.partial(xh_extract, final_url,
                                        "cookies.txt" if os.path.exists("cookies.txt") else None)
            )
            if res and res.get("qualities"):
                qlts = res["qualities"]
                item["_qualities"] = qlts
                item["_final_url"] = final_url
                # Pick best
                best_q = max(qlts, key=lambda q: q.get("height", 0))
                return best_q.get("height", 720), best_q.get("m3u8", ""), final_url
    except Exception as e:
        logger.warning(f"xh prefetch qualities fail for {url[:60]}: {e}")
    # Default fallback
    return 1080, "", url



async def _xh_collect_all_pages(start_url, max_videos=None):
    """Collect listing pages gradually; never creates one task per video."""
    found, seen, current = [], set(), start_url
    async with aiohttp.ClientSession() as session:
        # No video-count cap. The visited URL set and page safety cap prevent
        # an accidental pagination loop from running forever.
        visited_pages = set()
        for _ in range(10000):
            if not current or current in visited_pages:
                break
            visited_pages.add(current)
            if max_videos is not None and len(found) >= max_videos:
                break
            code, html, final_url = await _xh_get(session, current)
            if code != 200 or not html:
                break
            items, nxt = _extract_search(html, final_url)
            if RE_CREATOR.search(final_url):
                _, items, nxt = _extract_profile(html, final_url)
            for item in items:
                u = item.get("url")
                if u and u not in seen:
                    seen.add(u); found.append(item)
                    if max_videos is not None and len(found) >= max_videos: break
            # Some channel pages use numbered URLs (/2, /3, /4...) and
            # expose no rel=next/cursor. Continue those pages safely.
            if not nxt:
                parsed = urlparse(current)
                parts = [x for x in parsed.path.rstrip("/").split("/") if x]
                if len(parts) >= 2 and parts[0].lower() in ("channels", "creators", "pornstars", "users", "models"):
                    # Profile commands may start at /name/videos, while
                    # numbered channel pages are /name/2, /name/3...
                    if parts[-1].lower() in ("videos", "videos-porn", "new-videos", "popular"):
                        parts.pop()
                    if parts[-1].isdigit():
                        parts[-1] = str(int(parts[-1]) + 1)
                    else:
                        parts.append("2")
                    nxt = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/" + "/".join(parts))
            if not nxt or nxt == current:
                break
            current = nxt
            await asyncio.sleep(1.5)
    return found


async def _xh_full_queue_worker(client, job_id, user, status_msg):
    """Single-worker queue: download, upload/log, cleanup, then next item."""
    completed = failed = 0
    try:
        while True:
            job = await get_job(job_id)
            if not job or job.get("status") in ("cancelled", "paused"):
                return
            item = await claim_next_item(job_id)
            if not item:
                await update_job(job_id, status="completed")
                try: await status_msg.edit_text(f"✅ Full channel queue complete\n\nDone: {completed} | Failed: {failed}")
                except Exception: pass
                return
            idx = item.get("index", 0) + 1
            title = item.get("title", "Video")
            current_job = await get_job(job_id)
            total = len((current_job or {}).get("items", []))
            try:
                await status_msg.edit_text(f"📥 Full channel queue\n\n🔽 {idx}/{total} processing\n🎬 {title[:70]}\n✅ Done: {completed} | ❌ Failed: {failed}\n📋 Total videos: {total}")
            except Exception: pass
            # Reuse the stable existing downloader; quality extraction is per item.
            async with aiohttp.ClientSession() as session:
                h, m3u8, final_url = await _xh_fetch_qualities_for_item(item, session)
            prog = await client.send_message(status_msg.chat.id, f"🔽 #{idx} downloading: {title[:60]}")
            try:
                # Never allow one stuck yt-dlp/ffmpeg/upload job to block the
                # entire channel queue. Timeout is configurable through the
                # existing process-timeout environment variable.
                await asyncio.wait_for(
                    _xh_download_and_upload(
                        client, prog, user, final_url, m3u8, title, h, "video",
                        {"User-Agent": UA, "Referer": final_url, "Origin": _base_of(final_url)}
                    ),
                    timeout=max(300, int(Config.BIMBO_PROCESS_MAX_TIMEOUT))
                )
                await finish_item(job_id, item["index"], "completed")
                completed += 1
            except asyncio.TimeoutError:
                msg = f"timeout after {Config.BIMBO_PROCESS_MAX_TIMEOUT}s"
                await finish_item(job_id, item["index"], "failed", msg)
                failed += 1
                logger.error("xh full queue item timeout: %s", title[:100])
            except Exception as exc:
                await finish_item(job_id, item["index"], "failed", str(exc))
                failed += 1
                logger.exception("xh full queue item failed")
            # Give ffmpeg/yt-dlp and the allocator time to release memory
            # before the next maximum-quality job starts.
            gc.collect()
            await asyncio.sleep(10)
    except Exception as exc:
        await update_job(job_id, status="paused", last_error=str(exc)[:500])
        logger.exception("xh full queue worker stopped")
        try: await status_msg.edit_text(f"⚠️ Queue paused safely\nDone: {completed} | Failed: {failed}\nError: {str(exc)[:300]}")
        except Exception: pass


@Client.on_callback_query(filters.regex(r"^xh_(q|pg|vid|back|dl|best|album|all|pageall|prevpg|sort)::"))
async def xh_callbacks(client: Client, c: CallbackQuery):
    data = c.data or ""
    parts = data.split("::")
    action = parts[0]
    logger.info(f"xHamster callback: {data[:80]} (action={action})")
    # 1) Answer the callback IMMEDIATELY (avoids QUERY_ID_INVALID / timeout issues on Telegram)
    await _safe_answer(c)
    # 2) VIP gate
    try:
        uid = c.from_user.id
        if not is_admin(uid):
            try:
                if not await is_premium(uid):
                    return await _safe_answer(c, "🔒 Premium only. Owner @bimbobot69 se sampark karo.", show_alert=True)
            except Exception:
                return await _safe_answer(c, "🔒 Premium only.", show_alert=True)
    except Exception as e:
        logger.warning(f"xh VIP check fail: {e}")
    try:
        # ---------- FULL CHANNEL QUEUE (persistent, one video at a time) ----------
        if action == "xh_all":
            token = parts[1]
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            status_msg = await c.message.reply_text("🔎 Collecting channel pages safely...\nNo video-count limit")
            try:
                all_items = await _xh_collect_all_pages(entry.get("current_url") or entry.get("next"), None)
                if not all_items:
                    return await status_msg.edit_text("❌ No videos found for queue.")
                job_id = await create_job(c.from_user.id, c.message.chat.id, entry.get("title", "xHamster Channel"), entry.get("current_url", ""), all_items)
                await status_msg.edit_text(f"✅ Queue created\n\n📋 Videos: {len(all_items)}\n⚡ Mode: 1-by-1\n📦 Max quality\n🔁 Restart-safe queue")
                asyncio.create_task(_xh_full_queue_worker(client, job_id, c.from_user, status_msg))
            except Exception as exc:
                logger.exception("xh full queue create failed")
                await status_msg.edit_text(f"❌ Queue create failed: <code>{str(exc)[:500]}</code>")
            return

        # ---------- DOWNLOAD ALL on current page (BEST available quality per video) ----------
        if action == "xh_pageall":
            token = parts[1]
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            items = entry.get("items", [])[:8]
            if not items:
                return await _safe_answer(c, "No videos", show_alert=True)

            # Step 1: Pre-fetch qualities for all videos first (to know true best quality)
            status_msg = await c.message.reply_text(
                f"⬇️ **Preparing Download All**\n\n"
                f"🔍 Fetching best quality for {len(items)} videos... thoda wait karo"
            )
            download_jobs = []
            try:
                async with aiohttp.ClientSession() as s:
                    for i, item in enumerate(items, 1):
                        short_title = (item.get("title", "") or "video")[:40]
                        try:
                            await status_msg.edit_text(
                                f"⬇️ **Preparing Download All**\n\n"
                                f"🔍 Checking video {i}/{len(items)}: {short_title}..."
                            )
                        except Exception:
                            pass
                        bh, bm3u8, furl = await _xh_fetch_qualities_for_item(item, s)
                        download_jobs.append({
                            "idx": i,
                            "item": item,
                            "best_h": bh,
                            "best_m3u8": bm3u8,
                            "final_url": furl,
                        })
                        await asyncio.sleep(0.5)  # gentle rate limit to xHamster
            except Exception as e:
                logger.exception(f"xh all prefetch err: {e}")
                # Fallback: default 1080p for all
                for i, item in enumerate(items, 1):
                    download_jobs.append({
                        "idx": i, "item": item, "best_h": 1080, "best_m3u8": "",
                        "final_url": _preferred_mirror(item.get("url", "")),
                    })

            # Step 2: Launch downloads (semaphore limits concurrency to max 2 for Koyeb 512MB safety)
            async def _run_one(job):
                async with _XH_DOWNLOAD_SEM:
                    item = job["item"]
                    title = item.get("title", "Video")
                    i = job["idx"]
                    url = job["final_url"]
                    best_h = job["best_h"]
                    best_m3u8 = job["best_m3u8"]
                    try:
                        prog_msg = await client.send_message(
                            c.message.chat.id,
                            f"🔽 #{i}/{len(items)} Starting {best_h}p download: {title[:50]}...",
                            reply_to_message_id=c.message.id,
                        )
                    except Exception:
                        try:
                            prog_msg = await c.message.reply_text(
                                f"🔽 #{i}/{len(items)} Starting {best_h}p download: {title[:50]}..."
                            )
                        except Exception as e:
                            logger.warning(f"xh all send prog fail: {e}")
                            return
                    await _xh_download_and_upload(
                        client, prog_msg, c.from_user, url, best_m3u8, title, best_h, "video",
                        {"User-Agent": UA, "Referer": url, "Origin": _base_of(url)}
                    )

            for job in download_jobs:
                asyncio.create_task(_run_one(job))
                await asyncio.sleep(1)  # small stagger between starts

            try:
                await status_msg.edit_text(
                    f"✅ **Download All Started!**\n\n"
                    f"📥 {len(items)} videos queue me hain (best quality per video).\n"
                    f"⚡ Max 2 concurrent downloads (Koyeb RAM-safe).\n"
                    f"Har video alag progress bar ke saath ayegi..."
                )
            except Exception:
                pass
            asyncio.create_task(_auto_delete(status_msg, delay=20))
            return

        # ---------- SORT CURRENT PAGE ----------
        if action == "xh_sort":
            token = parts[1]
            mode = parts[2] if len(parts) > 2 else "long"
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            if mode not in ("long", "short"):
                return await _safe_answer(c, "Invalid sort", show_alert=True)
            entry["sort"] = mode
            items = list(entry.get("items", []))
            items.sort(key=lambda v: v.get("duration_sec", 999999), reverse=(mode == "long"))
            entry["items"] = items
            page = entry.get("page", 1)
            title = entry.get("title", "🔞 xHamster")
            text = f"{title}\n\n📋 Page {page} ({'longest' if mode == 'long' else 'shortest'} first):\n"
            for i, v in enumerate(items[:10], 1):
                t = re.sub(r"\s+", " ", v.get("title", "Video"))[:35]
                dur = f" ⏱{v.get('duration')}" if v.get("duration") else ""
                text += f"\n{i}. {t}{dur}"
            await c.message.edit_text(text, reply_markup=_listing_kbd(token, items, bool(entry.get("next")) or bool(entry.get("prev")) or page > 1, page, mode))
            return await _safe_answer(c, "Sorted")

        # ---------- PREV PAGE ----------
        if action == "xh_prevpg":
            token = parts[1]
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            prev_url = entry.get("prev")
            if not prev_url:
                return await _safe_answer(c, "No previous page", show_alert=True)
            m = c.message
            try:
                async with aiohttp.ClientSession() as s:
                    code, html, final_url = await _xh_get(s, prev_url)
                    if code != 200:
                        return await _safe_answer(c, f"HTTP {code}", show_alert=True)
                    items, np = _extract_search(html, final_url)
                    if RE_CREATOR.search(final_url):
                        _, items, np = _extract_profile(html, final_url)
                    items = sorted(items, key=lambda v: v.get("duration_sec", 999999), reverse=(entry.get("sort", "long") == "long"))
                if not items:
                    return await _safe_answer(c, "No results", show_alert=True)
                page = max(1, entry.get("page", 2) - 1)
                # Save current url as "next" so forward works too
                old_next = entry.get("current_url", "")
                entry["items"] = items
                entry["next"] = np or old_next
                entry["prev"] = entry.get("prev_prev", "")
                entry["current_url"] = prev_url
                entry["ts"] = time.time()
                title = entry.get("title", "🔞 xHamster")
                text = f"{title}\n\n📋 Page {page}:\n"
                for i, v in enumerate(items[:10], 1):
                    t = re.sub(r"\s+", " ", v["title"])[:35]
                    dur = f" ⏱{v['duration']}" if v.get("duration") else ""
                    text += f"\n{i}. {t}{dur}"
                entry["page"] = page
                await m.edit_text(text, reply_markup=_listing_kbd(token, items, bool(np or old_next), page, entry.get("sort", "long")))
                await _safe_answer(c)
            except Exception as e:
                logger.exception("xh prevpg"); await _safe_answer(c, f"Err: {e}", show_alert=True)
            return

        if action == "xh_pg":
            token = parts[1]
            entry = _LISTING_STORE.get(token)
            if not entry or not entry.get("next"):
                return await _safe_answer(c, "No more pages", show_alert=True)
            next_url = _preferred_mirror(entry["next"])
            m = c.message
            try:
                async with aiohttp.ClientSession() as s:
                    code, html, final_url = await _xh_get(s, next_url)
                    if code != 200:
                        return await _safe_answer(c, f"HTTP {code}", show_alert=True)
                    items, np = _extract_search(html, final_url)
                    if RE_CREATOR.search(final_url):
                        _, items, np = _extract_profile(html, final_url)
                    items = sorted(items, key=lambda v: v.get("duration_sec", 999999), reverse=(entry.get("sort", "long") == "long"))
                if not items:
                    return await _safe_answer(c, "No results", show_alert=True)
                old_page = entry.get("page", 1)
                new_page = old_page + 1
                # Save prev so back button works
                entry["prev"] = entry.get("current_url", "")
                entry["current_url"] = next_url
                entry["items"] = items
                entry["next"] = np
                entry["ts"] = time.time()
                title = entry.get("title", "🔞 xHamster")
                text = f"{title}\n\n📋 Page {new_page}:\n"
                for i, v in enumerate(items[:10], 1):
                    t = re.sub(r"\s+", " ", v["title"])[:35]
                    dur = f" ⏱{v['duration']}" if v.get("duration") else ""
                    text += f"\n{i}. {t}{dur}"
                entry["page"] = new_page
                await m.edit_text(text, reply_markup=_listing_kbd(token, items, bool(np), current_page=new_page, sort_mode=entry.get("sort", "long")))
                await _safe_answer(c)
            except Exception as e:
                logger.exception("xh pg")
                await _safe_answer(c, f"Err: {e}", show_alert=True)

        elif action == "xh_q":
            # Quality picker screen (no actual download yet, shows quality buttons)
            token = parts[1]
            idx = int(parts[2])
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            items = entry.get("items", [])
            if idx < 0 or idx >= len(items):
                return await _safe_answer(c, "Invalid", show_alert=True)
            item = items[idx]
            url = _preferred_mirror(item["url"])
            await _safe_answer(c, "Loading qualities...")
            # Fetch video page to extract qualities (with fallback to default)
            qlts = []
            try:
                async with aiohttp.ClientSession() as s:
                    code, html, final_url = await _xh_get(s, url)
                    if code == 200:
                        # Try engine extract via synchronous call in thread (network + parsing heavy)
                        import functools
                        loop = asyncio.get_event_loop()
                        res = await loop.run_in_executor(
                            None, functools.partial(xh_extract, final_url, "cookies.txt" if os.path.exists("cookies.txt") else None)
                        )
                        if res and res.get("qualities"):
                            qlts = res["qualities"]
                            item["_qualities"] = qlts
                            item["_final_url"] = final_url
            except Exception as e:
                logger.warning(f"xh_q fetch qualities fail: {e}")
            if not qlts:
                # fallback qualities
                qlts = [
                    {"height": 2160, "label": "2160p (4K)"},
                    {"height": 1080, "label": "1080p (FHD)"},
                    {"height": 720,  "label": "720p (HD)"},
                    {"height": 480,  "label": "480p (SD)"},
                    {"height": 360,  "label": "360p"},
                    {"height": 240,  "label": "240p"},
                ]
                item["_qualities"] = qlts
                item["_final_url"] = url
            title = item.get("title", "Video")
            dur = f" ⏱{item.get('duration')}" if item.get("duration") else ""
            txt = f"🎬 **{title[:80]}**{dur}\n\nQuality select karo:"
            await c.message.edit_text(txt, reply_markup=_quality_kbd(token, idx, qlts))

        elif action == "xh_back":
            token = parts[1]
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            items = entry.get("items", [])
            items = _sort_videos_by_duration(items)
            page = entry.get("page", 1)
            title = entry.get("title", "🔞 xHamster")
            text = f"{title}\n\n📋 Videos{' (Page '+str(page)+')' if page > 1 else ''}:\n"
            for i, v in enumerate(items[:10], 1):
                t = re.sub(r"\s+", " ", v["title"])[:35]
                dur = f" ⏱{v['duration']}" if v.get("duration") else ""
                text += f"\n{i}. {t}{dur}"
            has_next = bool(entry.get("next"))
            has_prev = bool(entry.get("prev")) or page > 1
            await c.message.edit_text(text, reply_markup=_listing_kbd(token, items, has_next or has_prev, current_page=page, sort_mode=entry.get("sort", "long")))
            await _safe_answer(c)

        elif action == "xh_vid":
            # 👉 DIRECT DOWNLOAD (quality already selected)
            token = parts[1]; idx = int(parts[2]); height = int(parts[3]); mode = parts[4]
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Session expired", show_alert=True)
            items = entry.get("items", [])
            if idx < 0 or idx >= len(items):
                return await _safe_answer(c, "Invalid", show_alert=True)
            item = items[idx]
            qlts = item.get("_qualities") or [
                {"height": 1080, "label": "1080p (FHD)", "m3u8": ""},
                {"height": 720,  "label": "720p (HD)",  "m3u8": ""},
                {"height": 480,  "label": "480p (SD)",  "m3u8": ""},
                {"height": 360,  "label": "360p",       "m3u8": ""},
            ]
            final_url = item.get("_final_url") or _preferred_mirror(item["url"])
            title = item.get("title", "Video")
            h_label = next((q.get("label", f"{height}p") for q in qlts if q.get("height") == height), f"{height}p")

            # Make sure we have m3u8 for the chosen height
            chosen_m3u8 = next((q.get("m3u8", "") for q in qlts if q.get("height") == height), "")
            # If m3u8 missing, re-fetch in background now
            if not chosen_m3u8:
                await _safe_answer(c, "Fetching stream...")
                try:
                    async with aiohttp.ClientSession() as s:
                        code, html, final_url2 = await _xh_get(s, final_url)
                        if code == 200:
                            res = await asyncio.get_event_loop().run_in_executor(
                                None, functools.partial(xh_extract, final_url2,
                                                        "cookies.txt" if os.path.exists("cookies.txt") else None)
                            )
                            if res and res.get("qualities"):
                                qlts = res["qualities"]; item["_qualities"] = qlts
                                final_url = final_url2; item["_final_url"] = final_url
                                chosen_m3u8 = next((q.get("m3u8", "") for q in qlts if q.get("height") == height), "")
                except Exception as e:
                    logger.warning(f"xh_vid re-fetch fail: {e}")

            await _safe_answer(c, "Download starting!")
            await c.message.edit_text(
                f"🎬 **{title[:80]}**\n\n"
                f"📥 Quality: **{h_label}**\n"
                f"📦 Type: **{mode.upper()}**\n\n"
                f"📥 Downloading via yt-dlp + aria2c..."
            )
            # Run download + upload in background task (so UI stays responsive)
            asyncio.create_task(_xh_download_and_upload(
                client, c.message, c.from_user, final_url, chosen_m3u8, title, height, mode,
                {k: v for k, v in (item.get("_xh_headers") or {}).items()} or
                {"User-Agent": UA, "Referer": final_url, "Origin": _base_of(final_url)}
            ))

        elif action == "xh_dl":
            token = parts[1]
            idx = int(parts[2]) if len(parts) > 2 else 0
            entry = _LISTING_STORE.get(token)
            if not entry:
                return await _safe_answer(c, "Expired", show_alert=True)
            items = entry.get("items", [])
            if 0 <= idx < len(items):
                url = _preferred_mirror(items[idx]["url"])
                await _safe_answer(c)
                await client.send_message(c.message.chat.id, url)
    except Exception as e:
        logger.exception(f"xh cb err: {e}")
        try:
            await _safe_answer(c, "Error", show_alert=True)
        except Exception:
            pass


# ================== DIRECT XHAMSTER DOWNLOAD + UPLOAD ==================
async def _xh_download_and_upload(client, status_msg, user, webpage_url, m3u8_url, title, height, mode, headers):
    """Download xHamster HLS with yt-dlp, then upload to Telegram with progress."""
    from helper_funcs.display_progress import progress_for_pyrogram, humanbytes as _hb, TimeFormatter as _TF
    from plugins.custom_thumbnail import Gthumb01, Gthumb02, Mdata01, Mdata03, get_flocation
    from plugins.youtube_dl_button import send_log_media
    from pyrogram import enums
    uid = user.id
    out_dir = user_download_dir(uid)
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time())
    safe_title = re.sub(r'[\\/:*?"<>|]+', ' ', title)[:100].strip() or "xhamster_video"
    if mode == "audio":
        out_path = os.path.join(out_dir, f"{safe_title}_{ts}.mp3")
    else:
        out_path = os.path.join(out_dir, f"{safe_title}_{height}p_{ts}.mp4")

    # Build yt-dlp command
    cmd = [
        "yt-dlp", "--no-warnings", "-c", "--newline",
        "--no-check-certificates", "--geo-bypass",
        "--buffer-size", "16M", "--http-chunk-size", "10M",
        "--retries", "10", "--fragment-retries", "10",
        "--retry-sleep", "3",
        "--concurrent-fragments", str(Config.YTDLP_CONCURRENT_FRAGMENTS),
        "--add-header", f"User-Agent:{UA}",
    ]
    if headers.get("Referer"):
        cmd += ["--add-header", f"Referer:{headers['Referer']}"]
    if headers.get("Origin"):
        cmd += ["--add-header", f"Origin:{headers['Origin']}"]
    if os.path.exists("cookies.txt"):
        cmd += ["--cookies", "cookies.txt"]
    if mode == "audio":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "192K", "--hls-prefer-ffmpeg", "-o", out_path]
    else:
        cmd += ["--hls-prefer-ffmpeg", "--merge-output-format", "mp4", "-o", out_path]
    target_url = m3u8_url or webpage_url
    cmd.append(target_url)

    start_ts = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    last_edit = 0
    speed = "0 KiB/s"; eta_s = "--:--"; pct = 0.0; total_bytes = 0; downloaded_bytes = 0
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="ignore").strip()
            m = re.search(r"\[download\]\s+([\d.]+)%", decoded)
            if m:
                pct = float(m.group(1))
                ms = re.search(r"([\d.]+[KMG]i?B/s)", decoded)
                if ms: speed = ms.group(1)
                me = re.search(r"ETA\s+([\d:]+)", decoded)
                if me: eta_s = me.group(1)
                mz = re.search(r"of\s+~?\s*([\d.]+\s*[KMG]?i?B)", decoded)
                if mz:
                    _t = mz.group(1)
                    try:
                        # parse to bytes roughly
                        unit_map = {"B":1,"K":1024,"KI":1024,"KB":1024,"M":1024**2,"MI":1024**2,"MB":1024**2,
                                    "G":1024**3,"GI":1024**3,"GB":1024**3,"T":1024**4,"TI":1024**4,"TB":1024**4}
                        val = float(re.search(r"([\d.]+)", _t).group(1))
                        u = re.sub(r"[\d.\s]", "", _t).upper()
                        total_bytes = int(val * unit_map.get(u, 1))
                        downloaded_bytes = int((pct/100)*total_bytes)
                    except Exception:
                        total_bytes = 0
                now = time.time()
                if now - last_edit > 3:
                    last_edit = now
                    bar = "█"*int(pct//5) + "░"*(20 - int(pct//5))
                    elapsed = int(now - start_ts)
                    dl_text = _hb(downloaded_bytes) if downloaded_bytes else f"{pct:.1f}%"
                    tt_text = _hb(total_bytes) if total_bytes else "??"
                    try:
                        await status_msg.edit_text(
                            f"📥 **Downloading...**\n\n"
                            f"🎬 {safe_title[:60]}\n"
                            f"┃ `{bar}` {pct:.1f}%\n"
                            f"┠ ⚡ Speed: `{speed}`\n"
                            f"┠ 📦 {dl_text} / {tt_text}\n"
                            f"┠ ⏳ ETA: `{eta_s}` | ⏱ {elapsed}s\n"
                            f"┠ 🎬 {height}p | Engine: yt-dlp HLS\n"
                            f"┖ Mode: {mode.upper()}"
                        )
                    except Exception:
                        pass
        await proc.wait()
    except Exception as e:
        logger.exception(f"xh dl err: {e}")
        try: proc.kill()
        except Exception: pass

    # Find final file
    final_file = None
    if os.path.exists(out_path):
        final_file = out_path
    else:
        for f in os.listdir(out_dir):
            p = os.path.join(out_dir, f)
            if os.path.isfile(p) and f.startswith(safe_title[:40]):
                final_file = p; break
    if proc.returncode != 0 or not final_file:
        return await status_msg.edit_text(
            f"❌ **Download failed** (code {proc.returncode})\n\n"
            f"URL: {webpage_url}\n\n"
            f"Try again ya direct link alag quality se download karo."
        )

    fsize = os.path.getsize(final_file)
    elapsed_dl = int(time.time() - start_ts)

    # Telegram limit: 2000 MiB (split at 1900MB for safety)
    MAX_TG_SIZE = 1900 * 1024 * 1024
    needs_split = fsize > MAX_TG_SIZE
    parts_to_upload = [final_file]

    if needs_split:
        await status_msg.edit_text(
            f"✂️ **Large File Detected**\n\n"
            f"🎬 {safe_title[:50]}\n"
            f"📦 Size: {humanbytes(fsize)} → splitting into parts for Telegram..."
        )
        parts_to_upload = await _split_large_video(final_file, MAX_TG_SIZE, status_msg=status_msg)
        await status_msg.edit_text(
            f"✅ **Split Complete**\n\n"
            f"📦 {len(parts_to_upload)} parts ready\n"
            f"📤 Uploading parts one by one..."
        )
    else:
        await status_msg.edit_text(f"📤 **Uploading to Telegram...**\n\n📦 {humanbytes(fsize)} | ⏱ Downloaded in {elapsed_dl}s")

    try:
        caption = f"🎬 **{safe_title}**\n📥 {height}p | ⚡ BIMBO"

        # Build a minimal fake update object for thumbnail helpers (Gthumb signature expects update.from_user.id etc.)
        class FakeFromUser:
            id = uid; first_name = getattr(user, "first_name", "User"); username = getattr(user, "username", None)
        class FakeMsgForThumb:
            from_user = FakeFromUser()
            id = status_msg.id
            chat = status_msg.chat
            message_id = status_msg.id
        fake_update = FakeMsgForThumb()

        upload_start = time.time()
        sent_msgs = []

        # Prepare thumbnail once from original file (same thumb for all parts)
        thumb = None
        w = h = duration = 0
        if mode == "audio":
            duration = await Mdata03(final_file)
            thumb = await Gthumb01(client, fake_update, task_id=f"xhaudio{ts}")
        elif mode == "file":
            thumb = await Gthumb01(client, fake_update, task_id=f"xhfile{ts}")
        else:
            w, h, duration = await get_video_whd(final_file)
            if duration < 1:
                try:
                    dur2 = await Mdata01(final_file)
                    if isinstance(dur2, tuple) and len(dur2) >= 3:
                        w, h, duration = dur2[:3]
                except Exception:
                    pass
            duration = max(duration or 1, 1)
            w = w or 0; h = h or 0
            thumb = await Gthumb02(client, fake_update, duration, final_file, task_id=f"xhvid{ts}")

        total_parts = len(parts_to_upload)
        for part_idx, part_path in enumerate(parts_to_upload, 1):
            part_size = os.path.getsize(part_path)
            part_caption = caption
            if total_parts > 1:
                part_caption = f"🎬 **{safe_title}**\n📥 {height}p | Part {part_idx}/{total_parts} | ⚡ BIMBO"

            # Update status for this part
            try:
                await status_msg.edit_text(
                    f"📤 **Uploading...**\n\n"
                    f"🎬 {safe_title[:45]}\n"
                    f"📦 Part {part_idx}/{total_parts} | {humanbytes(part_size)}\n"
                    f"⏱ Elapsed: {int(time.time() - upload_start)}s"
                )
            except Exception:
                pass

            if mode == "audio":
                sent = await client.send_audio(
                    status_msg.chat.id, part_path, caption=part_caption,
                    duration=duration, thumb=thumb,
                    reply_to_message_id=status_msg.id,
                    progress=progress_for_pyrogram,
                    progress_args=(f"Uploading audio {f'(part {part_idx}/{total_parts})' if total_parts>1 else ''}",
                                   status_msg, upload_start, os.path.basename(part_path), False),
                )
            elif mode == "file":
                sent = await client.send_document(
                    status_msg.chat.id, part_path, caption=part_caption, thumb=thumb,
                    reply_to_message_id=status_msg.id,
                    progress=progress_for_pyrogram,
                    progress_args=(f"Uploading file {f'(part {part_idx}/{total_parts})' if total_parts>1 else ''}",
                                   status_msg, upload_start, os.path.basename(part_path), False),
                )
            else:  # video
                # For split parts, re-probe duration/width (split parts may have smaller duration)
                pw, ph, pdur = w, h, duration
                if total_parts > 1:
                    try:
                        pw, ph, pdur = await get_video_whd(part_path)
                        pdur = max(pdur or 1, 1)
                    except Exception:
                        pdur = max(duration // total_parts, 1) if duration else 1
                sent = await client.send_video(
                    status_msg.chat.id, part_path, caption=part_caption,
                    duration=pdur, width=pw or w, height=ph or h,
                    thumb=thumb, supports_streaming=True,
                    reply_to_message_id=status_msg.id,
                    progress=progress_for_pyrogram,
                    progress_args=(f"Uploading video {f'(part {part_idx}/{total_parts})' if total_parts>1 else ''}",
                                   status_msg, upload_start, os.path.basename(part_path), False),
                )
            sent_msgs.append(sent)
            # Small delay between parts to avoid flood
            if part_idx < total_parts:
                await asyncio.sleep(1)

        # Final success message (replace progress msg)
        elapsed_total = time_formatter_delta(time.time() - start_ts)
        try:
            if total_parts > 1:
                await status_msg.edit_text(
                    f"✅ **Download Complete!**\n\n"
                    f"🎬 {safe_title[:60]}\n"
                    f"📥 Quality: {height}p | {mode.upper()}\n"
                    f"📦 Size: {humanbytes(fsize)} | 🔢 {total_parts} parts\n"
                    f"⏱ Total time: {elapsed_total}"
                )
            else:
                await status_msg.edit_text(
                    f"✅ **Download Complete!**\n\n"
                    f"🎬 {safe_title[:60]}\n"
                    f"📥 Quality: {height}p | {mode.upper()}\n"
                    f"📦 Size: {humanbytes(fsize)}\n"
                    f"⏱ Total time: {elapsed_total}"
                )
        except Exception:
            pass
        # Auto-delete the status msg after 15s (clean chat)
        asyncio.create_task(_auto_delete(status_msg, delay=15))

        # ====================== LOG CHANNEL SEND ======================
        try:
            if Config.BIMBO_LOG_CHANNEL and Config.BIMBO_LOG_CHANNEL != 0:
                # Send first part / single file to log channel (background, non-blocking)
                log_file = parts_to_upload[0] if parts_to_upload else final_file
                log_thumb = thumb if thumb and os.path.exists(str(thumb)) else None
                log_duration = duration
                log_w, log_h = w, h
                if total_parts > 1 and log_file:
                    # For split parts, probe first part dimensions
                    try:
                        log_w, log_h, log_duration = await get_video_whd(log_file)
                    except Exception:
                        pass
                # Upload media before cleanup; a background task could start
                # after finally() deletes the local file, leaving only the link.
                for log_idx, log_part in enumerate(parts_to_upload or [final_file], 1):
                    if not log_part or not os.path.exists(log_part):
                        continue
                    await send_log_media(
                        bot=client,
                        user=user,
                        file_path=log_part,
                        link=webpage_url,
                        file_name=f"{safe_title} (part {log_idx})" if total_parts > 1 else safe_title,
                        media_type=mode,
                        file_size=os.path.getsize(log_part),
                        thumbnail=log_thumb,
                        duration=log_duration,
                        width=log_w,
                        height=log_h,
                    )
        except Exception as e:
            logger.debug(f"xh log channel schedule err: {e}")
    except Exception as e:
        logger.exception("xh upload err")
        try:
            await status_msg.edit_text(f"❌ Upload failed: <code>{e}</code>")
        except Exception:
            pass
    finally:
        # Cleanup: remove original file + any split parts/directories
        try:
            # Remove all parts
            for part in parts_to_upload:
                try:
                    if part and os.path.exists(part):
                        os.remove(part)
                except Exception:
                    pass
            # Remove parts directory if exists
            try:
                base_no_ext = os.path.splitext(final_file)[0]
                parts_dir = base_no_ext + "_parts"
                if os.path.isdir(parts_dir):
                    import shutil
                    shutil.rmtree(parts_dir, ignore_errors=True)
            except Exception:
                pass
            # Remove original
            if final_file and os.path.exists(final_file):
                os.remove(final_file)
        except Exception:
            pass


async def _auto_delete(msg, delay: int = 10):
    """Delete a message after `delay` seconds (fire-and-forget)."""
    try:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception:
        pass


async def get_video_whd(path):
    import json as _json
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height:format=duration", "-of", "json", path]
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    d = _json.loads(out.decode("utf-8", "ignore") or "{}")
    v = (d.get("streams") or [{}])[0]
    f = d.get("format") or {}
    return int(v.get("width") or 0), int(v.get("height") or 0), int(float(f.get("duration") or 0))


async def _split_large_video(file_path: str, max_size_bytes: int = 1900000000, status_msg=None):
    """Split large video (>1.9GB) into multiple parts using ffmpeg stream-copy (fast).
    Returns list of part file paths. If file is small enough returns [file_path]."""
    file_size = os.path.getsize(file_path)
    if file_size <= max_size_bytes:
        return [file_path]

    num_parts = int(math.ceil(file_size / max_size_bytes))
    base, ext = os.path.splitext(os.path.basename(file_path))
    out_dir = os.path.join(os.path.dirname(file_path), f"{base}_parts")
    os.makedirs(out_dir, exist_ok=True)

    # Get video duration for time-based split
    _, _, duration = await get_video_whd(file_path)
    part_duration = (duration / num_parts) if duration and duration > 0 else None

    split_files = []
    split_start = time.time()

    def _bar(pct: float) -> str:
        return "█"*int(pct//5) + "░"*(20 - int(pct//5))

    for i in range(num_parts):
        out_part = os.path.join(out_dir, f"{base}_part{i+1:02d}{ext}")
        start_offset = i * part_duration if part_duration else 0

        # Build ffmpeg command
        if part_duration:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", str(start_offset),
                "-t", str(part_duration + 2),  # small overlap to avoid gap, but safe
                "-i", file_path,
                "-c", "copy",
                "-avoid_negative_ts", "1",
                out_part,
            ]
        else:
            # Fallback: dd split by size (for non-video files, though xh videos are always mp4)
            skip_mb = i * (max_size_bytes // (1024*1024))
            count_mb = max_size_bytes // (1024*1024)
            cmd = ["dd", f"if={file_path}", f"of={out_part}", "bs=1M", f"skip={skip_mb}", f"count={count_mb}"]

        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        if os.path.exists(out_part) and os.path.getsize(out_part) > 1024:
            split_files.append(out_part)

        if status_msg:
            pct = ((i+1)/num_parts)*100
            elapsed = int(time.time() - split_start)
            try:
                await status_msg.edit_text(
                    f"✂️ **Splitting Large File**\n\n"
                    f"🎬 {base[:50]}\n"
                    f"┃ `{_bar(pct)}` {pct:.0f}%\n"
                    f"┠ 📦 Part: {i+1}/{num_parts}\n"
                    f"┠ 📏 Size: {humanbytes(file_size)} → {num_parts} parts\n"
                    f"┠ ⏱ Elapsed: {elapsed}s"
                )
            except Exception:
                pass

    return split_files if split_files else [file_path]


def time_formatter_delta(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"
