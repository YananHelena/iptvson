#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

DIRECTIVES = ("#EXTVLCOPT:", "#EXTHTTP:", "#KODIPROP:")
TVG_ID_RE = re.compile(r'tvg-id="([^"]+)"')


def canonical(value: str) -> str:
    return (value or "").split("@", 1)[0]


def validate_m3u(path: Path) -> tuple[int, set[str]]:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("M3U UTF-8 BOM içeriyor")
    if b"\r" in data:
        raise ValueError("M3U CR/CRLF içeriyor")

    lines = [x.strip() for x in data.decode("utf-8").splitlines() if x.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("M3U ilk satırı #EXTM3U değil")

    # Catch the exact bug that existed in the previous repository.
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository:
        expected = f"raw.githubusercontent.com/{repository}/"
        if expected not in lines[0]:
            raise ValueError(
                f"Playlist EPG URL yanlış repo gösteriyor. Beklenen parça: {expected}"
            )

    ids: set[str] = set()
    count = 0
    i = 1

    while i < len(lines):
        if not lines[i].startswith("#EXTINF:"):
            raise ValueError(f"EXTINF bekleniyordu: {lines[i]}")

        match = TVG_ID_RE.search(lines[i])
        if match:
            cid = canonical(match.group(1))
            if cid in ids:
                raise ValueError(f"Tekrarlanan tvg-id: {cid}")
            ids.add(cid)

        i += 1
        while i < len(lines) and lines[i].startswith(DIRECTIVES):
            i += 1

        if i >= len(lines) or not lines[i].startswith(("http://", "https://")):
            raise ValueError("EXTINF sonrasında stream URL yok")
        i += 1
        count += 1

    return count, ids


def validate_epg(path: Path, playlist_ids: set[str]) -> tuple[int, int, int]:
    root = ET.parse(path).getroot()
    if root.tag != "tv":
        raise ValueError("EPG kök etiketi <tv> değil")

    channel_ids = {
        canonical(ch.get("id", ""))
        for ch in root.findall("channel")
        if ch.get("id")
    }
    programme_ids = {
        canonical(p.get("channel", ""))
        for p in root.findall("programme")
        if p.get("channel")
    }

    foreign = programme_ids - playlist_ids
    if foreign:
        raise ValueError(f"EPG'de playlist dışı programme channel ID var: {sorted(foreign)}")

    missing_channel_defs = programme_ids - channel_ids
    if missing_channel_defs:
        raise ValueError(f"Programme için channel tanımı eksik: {sorted(missing_channel_defs)}")

    return len(channel_ids), len(root.findall("programme")), len(programme_ids)


def main() -> int:
    playlist_count, playlist_ids = validate_m3u(Path("playlist.m3u"))
    print(f"OK playlist.m3u: {playlist_count} kanal")

    selected = json.loads(Path("selected_channels.json").read_text(encoding="utf-8"))
    config = json.loads(Path("channels.json").read_text(encoding="utf-8"))
    minimum = float(config.get("epg_min_coverage", 0.60))

    epg_channels, programmes, programme_channels = validate_epg(
        Path("epg.xml"), playlist_ids
    )
    ratio = programme_channels / len(selected) if selected else 0.0

    if ratio < minimum:
        raise ValueError(
            f"EPG kapsamı yetersiz: {programme_channels}/{len(selected)} "
            f"({ratio:.1%}) < {minimum:.0%}"
        )
    if programmes < 50:
        raise ValueError(f"EPG program sayısı şüpheli derecede düşük: {programmes}")

    with gzip.open("epg.xml.gz", "rb") as f:
        if b"<tv" not in f.read(256):
            raise ValueError("epg.xml.gz geçerli XMLTV görünmüyor")

    for json_name in (
        "status.json",
        "epg_source_map.json",
        "epg_coverage.json",
        "epg_status.json",
    ):
        json.loads(Path(json_name).read_text(encoding="utf-8"))
        print(f"OK {json_name}")

    print(
        f"OK epg.xml: {epg_channels} channel tanımı, {programmes} program, "
        f"{programme_channels}/{len(selected)} kanal EPG'li ({ratio:.1%})"
    )
    print("OK epg.xml.gz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
