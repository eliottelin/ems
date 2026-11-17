"""Run the same live-provider check shipped in exported projects."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'template_generated_app'))
from check_live_data import main

if __name__ == '__main__':
    raise SystemExit(main())
