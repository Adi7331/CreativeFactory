"""Project layout and safe migration helpers for Generator filmów Adi."""

from __future__ import annotations

import csv
import json
import secrets
import shutil
from pathlib import Path


FOLDERS = {
    "hooks": "Hooki",
    "clips": "Klipy",
    "cta": "Zakończenia",
    "output": "Gotowe filmy",
    "work": "_robocze",
}
LEGACY_FOLDERS = {"hooks": "hooks", "clips": "clips", "cta": "cta", "output": "output", "work": "_work"}


def role_dir(project: Path, role: str) -> Path:
    if role not in ("hooks", "clips", "cta"):
        raise ValueError(f"Nieznana biblioteka materiałów: {role}")
    preferred = project / FOLDERS[role]
    legacy = project / LEGACY_FOLDERS[role]
    return preferred if preferred.is_dir() or not legacy.is_dir() else legacy


def output_dir(project: Path) -> Path:
    preferred = project / FOLDERS["output"]
    legacy = project / LEGACY_FOLDERS["output"]
    return preferred if preferred.is_dir() or not legacy.is_dir() else legacy


def work_dir(project: Path) -> Path:
    preferred = project / FOLDERS["work"]
    legacy = project / LEGACY_FOLDERS["work"]
    return preferred if preferred.is_dir() or not legacy.is_dir() else legacy


def _has_legacy_layout(project: Path) -> bool:
    return any((project / LEGACY_FOLDERS[key]).is_dir() for key in ("hooks", "clips", "cta", "output"))


def create_project(path: Path, defaults: dict | None = None) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    legacy = _has_legacy_layout(path) and not any((path / FOLDERS[key]).is_dir() for key in ("hooks", "clips", "cta"))
    folders = LEGACY_FOLDERS if legacy else FOLDERS
    for key in ("hooks", "clips", "cta", "output", "work"):
        (path / folders[key]).mkdir(exist_ok=True)
    config_path = path / "config.json"
    if not config_path.exists():
        config_path.write_text(json.dumps(defaults or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _copy_contents(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        elif child.is_file():
            shutil.copy2(child, target)


def migrate_project(source: Path, destination: Path, defaults: dict | None = None) -> Path:
    """Copy project data to Polish folders. Source is intentionally left intact."""
    source, destination = Path(source), Path(destination)
    if not source.is_dir():
        raise FileNotFoundError(f"Nie znaleziono starego projektu: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("Folder źródłowy i docelowy projektu nie mogą być takie same.")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Folder docelowy nie jest pusty: {destination}")

    # Migrate into a sibling staging folder. The visible destination is only
    # replaced after every user file has been copied and verified.
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.migrating-{secrets.token_hex(4)}")
    create_project(staging, defaults)
    try:
        for role in ("hooks", "clips", "cta"):
            _copy_contents(role_dir(source, role), staging / FOLDERS[role])
        _copy_contents(output_dir(source), staging / FOLDERS["output"])

        for settings_name in ("assets.json", "ustawienia_ujec.json"):
            settings = source / settings_name
            if settings.is_file():
                try:
                    values = json.loads(settings.read_text(encoding="utf-8-sig"))
                    if isinstance(values, dict):
                        migrated = {}
                        for key, value in values.items():
                            for old, new in (("hooks/", "Hooki/"), ("clips/", "Klipy/"), ("cta/", "Zakończenia/")):
                                if key.startswith(old):
                                    key = new + key[len(old):]
                                    break
                            migrated[key] = value
                        (staging / settings_name).write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except (OSError, ValueError):
                    raise ValueError(f"Nie można bezpiecznie odczytać ustawień ujęć: {settings}")

        old_config = source / "config.json"
        if old_config.is_file():
            try:
                old = json.loads(old_config.read_text(encoding="utf-8-sig"))
                config = dict(defaults or {})
                if isinstance(old, dict):
                    for key in ("ads_count", "clips_per_ad", "use_cta", "crf", "preset", "threads", "seed"):
                        if key in old:
                            config[key] = old[key]
                    if "output_mode" not in old:
                        config["seed"] = None
                    elif "output_mode" in old:
                        config["output_mode"] = old["output_mode"]
                    legacy_fit = {"crop": "fill", "fill": "fill", "contain": "contain"}.get(old.get("fit"))
                    if legacy_fit:
                        assets_path = staging / "assets.json"
                        try:
                            asset_values = json.loads(assets_path.read_text(encoding="utf-8-sig")) if assets_path.is_file() else {}
                        except (OSError, ValueError) as exc:
                            raise ValueError(f"Nie można bezpiecznie odczytać ustawień ujęć: {assets_path}") from exc
                        if not isinstance(asset_values, dict):
                            asset_values = {}
                        extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
                        for role in ("hooks", "clips", "cta"):
                            folder = staging / FOLDERS[role]
                            for asset in folder.iterdir():
                                if asset.is_file() and asset.suffix.lower() in extensions:
                                    key = f"{FOLDERS[role]}/{asset.name}"
                                    current = asset_values.get(key)
                                    if not isinstance(current, dict):
                                        current = {}
                                        asset_values[key] = current
                                    current.setdefault("fit", legacy_fit)
                        assets_path.write_text(json.dumps(asset_values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                config["output_dir"] = FOLDERS["output"]
                (staging / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except (OSError, ValueError):
                raise ValueError(f"Nie można bezpiecznie odczytać ustawień projektu: {old_config}")

        for manifest in (staging / FOLDERS["output"]).rglob("manifest.csv"):
            try:
                with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                    fields = rows[0].keys() if rows else []
                if not fields:
                    continue
                for row in rows:
                    value = row.get("source", "")
                    for old, new in (("hooks/", "Hooki/"), ("clips/", "Klipy/"), ("cta/", "Zakończenia/")):
                        if value.startswith(old):
                            row["source"] = new + value[len(old):]
                with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
            except (OSError, csv.Error):
                continue

        _verify_copy(source, staging)
        if destination.exists():
            destination.rmdir()  # previously verified empty above
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def _verify_copy(source: Path, destination: Path) -> None:
    """Compare copied source assets and output by relative path and size."""
    pairs = []
    for role in ("hooks", "clips", "cta"):
        original = role_dir(source, role)
        if original.is_dir():
            pairs.append((original, destination / FOLDERS[role]))
    original_output = output_dir(source)
    if original_output.is_dir():
        pairs.append((original_output, destination / FOLDERS["output"]))
    for original, copied in pairs:
        for item in original.rglob("*"):
            if not item.is_file():
                continue
            target = copied / item.relative_to(original)
            if not target.is_file():
                raise OSError(f"Nie udało się sprawdzić kopii pliku: {item}")
            if item.name.casefold() == "manifest.csv":
                with item.open("r", encoding="utf-8-sig", newline="") as handle:
                    before = csv.DictReader(handle)
                    before_fields, before_rows = before.fieldnames, list(before)
                with target.open("r", encoding="utf-8-sig", newline="") as handle:
                    after = csv.DictReader(handle)
                    after_fields, after_rows = after.fieldnames, list(after)
                if before_fields != after_fields or len(before_rows) != len(after_rows):
                    raise OSError(f"Nie udało się sprawdzić historii wyników: {item}")
                for row in before_rows:
                    value = row.get("source", "")
                    for old, new in (("hooks/", "Hooki/"), ("clips/", "Klipy/"), ("cta/", "Zakończenia/")):
                        if value.startswith(old):
                            row["source"] = new + value[len(old):]
                            break
                if before_rows != after_rows:
                    raise OSError(f"Historia wyników uległa zmianie podczas kopiowania: {item}")
            elif target.stat().st_size != item.stat().st_size:
                raise OSError(f"Nie udało się sprawdzić kopii pliku: {item}")
