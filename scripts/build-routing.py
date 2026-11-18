"""Local entry point: .venv/bin/python -I -B scripts/build-routing.py --workdir .build/routing ..."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing.main import main  # noqa: E402

raise SystemExit(main())
