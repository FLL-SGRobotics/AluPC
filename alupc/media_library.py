"""Mediathek: gespeicherte und zuletzt gezeigte Bilder, Videos und Diashows (ohne Qt, testbar).

In der Einstellung „media“:
    saved  – vom Benutzer gespeichert (bleiben, bis man sie entfernt)
    recent – automatisch: zuletzt auf Monitor 2 gezeigt (höchstens RECENT_MAX)
Ein Eintrag ist eine normale Quellen-Einstellung (type image/video/slideshow) plus „title“.
"""

from __future__ import annotations

import os
from pathlib import Path

MEDIA_TYPES = ("image", "video", "slideshow")
RECENT_MAX = 12
KEEP = ("type", "path", "folder", "title", "loop", "volume", "muted", "interval", "fit")


def key(cfg: dict) -> tuple[str, str]:
    """Eindeutig pro Datei/Ordner (gleiche Datei = gleicher Eintrag, egal welche Optionen)."""
    target = cfg.get("folder") if cfg.get("type") == "slideshow" else cfg.get("path")
    return cfg.get("type", ""), os.path.normcase(os.path.abspath(target)) if target else ""


def default_title(cfg: dict) -> str:
    target = cfg.get("folder") if cfg.get("type") == "slideshow" else cfg.get("path")
    return Path(target or "").stem or Path(target or "").name or "Ohne Namen"


def clean(cfg: dict, title: str | None = None) -> dict:
    item = {k: v for k, v in cfg.items() if k in KEEP and v not in (None, "")}
    item["title"] = (title or cfg.get("title") or default_title(cfg)).strip()
    return item


def exists(cfg: dict) -> bool:
    target = cfg.get("folder") if cfg.get("type") == "slideshow" else cfg.get("path")
    if not target:
        return False
    from .platform.shared_paths import resolve

    return Path(resolve(target, mount=False)).exists()


def _lists(config) -> dict:
    media = dict(config["media"])
    media.setdefault("saved", [])
    media.setdefault("recent", [])
    return media


def saved(config) -> list[dict]:
    return list(_lists(config)["saved"])


def recent(config) -> list[dict]:
    """Zuletzt gezeigt – ohne die, die ohnehin gespeichert sind."""
    kept = {key(i) for i in saved(config)}
    return [i for i in _lists(config)["recent"] if key(i) not in kept]


def is_saved(config, cfg: dict) -> bool:
    return key(cfg) in {key(i) for i in saved(config)}


def save(config, cfg: dict, title: str | None = None) -> dict:
    """Speichern (bzw. Titel/Optionen aktualisieren) – neuer Eintrag steht vorne."""
    media = _lists(config)
    item = clean(cfg, title)
    media["saved"] = [item] + [i for i in media["saved"] if key(i) != key(item)]
    config["media"] = media
    return item


def save_many(config, cfgs: list[dict]) -> int:
    count = 0
    for cfg in reversed(cfgs):  # Reihenfolge der Auswahl beibehalten
        if cfg.get("type") in MEDIA_TYPES:
            save(config, cfg)
            count += 1
    return count


def remove(config, cfg: dict) -> None:
    media = _lists(config)
    k = key(cfg)
    media["saved"] = [i for i in media["saved"] if key(i) != k]
    media["recent"] = [i for i in media["recent"] if key(i) != k]
    config["media"] = media


def rename(config, cfg: dict, title: str) -> None:
    media = _lists(config)
    k = key(cfg)
    media["saved"] = [{**i, "title": title.strip() or default_title(i)} if key(i) == k else i
                      for i in media["saved"]]
    config["media"] = media


def remember(config, cfg: dict) -> None:
    """Wurde gerade auf Monitor 2 gezeigt → oben in „Zuletzt“."""
    if cfg.get("type") not in MEDIA_TYPES:
        return
    media = _lists(config)
    item = clean(cfg)
    media["recent"] = ([item] + [i for i in media["recent"] if key(i) != key(item)])[:RECENT_MAX]
    config["media"] = media


def file_type(path: str) -> str | None:
    """„image“/„video“ nach Dateiendung – None, wenn unbekannt."""
    from .sources import IMAGE_SUFFIXES

    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return None


VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
