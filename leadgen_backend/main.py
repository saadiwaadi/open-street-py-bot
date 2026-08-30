from pathlib import Path
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import router as api_router
from .config import get_cors_origin

app = FastAPI(
    title="Lead Generation Backend API",
    description="Stateless Overpass API lead-generation backend service",
    version="1.0.0",
)

allowed_origin = get_cors_origin()
origins = [allowed_origin] if allowed_origin else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

INDEX_PATH = Path(__file__).resolve().parent.parent / "index.html"


@app.get("/")
def root():
    if INDEX_PATH.exists():
        return FileResponse(INDEX_PATH)
    return {
        "status": "online",
        "service": "Lead Generation API",
        "endpoints": {"search": "/search"},
    }


if __name__ == "__main__":
    uvicorn.run("leadgen_backend.main:app", host="0.0.0.0", port=8000, reload=True)
