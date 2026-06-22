import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

FRONTEND_DIR = ROOT / "frontend"
CASES_DIR = ROOT / "wiki" / "cases"
