"""Local entry point: .venv/bin/python -I -B scripts/fetch-pbf.py --pbf-out .build/osm/taiwan-latest.osm.pbf --record .build/osm/pbf.json"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from search.sources import main  # noqa: E402

raise SystemExit(main())
