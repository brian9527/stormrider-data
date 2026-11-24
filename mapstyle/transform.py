"""Turn an OpenFreeMap style into the one StormRider serves (D1 spec §2.1).

Labels that read any name field show Chinese instead (name:zh-Hant, else the local name), and
`metadata.stormrider:attribution` carries the credit, every upstream layer and the licence
notices verbatim, so they travel with each copy of the file (licence research §0 item 2.3)."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

CHINESE_NAME = ["coalesce", ["get", "name:zh-Hant"], ["get", "name"]]
NOTICE_DIR = Path(__file__).with_name("notices")
ATTRIBUTION_KEY = "stormrider:attribution"

CC_BY_3 = "https://creativecommons.org/licenses/by/3.0/"
CC_BY_4 = "https://creativecommons.org/licenses/by/4.0/"
OFM_STYLES = "https://github.com/hyperknot/openfreemap-styles"
OFM_LICENSE = "https://github.com/hyperknot/openfreemap-styles/blob/main/LICENSE.md"

CREDIT = [
    {"label": "© OpenMapTiles", "url": "https://openmaptiles.org/"},
    {"label": "© OpenStreetMap contributors", "url": "https://www.openstreetmap.org/copyright"},
]
MODIFICATIONS = ["StormRider 修改：地名改為中文（優先 name:zh-Hant，沒有就用 name）"]

STYLES = {
    "liberty": {
        "title": "淺色（OpenFreeMap Liberty）",
        "layers": [
            {"work": "OpenFreeMap Liberty", "author": "OpenFreeMap（Zsolt Ero）", "url": OFM_STYLES,
             "license": "MIT", "licenseUrl": OFM_LICENSE, "changes": "OpenFreeMap 對 OSM Liberty 的修改，例如字型改為 Noto Sans"},
            {"work": "OSM Liberty", "author": "Maputnik 貢獻者", "url": "https://github.com/maputnik/osm-liberty",
             "license": "程式 BSD；設計衍生自 OSM Bright，CC-BY 3.0", "licenseUrl": CC_BY_3,
             "changes": "由 OSM Bright 大幅修改"},
            {"work": "OSM Bright（Mapbox Open Styles）", "author": "Mapbox（© 2014 Mapbox）",
             "url": "https://github.com/mapbox/mapbox-gl-styles", "license": "程式 BSD、設計 CC-BY 3.0",
             "licenseUrl": CC_BY_3, "changes": ""},
        ],
        "notices": ["openfreemap-styles-LICENSE.md", "osm-liberty-LICENSE.md", "mapbox-gl-styles-LICENSE.md"],
    },
    "fiord": {
        "title": "深色（OpenFreeMap Fiord）",
        "layers": [
            {"work": "OpenFreeMap Fiord", "author": "OpenFreeMap（Zsolt Ero）", "url": OFM_STYLES,
             "license": "MIT", "licenseUrl": OFM_LICENSE, "changes": "和 Fiord Color 相同，只把字型改為 Noto Sans"},
            {"work": "Fiord Color", "author": "MapTiler 與 OpenMapTiles 貢獻者（© 2024）",
             "url": "https://github.com/openmaptiles/fiord-color-gl-style",
             "license": "程式 BSD 3-Clause、設計 CC-BY 4.0", "licenseUrl": CC_BY_4, "changes": "衍生自 CartoDB Basemaps"},
            {"work": "CartoDB Basemaps", "author": "Stamen 與 Paul Norman 為 CartoDB Inc. 設計（© 2015 CartoDB）",
             "url": "https://github.com/CartoDB/CartoDB-basemaps", "license": "CC-BY 3.0", "licenseUrl": CC_BY_3,
             "changes": ""},
        ],
        "notices": ["openfreemap-styles-LICENSE.md", "fiord-color-LICENSE.md"],
    },
}


# name, name:latin, name:zh-Hant, name_en, name_int … — but not names, housename or a literal "name: ".
NAME_FIELD = re.compile(r"^name(?:[:_].+)?$")
TOKEN = re.compile(r"\{([^{}]+)\}")
# Operators whose every argument may become the label text.
PASS_THROUGH = {"coalesce", "concat", "to-string", "upcase", "downcase"}


class UnreadableLabel(Exception):
    """A label this parser does not understand; the build stops rather than guess (fail closed)."""


def _outputs(op: str, args: list):
    """The arguments that can become the label text, by operator (Codex plan review 9, second review 7)."""
    if op in PASS_THROUGH:
        return args
    if op == "format":  # ["format", text, {options}, text, {options} …]
        return [a for a in args if not isinstance(a, dict)]
    if op == "match":  # ["match", input, label, output, label, output …, fallback]: labels are not text
        return args[2:-1:2] + args[-1:]
    if op == "case":  # ["case", condition, output, condition, output …, fallback]
        return args[1:-1:2] + args[-1:]
    if op == "step":  # ["step", input, output, stop, output …]
        return args[1::2]
    return None


def _fields(expr, top: bool) -> set:
    """The fields whose value can end up in the label. Only a whole text-field string is the legacy
    "{field}" token form; strings inside expressions are literals."""
    if isinstance(expr, str):
        return set(TOKEN.findall(expr)) if top else set()
    if expr is None or isinstance(expr, (bool, int, float)):
        return set()
    if not isinstance(expr, list) or not expr or not isinstance(expr[0], str):
        raise UnreadableLabel(json.dumps(expr, ensure_ascii=False))  # a legacy function, or not an expression
    op, args = expr[0], expr[1:]
    if op == "literal":
        return set()
    if op == "get":
        if len(args) == 1 and isinstance(args[0], str):
            return {args[0]}
        raise UnreadableLabel(json.dumps(expr, ensure_ascii=False))  # a computed or object-scoped field
    outputs = _outputs(op, args)
    if outputs is None:
        raise UnreadableLabel(json.dumps(expr, ensure_ascii=False))
    fields = set()
    for arg in outputs:
        fields |= _fields(arg, False)
    return fields


def reads_name(field) -> bool:
    return any(NAME_FIELD.match(f) for f in _fields(field, True))


def chinese_labels(style: dict) -> tuple[dict, int]:
    """A copy of `style` whose name labels show Chinese, and how many layers changed. Raises
    UnreadableLabel for a label it cannot read, which stops the build (and the publish)."""
    out = copy.deepcopy(style)
    changed = 0
    for layer in out.get("layers", []):
        layout = layer.get("layout") or {}
        if layer.get("type") == "symbol" and "text-field" in layout and reads_name(layout["text-field"]):
            layout["text-field"] = copy.deepcopy(CHINESE_NAME)
            changed += 1
    return out, changed


def attribution(name: str) -> dict:
    spec = STYLES[name]
    return {
        "version": 1,
        "style": name,
        "title": spec["title"],
        "credit": copy.deepcopy(CREDIT),
        "modifications": list(MODIFICATIONS),
        "layers": copy.deepcopy(spec["layers"]),
        "notices": [{"file": f, "text": (NOTICE_DIR / f).read_text(encoding="utf-8")} for f in spec["notices"]],
    }


def transform(name: str, upstream: dict) -> tuple[dict, int]:
    style, changed = chinese_labels(upstream)
    metadata = style.get("metadata")
    style["metadata"] = dict(metadata) if isinstance(metadata, dict) else {}
    style["metadata"][ATTRIBUTION_KEY] = attribution(name)
    return style, changed


def encode(style: dict) -> bytes:
    """The published bytes: UTF-8, sorted keys, no spaces — the same style always encodes the same.
    NaN and Infinity raise: Python reads and writes them, JSON.parse in the app does not."""
    return json.dumps(style, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def license_markdown() -> str:
    """map/<build>/LICENSE.md: the same content as the metadata, for people."""
    lines = [
        "# StormRider map styles / StormRider 地圖樣式",
        "",
        "這個資料夾是 StormRider app 用的兩份地圖樣式檔，修改自 OpenFreeMap 的 Liberty 與 Fiord。"
        "每份樣式檔的 `metadata.stormrider:attribution` 帶著同樣的內容。",
        "",
        "地圖上要看得到的出處：© OpenMapTiles（https://openmaptiles.org/）、"
        "© OpenStreetMap contributors（https://www.openstreetmap.org/copyright）。",
        "",
    ]
    for name in STYLES:
        a = attribution(name)
        lines += [f"## {a['title']}", ""]
        lines += [f"- {m}" for m in a["modifications"]]
        for layer in a["layers"]:
            changes = f"；{layer['changes']}" if layer["changes"] else ""
            lines.append(f"- {layer['work']}，{layer['author']}，{layer['url']}，{layer['license']}（{layer['licenseUrl']}）{changes}")
        lines.append("")
        for notice in a["notices"]:
            lines += [f"### {notice['file']}", "", "```", notice["text"].rstrip("\n"), "```", ""]
    return "\n".join(lines)
