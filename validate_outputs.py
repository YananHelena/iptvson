#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DIRECTIVES = ("#EXTVLCOPT:", "#EXTHTTP:", "#KODIPROP:")


def validate_m3u(path: Path) -> int:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("M3U UTF-8 BOM içeriyor")
    if b"\r" in data:
        raise ValueError("M3U CR/CRLF içeriyor")

    text = data.decode("utf-8")
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("M3U ilk satırı #EXTM3U değil")

    count = 0
    i = 1
    ids = set()
    while i < len(lines):
        if not lines[i].startswith("#EXTINF:"):
            raise ValueError(f"EXTINF bekleniyordu: {lines[i]}")
        extinf = lines[i]
        if 'tvg-id="' in extinf:
            tvg_id = extinf.split('tvg-id="', 1)[1].split('"', 1)[0]
            if tvg_id and tvg_id in ids:
                raise ValueError(f"Tekrarlanan tvg-id: {tvg_id}")
            if tvg_id:
                ids.add(tvg_id)
        i += 1

        while i < len(lines) and lines[i].startswith(DIRECTIVES):
            i += 1

        if i >= len(lines) or not lines[i].startswith(("http://", "https://")):
            raise ValueError("EXTINF sonrasında stream URL yok")
        i += 1
        count += 1

    return count


def validate_epg_xml(path: Path) -> tuple[int, int]:
    root = ET.parse(path).getroot()
    if root.tag != "tv":
        raise ValueError("EPG kök etiketi <tv> değil")
    return len(root.findall("channel")), len(root.findall("programme"))


def validate_gzip(path: Path) -> None:
    with gzip.open(path, "rb") as f:
        head = f.read(128)
    if b"<tv" not in head and b"<?xml" not in head:
        raise ValueError("epg.xml.gz XMLTV görünmüyor")


def main() -> int:
    playlist = Path("playlist.m3u")
    count = validate_m3u(playlist)
    print(f"OK playlist.m3u: {count} kanal")

    for json_name in ("selected_channels.json", "status.json", "epg_mapping.json", "epg_status.json"):
        path = Path(json_name)
        if path.exists():
            json.loads(path.read_text(encoding="utf-8"))
            print(f"OK {json_name}")

    if Path("epg.xml").exists():
        ch, pr = validate_epg_xml(Path("epg.xml"))
        print(f"OK epg.xml: {ch} kanal, {pr} program")

    if Path("epg.xml.gz").exists():
        validate_gzip(Path("epg.xml.gz"))
        print("OK epg.xml.gz")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
