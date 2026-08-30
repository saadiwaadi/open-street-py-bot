import time
import urllib.parse
import requests
import logging
from functools import lru_cache
from typing import Tuple, Optional

from .config import get_user_agent

logger = logging.getLogger(__name__)

# Nominatim base URL
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Simple per-process rate limiter (1 request per second).
_last_request_timestamp = 0.0

def _rate_limit() -> None:
    """Ensure at least one second has passed since the previous request.

    This function is deliberately lightweight; it updates the global timestamp
    after sleeping if needed. It is used internally by ``_fetch``.
    """
    global _last_request_timestamp
    now = time.time()
    elapsed = now - _last_request_timestamp
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_request_timestamp = time.time()

import socket

def _is_offline_error(exc: Exception) -> bool:
    """Detect if an exception is due to no internet connection or DNS resolution failure."""
    if isinstance(exc, socket.gaierror):
        return True
    msg = str(exc).lower()
    offline_keywords = [
        "getaddrinfo failed",
        "name resolution",
        "errno 11001",
        "no route to host",
        "network is unreachable",
        "failed to establish a new connection",
        "max retries exceeded with url"
    ]
    return any(k in msg for k in offline_keywords)

def _fetch(query: str) -> dict:
    """Perform a GET request to Nominatim and return the parsed JSON.

    Attempts up to 3 times with exponential backoff on failure.
    Directly catches network connection loss to avoid wasteful retries.
    """
    params = {
        "q": query,
        "format": "json",
        "limit": "1",
        "addressdetails": "0",
    }
    headers = {"User-Agent": get_user_agent()}
    
    retries = 3
    for attempt in range(1, retries + 1):
        _rate_limit()
        try:
            response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data:
                raise ValueError(f"No results found for query: {query}")
            return data[0]
        except Exception as e:
            if _is_offline_error(e):
                raise RuntimeError("Internet Connection Error: Unable to reach geocoding service. Please check your internet connection.") from e
            logger.warning(
                "Nominatim geocoding attempt %d/3 failed for query '%s': %s",
                attempt, query, str(e)
            )
            if attempt == retries:
                raise e
            time.sleep(2 ** (attempt - 1))


@lru_cache(maxsize=128)
def geocode(country: str, city: Optional[str] = None) -> Tuple[float, float, float, float]:
    """Resolve a country (and optional city) to a bounding box.

    The function respects the Nominatim usage policy by:
        * Using a descriptive ``User-Agent`` header (from ``config``).
        * Limiting calls to at most one per second.
        * Caching identical look‑ups for the lifetime of the process.

    Args:
        country: Required country name.
        city: Optional city name. If provided, the query becomes "city, country".

    Returns:
        A tuple ``(min_lat, min_lon, max_lat, max_lon)`` representing the
        bounding box of the result. The values are converted to ``float`` for
        downstream arithmetic.
    """
    if not country:
        raise ValueError("Country parameter must be a non‑empty string")
    query = f"{city}, {country}" if city else country
    result = _fetch(query)
    # Nominatim returns "boundingbox": [south, north, west, east] as strings.
    bbox = result.get("boundingbox")
    if not bbox or len(bbox) != 4:
        raise ValueError(f"Invalid bounding box in Nominatim response for query: {query}")
    # Convert to floats and reorder to (min_lat, min_lon, max_lat, max_lon).
    south, north, west, east = map(float, bbox)
    return (south, west, north, east)
