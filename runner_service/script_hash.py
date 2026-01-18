from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def load_script_hashes(hash_path: Path | None) -> dict[str, str]:
    if not hash_path or not hash_path.exists():
        return {}
    hashes: dict[str, str] = {}
    try:
        with hash_path.open("r", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) != 2:
                    continue
                hashes[row[0]] = row[1]
    except Exception:
        return {}
    return hashes


def write_script_hashes(hash_path: Path | None, hashes: dict[str, str]) -> None:
    if not hash_path:
        return
    with hash_path.open("w", newline="") as f:
        writer = csv.writer(f)
        for path, digest in sorted(hashes.items()):
            writer.writerow([path, digest])


def compute_script_hashes(script_path: Path | None) -> dict[str, str]:
    if not script_path:
        return {}
    script_dir = script_path.parent
    hashes: dict[str, str] = {}
    try:
        main_text = script_path.read_text(encoding="utf-8")
    except Exception:
        main_text = ""
    import_names: set[str] = set()
    for line in main_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            names = stripped[len("import "):].split(",")
            for name in names:
                import_names.add(name.strip().split()[0])
        elif stripped.startswith("from "):
            parts = stripped.split()
            if len(parts) >= 4 and parts[2] == "import":
                import_names.add(parts[1])
    # Always include the main script itself.
    import_names.add(script_path.stem)
    for name in sorted(import_names):
        py_file = script_dir / f"{name}.py"
        if not py_file.exists():
            continue
        try:
            content = py_file.read_bytes()
        except Exception:
            continue
        hashes[str(py_file)] = hashlib.sha256(content).hexdigest()
    return hashes
