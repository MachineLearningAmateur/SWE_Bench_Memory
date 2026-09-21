from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "memory_packages" / "trajdebug_flat_rag_86_COMPLETE.zip"
OUT = ROOT / "memory"
PACKAGE = OUT / "trajdebug_flat_rag_86"


def bootstrap_memory(force: bool = False) -> Path:
    """Extract the same-information memory package locally without executing its contents."""
    if PACKAGE.exists() and not force:
        return PACKAGE
    if not ZIP.exists():
        raise FileNotFoundError(f"Missing {ZIP}. Put trajdebug_flat_rag_86_COMPLETE.zip in memory_packages/.")
    if force and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP) as zf:
        for info in zf.infolist():
            p = Path(info.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"Unsafe ZIP member: {info.filename}")
        zf.extractall(OUT)
    db = PACKAGE / "data" / "flat_rag.sqlite"
    if not db.exists():
        raise RuntimeError("Memory extraction completed but flat_rag.sqlite was not found")
    return PACKAGE


if __name__ == "__main__":
    print(bootstrap_memory())
