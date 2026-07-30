"""
Startup entrypoint for Wispbyte (or any host where you pick a single
Python file to run, rather than supplying a Dockerfile/CLI command).

Wispbyte's panel typically wants ONE file selected as the app's entry
point. Point it at this file (e.g. `python run.py` as the startup
command, or select `run.py` in the file picker) and it launches the
FastAPI app with uvicorn, reading the port from whatever environment
variable the host injects.
"""
import os
import sys
from pathlib import Path

# Load .env BEFORE anything else touches os.environ (config.py reads
# env vars at import time via get_settings()).
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

# Make sure `backend/` is importable regardless of the working directory
# the panel launches this script from.
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn

if __name__ == "__main__":
    # Most panel-style hosts (Wispbyte included) inject the port to bind
    # to via a PORT environment variable. Fall back to 7860 for local
    # testing if it isn't set.
    port = int(os.getenv("PORT", os.getenv("SERVER_PORT", "7860")))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, app_dir=str(Path(__file__).resolve().parent / "backend"))
