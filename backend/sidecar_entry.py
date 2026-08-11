"""
sidecar_entry.py
PyInstaller entrypoint for the Tauri sidecar build. Not used in normal
dev (`fastapi dev` / `uvicorn app.main:app`) — only when packaged into the
desktop app, where the Tauri Rust side spawns this binary and sets PORT.
"""
import os

import uvicorn

from app.main import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8756"))
    uvicorn.run(app, host="127.0.0.1", port=port)
