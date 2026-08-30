import sys
from pathlib import Path

# Ensure project root directory is in sys.path for Vercel serverless functions
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from leadgen_backend.main import app
