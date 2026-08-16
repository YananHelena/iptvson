#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
CONFIG_DEFAULT = ROOT / "channels.json"

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
QUALITY_RE = re.compile(
    r"\s*(?:\((?:2160p|1440p|1080p|720p|576p|540p|480p|360p|240p|4K|UHD|FHD|HD|SD)\)|"
    r"\[(?:Geo-blocked|Not 24/7|Offline|Blocked|Geo Blocked|Geo-Blocked)\])\s*",
    re.I,
)
DIRECTIVE_PREFIXES = ("#EXTVLCOPT:", "#EXTHTTP:", "#KODIPROP:")


@dataclass
class Entry:
    extinf: str
    url: str
    name: str
    tvg_id: str
    logo: str
    group: str
    directives: list[str] = field(default_factory=list)


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("ı", "i")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def compact(value: str) -> str:
    return normalize(value).replace(" ", "")


def canonical_id(value: str) -> str:
    # iptv-org may use feed/profile suffixes: TV8.tr@SD, NTV.tr@HD, etc.
    return (value or "").strip().split("@", 1)[0]


def clean_name(value: str) -> str:
    value = QUALITY_RE.sub(" ", value)
    value = re.sub(r"\s*\[[^\]]+\]\s*", " ", value)
    return " ".join(value.split()).strip()


def parse_m3u(text: str) -> list[Entry]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    entries: list[Entry] = []
    pending_extinf: str | None = None
    pending_directives: list[str] = []

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            pending_extinf = line
            pending_directives = []
            continue

        if pending_extinf and line.startswith(DIRECTIVE_PREFIXES):
            pending_directives.append(line)
            continue

        if line.startswith("#"):
            continue

        if pending_extinf and line.startswith(("http://", "https://")):
            attrs = dict(ATTR_RE.findall(pending_extinf))
            display = pending_extinf.split(",", 1)[1].strip() if "," in pending_extinf else ""
            entries.append(
                Entry(
                    extinf=pending_extinf,
                    url=line,
                    name=clean_name(display),
                    tvg_id=attrs.get("tvg-id", ""),
                    logo=attrs.get("tvg-logo", ""),
                    group=attrs.get("group-title", ""),
                    directives=list(pending_directives),
                )
            )
            pending_extinf = None
            pending_directives = []

    return entries


def fetch_text(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "turkiye-iptv-curator/1.0",
            "Accept": "application/x-mpegURL,application/vnd.apple.mpegurl,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig", errors="replace")


def resolution_score(entry: Entry) -> int:
    blob = f"{entry.extinf} {entry.name}".casefold()
    score = 0

    # Prefer HTTPS when multiple upstream streams are available.
    if entry.url.startswith("https://"):
        score += 30

    if "2160p" in blob or "4k" in blob or "uhd" in blob:
        score += 50
    elif "1440p" in blob:
        score += 45
    elif "1080p" in blob or "fhd" in blob:
        score += 40
    elif "720p" in blob or "(hd)" in blob:
        score += 30
    elif "576p" in blob or "540p" in blob:
        score += 15
    elif "480p" in blob:
        score += 8

    # These are preferences, not hard filters. If no alternative exists,
    # the channel remains available in the curated list.
    if "not 24/7" in blob:
        score -= 70
    if "offline" in blob:
        score -= 70
    if "geo-blocked" in blob or "geo blocked" in blob:
        score -= 25
    if "blocked" in blob and "geo" not in blob:
        score -= 25
    if "@sd" in entry.tvg_id.casefold():
        score -= 5

    return score


def exact_match(entry: Entry, spec: dict) -> bool:
    entry_id = canonical_id(entry.tvg_id).casefold()
    wanted_ids = {canonical_id(x).casefold() for x in spec.get("ids", []) if x}
    if entry_id and entry_id in wanted_ids:
        return True

    aliases = spec.get("aliases") or [spec["name"]]
    entry_name_norm = normalize(entry.name)
    entry_name_compact = compact(entry.name)

    for alias in aliases:
        if entry_name_norm == normalize(alias):
            return True
        if entry_name_compact == compact(alias):
            return True

    return False


def fuzzy_score(entry: Entry, spec: dict) -> float:
    aliases = spec.get("aliases") or [spec["name"]]
    entry_name = compact(entry.name)

    # Fuzzy matching is intentionally disabled for very short names such as
    # "24" and "360"; they must match exactly to prevent false positives.
    if len(entry_name) < 4:
        return 0.0

    best = 0.0
    for alias in aliases:
        alias_name = compact(alias)
        if len(alias_name) < 4:
            continue
        best = max(best, difflib.SequenceMatcher(None, alias_name, entry_name).ratio())
    return best


def select_entry(entries: Iterable[Entry], spec: dict, fuzzy_threshold: float, fuzzy_margin: float) -> tuple[Entry | None, str]:
    exact = [e for e in entries if exact_match(e, spec)]
    if exact:
        return max(exact, key=resolution_score), "exact"

    ranked = sorted(
        ((fuzzy_score(e, spec), e) for e in entries),
        key=lambda pair: (pair[0], resolution_score(pair[1])),
        reverse=True,
    )
    if not ranked or ranked[0][0] < fuzzy_threshold:
        return None, "missing"

    top_score, top_entry = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0

    # Avoid guessing when two different channel names are almost equally close.
    if top_score - second_score < fuzzy_margin:
        return None, "ambiguous"

    return top_entry, f"fuzzy:{top_score:.3f}"


def escape_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def infer_output_id(entry: Entry, spec: dict) -> str:
    source_id = canonical_id(entry.tvg_id)
    if source_id:
        return source_id
    ids = spec.get("ids", [])
    return canonical_id(ids[0]) if ids else ""


def render_channel(spec: dict, entry: Entry) -> tuple[list[str], dict]:
    tvg_id = infer_output_id(entry, spec)
    logo = entry.logo
    if logo.startswith("http://"):
        logo = "https://" + logo[len("http://"):]

    attrs = []
    if tvg_id:
        attrs.append(f'tvg-id="{escape_attr(tvg_id)}"')
    if logo:
        attrs.append(f'tvg-logo="{escape_attr(logo)}"')
    attrs.append(f'group-title="{escape_attr(spec["group"])}"')

    lines = [f'#EXTINF:-1 {" ".join(attrs)},{spec["name"]}']
    lines.extend(entry.directives)
    lines.append(entry.url)

    selected = {
        "name": spec["name"],
        "group": spec["group"],
        "tvg_id": tvg_id,
        "aliases": spec.get("aliases", []),
        "source_name": entry.name,
        "source_tvg_id": entry.tvg_id,
        "stream_url": entry.url,
    }
    return lines, selected


def validate_playlist(text: str) -> int:
    if text.startswith("\ufeff"):
        raise ValueError("playlist UTF-8 BOM içermemeli")
    if "\r" in text:
        raise ValueError("playlist LF satır sonu kullanmalı")

    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("ilk satır #EXTM3U olmalı")

    i = 1
    count = 0
    while i < len(lines):
        if not lines[i].startswith("#EXTINF:"):
            raise ValueError(f"EXTINF bekleniyordu: {lines[i]}")
        i += 1

        while i < len(lines) and lines[i].startswith(DIRECTIVE_PREFIXES):
            i += 1

        if i >= len(lines) or not lines[i].startswith(("http://", "https://")):
            raise ValueError("EXTINF sonrasında geçerli HTTP(S) stream URL bulunamadı")
        count += 1
        i += 1

    return count


def now_tr_iso() -> str:
    tr_tz = timezone(timedelta(hours=3))
    return datetime.now(tr_tz).replace(microsecond=0).isoformat()


def build(config: dict, source_text: str) -> tuple[str, list[dict], dict]:
    entries = parse_m3u(source_text)
    fuzzy_threshold = float(config.get("fuzzy_threshold", 0.90))
    fuzzy_margin = float(config.get("fuzzy_margin", 0.04))

    epg_url = config.get("epg_url", "").strip()
    header = "#EXTM3U"
    if epg_url:
        header += f' x-tvg-url="{escape_attr(epg_url)}"'

    output = [header]
    selected: list[dict] = []
    missing: list[dict] = []
    seen_urls: set[str] = set()

    for spec in config["channels"]:
        entry, method = select_entry(entries, spec, fuzzy_threshold, fuzzy_margin)
        if entry is None:
            missing.append({"name": spec["name"], "reason": method})
            continue

        # Never duplicate the same stream URL under two curated names.
        if entry.url in seen_urls:
            missing.append({"name": spec["name"], "reason": "duplicate_stream"})
            continue

        lines, item = render_channel(spec, entry)
        item["match"] = method
        output.extend(lines)
        selected.append(item)
        seen_urls.add(entry.url)

    playlist = "\n".join(output).rstrip() + "\n"
    count = validate_playlist(playlist)

    status = {
        "updated_at_tr": now_tr_iso(),
        "source_url": config["source_url"],
        "source_entries": len(entries),
        "selected_channels": count,
        "missing_channels": missing,
    }
    return playlist, selected, status


def main() -> int:
    parser = argparse.ArgumentParser(description="iptv-org Turkish playlist curator")
    parser.add_argument("--config", default=str(CONFIG_DEFAULT))
    parser.add_argument("--source-file", help="Test için yerel M3U kullan")
    parser.add_argument("--source-url", help="Config source_url değerini geçersiz kıl")
    parser.add_argument("--output", help="Playlist çıktı yolunu geçersiz kıl")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_url = args.source_url or config["source_url"]

    if args.source_file:
        source_text = Path(args.source_file).read_text(encoding="utf-8-sig")
    else:
        print(f"Kaynak indiriliyor: {source_url}")
        source_text = fetch_text(source_url)

    playlist, selected, status = build(config, source_text)

    playlist_path = Path(args.output or config.get("playlist_output", "playlist.m3u"))
    selected_path = Path(config.get("selected_output", "selected_channels.json"))
    status_path = Path(config.get("status_output", "status.json"))

    playlist_path.write_text(playlist, encoding="utf-8", newline="\n")
    selected_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"{playlist_path}: {status['selected_channels']} kanal")
    if status["missing_channels"]:
        print("Kaynakta bulunamayan / güvenli eşleşmeyen kanallar:")
        for item in status["missing_channels"]:
            print(f"  - {item['name']}: {item['reason']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
