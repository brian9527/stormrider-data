"""Local entry point: .venv/bin/python -I -B scripts/build-map-styles.py --out .build/map-styles"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapstyle.main import main  # noqa: E402

raise SystemExit(main())
