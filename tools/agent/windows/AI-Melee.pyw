"""Double-click to open the AI-Melee launcher (ai-melee\launcher.py) without a console window.

Python from python.org runs .pyw files with pythonw.exe. "Play AI-Melee.bat" still starts
AI-Melee the old way, with the log in a console window.
"""

import runpy
import sys
from pathlib import Path

launcher = Path(__file__).resolve().parent / "ai-melee" / "launcher.py"
sys.argv[0] = str(launcher)
runpy.run_path(str(launcher), run_name="__main__")
