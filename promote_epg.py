#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now_tr_iso() -> str:
    return datetime.now(timezone(timedelta(hours=3))).replace(microsecond=0).isoformat()


def inspect_xml(path: Path) -> tuple[bool, int, int]:
    if not path.exists() or path.stat().st_size < 50:
        return False, 0, 0
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False, 0, 0
    if root.tag != "tv":
        return False, 0, 0
    return True, len(root.findall("channel")), len(root.findall("programme"))


def programme_channel_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return 0
    return len({
        (p.get("channel") or "").split("@", 1)[0]
        for p in root.findall("programme")
        if p.get("channel")
    })


def gzip_file(src: Path, dst: Path) -> None:
    with src.open("rb") as fin, gzip.open(dst, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote EPG only when real per-channel programme coverage is sufficient"
    )
    parser.add_argument("--candidate", default="epg.new.xml")
    parser.add_argument("--coverage", default="epg_coverage.new.json")
    parser.add_argument("--selected", default="selected_channels.json")
    parser.add_argument("--config", default="channels.json")
    parser.add_argument("--output", default="epg.xml")
    parser.add_argument("--gzip-output", default="epg.xml.gz")
    parser.add_argument("--final-coverage", default="epg_coverage.json")
    parser.add_argument("--status", default="epg_status.json")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    selected = json.loads(Path(args.selected).read_text(encoding="utf-8"))
    minimum = float(config.get("epg_min_coverage", 0.60))

    candidate = Path(args.candidate)
    coverage_path = Path(args.coverage)
    output = Path(args.output)
    gzip_output = Path(args.gzip_output)
    final_coverage = Path(args.final_coverage)

    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    ratio = float(coverage.get("coverage_ratio", 0.0))
    covered = int(coverage.get("channels_with_programmes", 0))
    programmes = int(coverage.get("total_programmes", 0))

    xml_ok, _, xml_programmes = inspect_xml(candidate)
    candidate_ok = (
        xml_ok
        and len(selected) > 0
        and ratio >= minimum
        and covered >= 10
        and programmes >= 50
        and xml_programmes >= 50
    )

    state = ""
    if candidate_ok:
        shutil.move(candidate, output)
        shutil.move(coverage_path, final_coverage)
        gzip_file(output, gzip_output)
        state = "updated"
    else:
        old_ok, _, old_programmes = inspect_xml(output)
        old_covered = programme_channel_count(output)
        old_ratio = old_covered / len(selected) if selected else 0.0

        if old_ok and old_ratio >= minimum and old_programmes >= 50:
            state = "kept_previous_good_epg"
            candidate.unlink(missing_ok=True)
            coverage_path.unlink(missing_ok=True)
            if not gzip_output.exists():
                gzip_file(output, gzip_output)
        else:
            # Do not silently publish an EPG that only contains one or two channels.
            status = {
                "updated_at_tr": now_tr_iso(),
                "state": "rejected_low_coverage",
                "candidate_coverage_ratio": ratio,
                "candidate_channels_with_programmes": covered,
                "candidate_programmes": programmes,
                "minimum_coverage_ratio": minimum,
            }
            Path(args.status).write_text(
                json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print(json.dumps(status, ensure_ascii=False))
            raise SystemExit(2)

    final_ok, final_channels, final_programmes = inspect_xml(output)
    final_covered = programme_channel_count(output)
    final_ratio = final_covered / len(selected) if selected else 0.0

    status = {
        "updated_at_tr": now_tr_iso(),
        "state": state,
        "channels": final_channels if final_ok else 0,
        "channels_with_programmes": final_covered,
        "coverage_ratio": round(final_ratio, 4),
        "programmes": final_programmes if final_ok else 0,
        "minimum_coverage_ratio": minimum,
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
