#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


def inspect_xml(path: Path) -> tuple[bool, int, int, str]:
    if not path.exists() or path.stat().st_size < 50:
        return False, 0, 0, "missing_or_too_small"
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return False, 0, 0, f"xml_error:{type(exc).__name__}"

    if root.tag != "tv":
        return False, 0, 0, "root_is_not_tv"

    channels = len(root.findall("channel"))
    programmes = len(root.findall("programme"))
    if channels < 1 or programmes < 1:
        return False, channels, programmes, "no_epg_data"

    return True, channels, programmes, "ok"


def now_tr_iso() -> str:
    return datetime.now(timezone(timedelta(hours=3))).replace(microsecond=0).isoformat()


def write_empty_epg(path: Path) -> None:
    text = '<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="turkiye-iptv-final"></tv>\n'
    path.write_text(text, encoding="utf-8", newline="\n")


def gzip_file(src: Path, dst: Path) -> None:
    with src.open("rb") as fin, gzip.open(dst, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a valid EPG candidate, otherwise keep previous")
    parser.add_argument("--candidate", default="epg.new.xml")
    parser.add_argument("--output", default="epg.xml")
    parser.add_argument("--gzip-output", default="epg.xml.gz")
    parser.add_argument("--status", default="epg_status.json")
    args = parser.parse_args()

    candidate = Path(args.candidate)
    output = Path(args.output)
    gzip_output = Path(args.gzip_output)

    valid, channels, programmes, reason = inspect_xml(candidate)
    state = ""

    if valid:
        candidate.replace(output)
        gzip_file(output, gzip_output)
        state = "updated"
    else:
        old_valid, old_channels, old_programmes, old_reason = inspect_xml(output)
        if old_valid:
            channels, programmes = old_channels, old_programmes
            state = f"kept_previous:{reason}"
            if not gzip_output.exists():
                gzip_file(output, gzip_output)
        else:
            # First-run safety: create valid XMLTV-shaped files even if upstream
            # EPG sites temporarily fail. A later successful run replaces them.
            write_empty_epg(output)
            gzip_file(output, gzip_output)
            channels, programmes = 0, 0
            state = f"empty_fallback:{reason}"

    if candidate.exists():
        candidate.unlink()

    status = {
        "updated_at_tr": now_tr_iso(),
        "state": state,
        "channels": channels,
        "programmes": programmes,
    }
    Path(args.status).write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
