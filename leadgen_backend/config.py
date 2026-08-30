import os
from typing import Optional

# Default configuration values
DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
DEFAULT_USER_AGENT = "LeadGenBackend/1.0 (+https://example.com)"
DEFAULT_CHUNK_SIZE_DEGREES = 0.5  # Approx 55km at equator

# Environment variable names
ENV_OVERPASS_ENDPOINT = "OVERPASS_ENDPOINT"
ENV_USER_AGENT = "USER_AGENT"
ENV_CHUNK_SIZE = "CHUNK_SIZE_DEGREES"
ENV_CORS_ORIGIN = "CORS_ORIGIN"

def get_overpass_endpoint() -> str:
    """Return the Overpass API endpoint, overridable via env var."""
    return os.getenv(ENV_OVERPASS_ENDPOINT, DEFAULT_OVERPASS_ENDPOINT)

def get_user_agent() -> str:
    """Return the HTTP User-Agent header for external API calls.

    Must be a descriptive string per Nominatim usage policy.
    """
    return os.getenv(ENV_USER_AGENT, DEFAULT_USER_AGENT)

def get_chunk_size_degrees() -> float:
    """Return the bounding-box chunk size in degrees.

    Can be overridden via the CHUNK_SIZE_DEGREES env var.
    """
    try:
        return float(os.getenv(ENV_CHUNK_SIZE, str(DEFAULT_CHUNK_SIZE_DEGREES)))
    except ValueError:
        return DEFAULT_CHUNK_SIZE_DEGREES

def get_cors_origin() -> Optional[str]:
    """Return the allowed CORS origin for the FastAPI app.

    If CORS_ORIGIN is not set, the caller can decide whether to allow all origins ("*") or raise an error.
    """
    origin = os.getenv(ENV_CORS_ORIGIN)
    return origin if origin else None
