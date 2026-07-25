# -*- coding: utf-8 -*-
"""
Sxyprn Custom Engine for BIMBO Bot (Final Fixed)
Extracts video URLs from sxyprn.com
"""

import re
import json
import logging
import asyncio
from urllib.parse import urlparse, urljoin
from typing import Optional, Dict, List

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

QLABEL = {
    144: "144p", 240: "240p", 360: "360p", 480: "480p (SD)",
    720: "720p (HD)", 1080: "1080p (FHD)", 1440: "1440p (2K)", 2160: "2160p (4K UHD)",
}


def is_sxyprn(url: str) -> bool:
    try:
        host = urlparse(url).hostname.lower()
        return "sxyprn" in host
    except:
        return False


def _clean_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _extract_post_id(url: str) -> Optional[str]:
    match = re.search(r'sxyprn\.com/post/([a-zA-Z0-9]+)', url)
    if match:
        return match.group(1)
    return None


async def extract_video_info(url: str) -> Optional[Dict]:
    try:
        url = _clean_url(url)
        post_id = _extract_post_id(url)
        
        if not post_id:
            logger.error(f"Could not extract post ID from: {url}")
            return None
        
        logger.info(f"Extracting Sxyprn video: {post_id}")
        
        session = requests.Session()
        session.headers.update({
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Referer': 'https://www.sxyprn.com/',
        })
        
        response = session.get(url, timeout=30, allow_redirects=True)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch page: {response.status_code}")
            return None
        
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract title
        title = None
        title_tag = soup.find('h1') or soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
            title = re.sub(r'\s*-?\s*Sxyprn.*$', '', title, flags=re.I)
        
        # Extract thumbnail
        thumbnail = None
        og_image = soup.find('meta', property='og:image')
        if og_image:
            thumbnail = og_image.get('content')
        
        # Extract duration
        duration = 0
        duration_match = re.search(r'duration["\s:]+(\d+)', html, re.I)
        if duration_match:
            duration = int(duration_match.group(1))
        
        # Extract video URLs - FINAL FIXED VERSION
        qualities = []
        video_urls = []
        
        # Method 1: Extract from <video> tags (main source)
        video_tags = soup.find_all('video')
        logger.debug(f"DEBUG: Found {len(video_tags)} video tags")
        logger.debug(f"DEBUG: video_urls before extraction: {len(video_urls)}")
        for video in video_tags:
            src = video.get('src')
            if src and 'trafficdeposit.com' in src and '/vid/' in src:
                # Add https: prefix if protocol-relative
                if src.startswith('//'):
                    src = 'https:' + src
                if src not in video_urls:
                    video_urls.append(src)
            
            # Also check data attributes
            for attr in ['data-video', 'data-src', 'data-hls']:
                data_src = video.get(attr)
                if data_src and 'trafficdeposit.com' in data_src:
                    if data_src.startswith('//'):
                        data_src = 'https:' + data_src
                    if data_src not in video_urls:
                        video_urls.append(data_src)
        
        # Method 2: Extract from video src attributes via regex
        video_src_pattern = r'<video[^>]+src=["\']([^"\']+trafficdeposit\.com[^"\']*)["\']'
        matches = re.findall(video_src_pattern, html, re.I)
        logger.debug(f"DEBUG: video_urls after extraction: {len(video_urls)}")
        for match in matches:
            if match.startswith('//'):
                match = 'https:' + match
            if match not in video_urls:
                video_urls.append(match)
        
        # Method 3: Extract from JavaScript/inline scripts
        js_pattern = r'["\']?(https?://[^"\']*trafficdeposit\.com/[^"\']+/vid/[^"\']*)["\']?'
        matches = re.findall(js_pattern, html, re.I)
        for match in matches:
            if match not in video_urls:
                video_urls.append(match)
        
        # Process URLs
        for video_url in video_urls:
            try:
                quality_height = 720  # Default
                
                if '2160' in video_url or '4k' in video_url.lower():
                    quality_height = 2160
                elif '1440' in video_url or '2k' in video_url.lower():
                    quality_height = 1440
                elif '1080' in video_url or 'fhd' in video_url.lower():
                    quality_height = 1080
                elif '720' in video_url or 'hd' in video_url.lower():
                    quality_height = 720
                elif '480' in video_url or 'sd' in video_url.lower():
                    quality_height = 480
                elif '360' in video_url:
                    quality_height = 360
                elif '240' in video_url:
                    quality_height = 240
                
                quality_label = QLABEL.get(quality_height, f"{quality_height}p")
                
                qualities.append({
                    'height': quality_height,
                    'label': quality_label,
                    'url': video_url
                })
                
            except Exception as e:
                logger.warning(f"Error processing video URL: {e}")
                continue
        
        # Remove duplicates and sort
        seen_urls = set()
        unique_qualities = []
        for q in qualities:
            if q['url'] not in seen_urls:
                seen_urls.add(q['url'])
                unique_qualities.append(q)
        
        unique_qualities.sort(key=lambda x: x['height'], reverse=True)
        
        if not unique_qualities:
            logger.error("No video URLs found")
            return None
        
        logger.info(f"Found {len(unique_qualities)} quality variants")
        
        return {
            'title': title or 'Sxyprn Video',
            'thumbnail': thumbnail,
            'duration': duration,
            'qualities': unique_qualities,
            'headers': dict(session.headers),
            'webpage_url': url
        }
        
    except Exception as e:
        logger.error(f"Error extracting Sxyprn video: {e}", exc_info=True)
        return None
