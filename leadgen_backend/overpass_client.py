import time
import logging
import socket
import overpy
from typing import List, Tuple, Dict, Any, Optional, Union
from .config import get_overpass_endpoint, get_chunk_size_degrees

logger = logging.getLogger(__name__)

# Fallback public Overpass API mirrors in case the primary is down/rate-limited
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

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

# 10-15 default human-readable categories mapped to their OSM tag equivalents
CATEGORY_MAPPING = {
    "Restaurants": [("amenity", "restaurant"), ("amenity", "fast_food"), ("amenity", "food_court")],
    "Cafes": [("amenity", "cafe"), ("amenity", "bar"), ("amenity", "pub")],
    "Retail Shops": [("shop", "*")],
    "Offices": [("office", "*")],
    "Hotels": [("tourism", "hotel"), ("tourism", "motel"), ("tourism", "guest_house"), ("tourism", "hostel")],
    "Healthcare": [("amenity", "clinic"), ("amenity", "hospital"), ("amenity", "pharmacy"), ("amenity", "doctors"), ("amenity", "dentist")],
    "Law Firms": [("office", "lawyer")],
    "Real Estate Agencies": [("office", "estate_agent")],
    "Marketing Agencies": [("office", "advertising"), ("office", "marketing")],
    "Construction Companies": [("office", "construction")],
    "Software Companies": [("office", "it"), ("office", "telecommunication")],
    "Hair Salons": [("shop", "hairdresser"), ("shop", "beauty")],
    "Supermarkets": [("shop", "supermarket"), ("shop", "convenience")],
    "Automotive Services": [("amenity", "car_wash"), ("amenity", "car_repair"), ("shop", "car")]
}

def _chunk_bbox(bbox: Tuple[float, float, float, float]) -> List[Tuple[float, float, float, float]]:
    """Split a bounding box into smaller grid cells if it exceeds the step size."""
    min_lat, min_lon, max_lat, max_lon = bbox
    step = get_chunk_size_degrees()
    lat_steps = max(1, int(((max_lat - min_lat) / step) + 1))
    lon_steps = max(1, int(((max_lon - min_lon) / step) + 1))
    chunks = []
    for i in range(lat_steps):
        south = min_lat + i * step
        north = min(south + step, max_lat)
        for j in range(lon_steps):
            west = min_lon + j * step
            east = min(west + step, max_lon)
            chunks.append((south, west, north, east))
    return chunks

def _resolve_category_tags(category: Optional[str], is_custom_category: bool = False) -> List[Tuple[str, str]]:
    """Resolve human-readable category or custom tag to OSM key-value pairs."""
    if not category:
        return []
    
    if is_custom_category:
        cat_str = category.strip()
        if "=" in cat_str:
            k, v = cat_str.split("=", 1)
            return [(k.strip(), v.strip())]
        else:
            return [(cat_str, "*")]
            
    return CATEGORY_MAPPING.get(category, [(category, "*")])

def _build_overpass_ql(bbox: Tuple[float, float, float, float], tag_filters: List[Tuple[str, str]], is_custom: bool = False, custom_str: str = "") -> str:
    """Build a raw Overpass QL query string combining bbox + tag filters."""
    south, west, north, east = bbox
    bbox_str = f"({south},{west},{north},{east})"
    
    lines = []
    # If custom tag search without = (e.g. searching for a name keyword like "Starbucks")
    if is_custom and custom_str and not ("=" in custom_str):
        # Treat as direct OSM tag search across name, shop, amenity, office using Overpass regex matching
        regex_val = custom_str.replace('"', '\\"')
        for el_type in ["node", "way", "relation"]:
            for tag in ["name", "shop", "amenity", "office"]:
                lines.append(f'  {el_type}["{tag}"~"{regex_val}",i]{bbox_str};')
    else:
        # Standard key=value matching
        for el_type in ["node", "way", "relation"]:
            if not tag_filters:
                # Default set covering common lead-gen verticals if no category selected
                for key in ["amenity", "shop", "tourism", "leisure", "office"]:
                    lines.append(f'  {el_type}["{key}"]{bbox_str};')
            else:
                for k, v in tag_filters:
                    if v == "*":
                        lines.append(f'  {el_type}["{k}"]{bbox_str};')
                    else:
                        lines.append(f'  {el_type}["{k}"="{v}"]{bbox_str};')
                        
    block = "\n".join(lines)
    return f"""[out:json][timeout:90];
(
{block}
);
out center;
"""

def _fetch_chunk_overpy(query_str: str, endpoint: str, retries: int = 3) -> overpy.Result:
    """Execute Overpass query using overpy with retry logic and mirror fallback."""
    primary_endpoint = endpoint
    endpoints = []
    for url in [primary_endpoint] + OVERPASS_MIRRORS:
        if url not in endpoints:
            endpoints.append(url)
    
    last_err = None
    for url in endpoints:
        api = overpy.Overpass(url=url)
        for attempt in range(1, retries + 1):
            try:
                result = api.query(query_str)
                return result
            except Exception as e:
                last_err = e
                if _is_offline_error(e):
                    raise RuntimeError("Internet Connection Error: Unable to connect to Overpass server. Please check your internet connection.") from e
                logger.warning("Attempt %d failed on endpoint %s: %s", attempt, url, str(e))
                if attempt < retries:
                    time.sleep(2 ** (attempt - 1))
                    
    raise last_err or Exception("All Overpass endpoints failed")

def query_pois(
    bbox: Tuple[float, float, float, float],
    category: Optional[str] = None,
    is_custom_category: bool = False,
    limit_mode: str = "capped",
    limit_value: int = 500,
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """Query Overpass for points of interest within the resolved bounding box.
    
    Returns:
        Tuple: (all_records, is_custom_match_flag, is_chunked_flag)
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    area = (max_lat - min_lat) * (max_lon - min_lon)
    
    # Warning/logging if large area and cap is high or disabled
    if limit_mode in ("increased", "disabled") and area > 10.0:
        logger.warning(
            "Large bounding box area (%.2f sq deg) combined with limit_mode '%s' (cap: %s) may produce Overpass timeout.",
            area, limit_mode, str(limit_value)
        )

    # Resolve tag filters
    tag_filters = _resolve_category_tags(category, is_custom_category)
    is_custom_match = is_custom_category and category and not ("=" in category)

    chunks = _chunk_bbox(bbox)
    is_chunked = len(chunks) > 1
    endpoint = get_overpass_endpoint()
    
    all_records = []
    seen_ids = set()
    
    def get_coords(el, el_type):
        if el_type == "node":
            return float(el.lat), float(el.lon)
        clat = getattr(el, "center_lat", None)
        clon = getattr(el, "center_lon", None)
        if clat is not None and clon is not None:
            return float(clat), float(clon)
        if el_type == "way" and getattr(el, "nodes", None):
            lats = [float(n.lat) for n in el.nodes if getattr(n, "lat", None) is not None]
            lons = [float(n.lon) for n in el.nodes if getattr(n, "lon", None) is not None]
            if lats and lons:
                return sum(lats)/len(lats), sum(lons)/len(lons)
        return None, None

    def extract_record(el, el_type):
        tags = el.tags or {}
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        website = tags.get("website") or tags.get("contact:website") or ""
        
        addr_fields = [tags.get(f) for f in ["addr:housenumber", "addr:street", "addr:city", "addr:postcode"] if tags.get(f)]
        address = " ".join(addr_fields) if addr_fields else (tags.get("addr:full") or "")
        
        # Determine category matched tag
        matched_tag = ""
        for k in ["amenity", "shop", "office", "tourism", "leisure"]:
            if k in tags:
                matched_tag = f"{k}={tags[k]}"
                break
        if not matched_tag:
            matched_tag = "custom=match"
            
        lat, lon = get_coords(el, el_type)
        return {
            "osm_id": el.id,
            "osm_type": el_type,
            "name": tags.get("name"),
            "phone": phone,
            "website": website,
            "address": address,
            "category": matched_tag,
            "lat": lat,
            "lon": lon
        }

    for idx, chunk in enumerate(chunks):
        if idx > 0:
            time.sleep(1.0)  # Sleep 1 second between chunk requests to avoid rate limits
            
        query_str = _build_overpass_ql(chunk, tag_filters, is_custom_match, category or "")
        result = _fetch_chunk_overpy(query_str, endpoint)
        
        # List comprehensions to extract records per type, discarding elements without names
        nodes_recs = [extract_record(n, "node") for n in result.nodes if n.tags and n.tags.get("name")]
        ways_recs = [extract_record(w, "way") for w in result.ways if w.tags and w.tags.get("name")]
        rels_recs = [extract_record(r, "relation") for r in result.relations if r.tags and r.tags.get("name")]
        
        chunk_recs = nodes_recs + ways_recs + rels_recs
        
        for rec in chunk_recs:
            dup_key = (rec["osm_id"], rec["osm_type"])
            if dup_key in seen_ids:
                continue
            seen_ids.add(dup_key)
            all_records.append(rec)
            
            # Enforce cap limits
            if limit_mode in ("capped", "increased") and len(all_records) >= limit_value:
                return all_records, is_custom_match, is_chunked
                
    return all_records, is_custom_match, is_chunked
