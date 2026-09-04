"""
Root entry point for 3D ULPIN Application.
Binds to 0.0.0.0:8000 for local and network access.
"""

import os
from dotenv import load_dotenv

load_dotenv()

import uvicorn
from backend.main import app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print("=" * 65)
    print(" [HOSTED] 3D ULPIN Cadastral Server running at:")
    print(f"  Local URL:   http://localhost:{port}")
    print(f"  Network URL: http://127.0.0.1:{port}")
    print(f"  API Docs:    http://127.0.0.1:{port}/docs")
    print("=" * 65)
    uvicorn.run(app, host="0.0.0.0", port=port)
