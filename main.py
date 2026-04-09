"""
Convenience startup script.
Usage:
    python main.py
or:
    uvicorn backend.main:app --reload --port 8000
"""
import subprocess
import sys


if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--reload", "--port", "8000"],
        check=True,
    )
