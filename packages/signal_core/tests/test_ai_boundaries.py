import json
import re
from pathlib import Path

from signal_core.ai_gateway.prompts import PROMPTS_DIR, file_sha256

ROOT = Path(__file__).resolve().parents[3]
VENDOR_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:anthropic|voyageai)\b", re.MULTILINE)
SKIP_PARTS = {".venv", "site-packages", "node_modules"}


def test_only_providers_import_vendor_sdks() -> None:
    offenders: list[str] = []
    for top in ("packages", "services"):
        for path in (ROOT / top).rglob("*.py"):
            parts = set(path.parts)
            if parts & SKIP_PARTS:
                continue
            if "ai_gateway" in parts and "providers" in parts:
                continue
            if VENDOR_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_released_prompts_unchanged() -> None:
    lock = json.loads((PROMPTS_DIR / "LOCK.json").read_text(encoding="utf-8"))
    found = {
        f"{p.parent.name}/{p.stem}": file_sha256(p.read_bytes())
        for p in PROMPTS_DIR.glob("*/v*.md")
    }
    assert found == lock
