#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


XMLTV_TIME_RE = re.compile(r"^(\d{14})(?:\s+([+-]\d{4}))?")


def parse_xmltv_time(value: str) -> datetime | None:
    match = XMLTV_TIME_RE.match((value or "").strip())
    if not match:
        return None

    base = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    offset = match.group(2)

    if offset:
        sign = 1 if offset[0] == "+" else -1
        hours = int(offset[1:3])
        minutes = int(offset[3:5])
        tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    else:
        tz = timezone.utc

    return base.replace(tzinfo=tz).astimezone(timezone.utc)


def canonical_id(value: str) -> str:
    return (value or "").strip().split("@", 1)[0]


def load_candidate(path: Path) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]:
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = defaultdict(list)

    if not path.exists() or path.stat().st_size < 40:
        return channels, programmes

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return channels, programmes

    if root.tag != "tv":
        return channels, programmes

    for channel in root.findall("channel"):
        cid = canonical_id(channel.get("id", ""))
        if cid:
            channels[cid] = channel

    for programme in root.findall("programme"):
        cid = canonical_id(programme.get("channel", ""))
        if cid:
            programmes[cid].append(programme)

    return channels, programmes


def relevant_program_count(programmes: list[ET.Element], now_utc: datetime) -> int:
    """Count programmes that are not stale.

    A source only qualifies when it contains at least one programme ending in
    the recent/future window. This prevents an old or 0-program source from
    winning merely because its channel metadata exists.
    """
    lower = now_utc - timedelta(hours=8)
    upper = now_utc + timedelta(days=5)
    count = 0

    for programme in programmes:
        start = parse_xmltv_time(programme.get("start", ""))
        stop = parse_xmltv_time(programme.get("stop", ""))

        if stop is None and start is None:
            continue
        effective_end = stop or start
        effective_start = start or stop

        if effective_end >= lower and effective_start <= upper:
            count += 1

    return count


def make_channel_element(target: dict) -> ET.Element:
    cid = canonical_id(target.get("tvg_id", ""))
    channel = ET.Element("channel", {"id": cid})
    display = ET.SubElement(channel, "display-name")
    display.text = target["name"]

    logo = target.get("tvg_logo", "")
    if logo:
        ET.SubElement(channel, "icon", {"src": logo})

    return channel


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge multiple iptv-org EPG candidates using actual programme coverage"
    )
    parser.add_argument("--selected", default="selected_channels.json")
    parser.add_argument("--config", default="channels.json")
    parser.add_argument("--candidate-dir", default="epg_candidates")
    parser.add_argument("--output", default="epg.new.xml")
    parser.add_argument("--report", default="epg_coverage.new.json")
    parser.add_argument("--now", help="UTC ISO time for tests, e.g. 2026-08-17T00:00:00+00:00")
    args = parser.parse_args()

    selected = json.loads(Path(args.selected).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    candidate_dir = Path(args.candidate_dir)
    priority = config.get("epg_sources", [])

    if args.now:
        now_utc = datetime.fromisoformat(args.now).astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)

    source_data = {}
    for site in priority:
        source_data[site] = load_candidate(candidate_dir / f"{site}.xml")

    root = ET.Element(
        "tv",
        {
            "generator-info-name": "YananHelena/iptvson + iptv-org/epg",
            "date": now_utc.strftime("%Y%m%d"),
        },
    )

    report = {
        "generated_at_utc": now_utc.replace(microsecond=0).isoformat(),
        "selected_channels": len(selected),
        "channels_with_programmes": 0,
        "coverage_ratio": 0.0,
        "total_programmes": 0,
        "sources_used": {},
        "channels": [],
    }

    chosen_programmes: list[ET.Element] = []

    for target in selected:
        cid = canonical_id(target.get("tvg_id", ""))
        if not cid:
            continue

        attempts = []
        chosen_site = None
        chosen = []

        for site in priority:
            _, programmes_by_id = source_data.get(site, ({}, {}))
            programmes = programmes_by_id.get(cid, [])
            current_count = relevant_program_count(programmes, now_utc)

            attempts.append({
                "source": site,
                "programmes": len(programmes),
                "current_or_future_programmes": current_count,
            })

            if current_count > 0:
                chosen_site = site
                chosen = programmes
                break

        root.append(make_channel_element(target))

        if chosen_site:
            for programme in chosen:
                cloned = copy.deepcopy(programme)
                cloned.set("channel", cid)
                chosen_programmes.append(cloned)

            report["channels_with_programmes"] += 1
            report["total_programmes"] += len(chosen)
            report["sources_used"][chosen_site] = report["sources_used"].get(chosen_site, 0) + 1

        report["channels"].append({
            "name": target["name"],
            "tvg_id": cid,
            "source": chosen_site,
            "programme_count": len(chosen),
            "attempts": attempts,
        })

    # Channel elements first, programmes afterwards is XMLTV-friendly.
    for programme in chosen_programmes:
        root.append(programme)

    if selected:
        report["coverage_ratio"] = round(
            report["channels_with_programmes"] / len(selected), 4
        )

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(args.output, encoding="utf-8", xml_declaration=True)

    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        f"EPG coverage: {report['channels_with_programmes']}/{len(selected)} "
        f"({report['coverage_ratio']:.1%}), {report['total_programmes']} program"
    )
    print("Kaynak kullanımı:", json.dumps(report["sources_used"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
