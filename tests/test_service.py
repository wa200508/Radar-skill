import subprocess
import sys
from pathlib import Path


def test_standalone_tcp_http_and_unchanged_replay():
    root=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(root/'scripts/qualify_independence.py')],check=True,timeout=45)
