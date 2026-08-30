import re
from typing import List, Dict, Any
import pandas as pd
import numpy as np
from rapidfuzz import fuzz

def clean_phone_vectorized(series: pd.Series) -> pd.Series:
    """Normalize phone numbers in a vectorized way: strip non-numeric except leading '+'."""
    s = series.astype(str).str.strip()
    has_plus = s.str.startswith("+")
    digits = s.str.replace(r"[^\d]", "", regex=True)
    cleaned = np.where(has_plus, "+" + digits, digits)
    # Handle empty/invalid cells
    is_empty = series.isna() | (series.astype(str) == "") | (series.astype(str) == "nan") | (series.astype(str) == "—")
    return np.where(is_empty, "—", cleaned)

def haversine_vectorized(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Calculate great-circle distance between coordinates using vectorized NumPy operations."""
    R = 6371000.0  # Radius of Earth in meters
    
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    
    a = np.sin(dphi / 2.0)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2.0)**2
    c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return R * c

def deduplicate_fuzzy(df: pd.DataFrame, max_distance_m: float = 50.0, similarity_threshold: float = 80.0) -> pd.DataFrame:
    """Fuzzy deduplication combining rapidfuzz string similarity and Haversine geographic distance."""
    if df.empty:
        return df
    
    # 1. Exact duplicate check for OSM ID and type
    df = df.drop_duplicates(subset=["osm_id", "osm_type"], keep="first").reset_index(drop=True)
    
    if len(df) <= 1:
        return df
        
    # Add normalized name for comparison
    df["norm_name"] = df["name"].astype(str).str.lower().str.strip()
    keep_mask = np.ones(len(df), dtype=bool)
    
    lats = df["lat"].values
    lons = df["lon"].values
    names = df["norm_name"].values
    
    # Group by spatial grid (approx ~1km binning) to avoid global O(N^2)
    lat_bins = np.round(lats, 2)
    lon_bins = np.round(lons, 2)
    grid_keys = list(zip(lat_bins, lon_bins))
    df["grid_key"] = grid_keys
    
    for _, group in df.groupby("grid_key"):
        indices = group.index.values
        g_lats = lats[indices]
        g_lons = lons[indices]
        g_names = names[indices]
        m = len(indices)
        if m <= 1:
            continue
            
        for i in range(m):
            if not keep_mask[indices[i]]:
                continue
            if pd.isna(g_lats[i]) or pd.isna(g_lons[i]):
                continue
                
            for j in range(i + 1, m):
                if not keep_mask[indices[j]]:
                    continue
                if pd.isna(g_lats[j]) or pd.isna(g_lons[j]):
                    continue
                    
                dist = haversine_vectorized(
                    np.array([g_lats[i]]), np.array([g_lons[i]]),
                    np.array([g_lats[j]]), np.array([g_lons[j]])
                )[0]
                
                if dist <= max_distance_m:
                    sim = fuzz.token_set_ratio(g_names[i], g_names[j])
                    if sim >= similarity_threshold:
                        keep_mask[indices[j]] = False

    cols_to_drop = [c for c in ["norm_name", "grid_key"] if c in df.columns]
    df_clean = df[keep_mask].drop(columns=cols_to_drop).reset_index(drop=True)
    return df_clean

def process_pois(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """Full cleaning and deduplication pipeline using pandas/NumPy vectorization."""
    if not records:
        return pd.DataFrame(columns=["osm_id", "osm_type", "name", "phone", "website", "address", "category", "lat", "lon"])
        
    df = pd.DataFrame(records)
    
    # Drop rows with empty/null name
    df = df.dropna(subset=["name"])
    df = df[df["name"].astype(str).str.strip() != ""]
    
    if df.empty:
        return df
        
    # Vectorized phone normalization
    df["phone"] = clean_phone_vectorized(df["phone"])
    
    # Vectorized deduplication (exact and fuzzy distance-based)
    df = deduplicate_fuzzy(df)
    
    return df

def prepare_json_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert DataFrame to clean JSON-serializable list of records."""
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    clean_records = []
    for r in records:
        clean_r = {}
        for k, v in r.items():
            if pd.isna(v):
                clean_r[k] = None
            elif isinstance(v, (np.integer, int)):
                clean_r[k] = int(v)
            elif isinstance(v, (np.floating, float)):
                clean_r[k] = float(v)
            else:
                clean_r[k] = str(v)
        clean_records.append(clean_r)
    return clean_records
