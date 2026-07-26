# -*- coding: utf-8 -*-
"""
Free Proxy Rotation System for xHamster
Automatically rotates through free proxies to avoid rate limiting
"""

import requests
import random
import time
import logging
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


class FreeProxyRotator:
    """Rotates through free proxies to avoid rate limiting"""
    
    def __init__(self):
        self.proxies: List[Dict] = []
        self.current_proxy_index = 0
        self.failed_proxies: set = set()
        self.last_proxy_change = 0
        self.min_proxy_lifetime = 30  # Minimum seconds before changing proxy
        
    def fetch_free_proxies(self) -> List[str]:
        """Fetch free proxies from multiple sources"""
        proxies = []
        
        # Source 1: FreeProxyList
        try:
            response = requests.get(
                "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
                timeout=10
            )
            if response.status_code == 200:
                proxy_list = response.text.strip().split('\n')
                proxies.extend([p.strip() for p in proxy_list if p.strip()])
                logger.info(f"Fetched {len(proxy_list)} proxies from ProxyScrape")
        except Exception as e:
            logger.warning(f"Failed to fetch from ProxyScrape: {e}")
        
        # Source 2: Another free proxy API
        try:
            response = requests.get(
                "https://www.proxy-list.download/api/v1/get?type=http",
                timeout=10
            )
            if response.status_code == 200:
                proxy_list = response.text.strip().split('\n')
                proxies.extend([p.strip() for p in proxy_list if p.strip()])
                logger.info(f"Fetched {len(proxy_list)} proxies from proxy-list.download")
        except Exception as e:
            logger.warning(f"Failed to fetch from proxy-list.download: {e}")
        
        # Source 3: FreeProxyList.net
        try:
            response = requests.get(
                "https://free-proxy-list.net/",
                timeout=10
            )
            if response.status_code == 200:
                # Parse HTML for proxies
                import re
                proxy_matches = re.findall(r'(\d+\.\d+\.\d+\.\d+):(\d+)', response.text)
                proxies.extend([f"{ip}:{port}" for ip, port in proxy_matches])
                logger.info(f"Fetched {len(proxy_matches)} proxies from free-proxy-list.net")
        except Exception as e:
            logger.warning(f"Failed to fetch from free-proxy-list.net: {e}")
        
        return list(set(proxies))  # Remove duplicates
    
    def update_proxies(self):
        """Update the proxy list"""
        new_proxies = self.fetch_free_proxies()
        if new_proxies:
            self.proxies = [{"http": f"http://{p}", "https": f"http://{p}"} for p in new_proxies]
            logger.info(f"Proxy list updated: {len(self.proxies)} proxies available")
        else:
            logger.warning("No proxies fetched, keeping existing list")
    
    def get_working_proxy(self) -> Optional[Dict]:
        """Get a working proxy (rotates through available proxies)"""
        if not self.proxies:
            self.update_proxies()
        
        if not self.proxies:
            return None
        
        # Try to find a working proxy
        attempts = 0
        max_attempts = min(10, len(self.proxies))
        
        while attempts < max_attempts:
            # Rotate to next proxy
            self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxies)
            proxy = self.proxies[self.current_proxy_index]
            
            # Skip if already failed
            proxy_key = proxy["http"]
            if proxy_key in self.failed_proxies:
                attempts += 1
                continue
            
            # Test if proxy is working
            if self._test_proxy(proxy):
                self.last_proxy_change = time.time()
                logger.info(f"Using proxy: {proxy['http']}")
                return proxy
            else:
                self.failed_proxies.add(proxy_key)
                logger.warning(f"Proxy failed: {proxy['http']}")
            
            attempts += 1
        
        # If all proxies failed, refresh the list
        logger.warning("All proxies failed, refreshing proxy list")
        self.failed_proxies.clear()
        self.update_proxies()
        
        return None
    
    def _test_proxy(self, proxy: Dict) -> bool:
        """Test if a proxy is working"""
        try:
            response = requests.get(
                "https://www.xhamster.com/",
                proxies=proxy,
                timeout=5
            )
            return response.status_code in [200, 403, 429]  # Any response means proxy is working
        except:
            return False
    
    def get_proxy_for_request(self) -> Optional[Dict]:
        """Get proxy for making a request (with rotation logic)"""
        current_time = time.time()
        
        # Change proxy if enough time has passed
        if current_time - self.last_proxy_change >= self.min_proxy_lifetime:
            proxy = self.get_working_proxy()
            if proxy:
                return proxy
        
        # Use current proxy
        if self.proxies and self.current_proxy_index < len(self.proxies):
            return self.proxies[self.current_proxy_index]
        
        return None


# Global instance
proxy_rotator = FreeProxyRotator()


def get_proxy_dict() -> Optional[Dict]:
    """Get proxy dictionary for requests"""
    return proxy_rotator.get_proxy_for_request()


def mark_proxy_failed(proxy: Dict):
    """Mark a proxy as failed"""
    proxy_key = proxy.get("http", "")
    if proxy_key:
        proxy_rotator.failed_proxies.add(proxy_key)
        logger.warning(f"Marked proxy as failed: {proxy_key}")
