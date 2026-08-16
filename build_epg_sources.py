#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("ı", "i")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def compact(value: str) -> str:
    return normalize(value).replace(" ", "")


def canonical_id(value: str) -> str:
    return (value or "").strip().split("@", 1)[0]


def load_site_channels(epg_root: Path, site: str) -> list[ET.Element]:
    site_dir = epg_root / "sites" / site
    channels: list[ET.Element] = []

    for path in sorted(site_dir.glob("*.channels.xml")):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        channels.extend(root.findall("channel"))

    return channels


def score_candidate(channel: ET.Element, target: dict) -> tuple[float, str]:
    source_id = canonical_id(channel.get("xmltv_id", ""))
    target_id = canonical_id(target.get("tvg_id", ""))
    source_name = (channel.text or "").strip()

    score = 0.0
    reasons: list[str] = []

    if source_id and target_id and source_id.casefold() == target_id.casefold():
        score += 120.0
        reasons.append("id")

    aliases = list(target.get("aliases", [])) + [target.get("name", "")]
    src_norm = normalize(source_name)
    src_compact = compact(source_name)

    exact_name = False
    for alias in aliases:
        if src_norm and src_norm == normalize(alias):
            score += 45.0
            reasons.append("name")
            exact_name = True
            break
        if src_compact and src_compact == compact(alias):
            score += 40.0
            reasons.append("compact_name")
            exact_name = True
            break

    if not exact_name and len(src_compact) >= 5:
        best = 0.0
        for alias in aliases:
            alias_compact = compact(alias)
            if len(alias_compact) < 5:
                continue
            best = max(best, difflib.SequenceMatcher(None, alias_compact, src_compact).ratio())
        if best >= 0.94:
            score += best * 25.0
            reasons.append(f"fuzzy:{best:.3f}")

    return score, "+".join(reasons)


def choose_candidate(channels: list[ET.Element], target: dict) -> tuple[ET.Element | None, float, str]:
    ranked: list[tuple[float, str, ET.Element]] = []
    for channel in channels:
        score, reason = score_candidate(channel, target)
        if score > 0:
            ranked.append((score, reason, channel))

    if not ranked:
        return None, 0.0, ""

    ranked.sort(key=lambda x: x[0], reverse=True)
    score, reason, channel = ranked[0]

    # Do not accept a weak fuzzy-only guess.
    if score < 23.5:
        return None, score, reason

    return channel, score, reason


def write_xml(path: Path, root: ET.Element) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build one custom iptv-org *.channels.xml file per EPG source"
    )
    parser.add_argument("--selected", default="selected_channels.json")
    parser.add_argument("--config", default="channels.json")
    parser.add_argument("--epg-root", required=True)
    parser.add_argument("--output-dir", default="epg_sources")
    parser.add_argument("--report", default="epg_source_map.json")
    args = parser.parse_args()

    selected = json.loads(Path(args.selected).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    epg_root = Path(args.epg_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale source lists so deleted sources cannot be grabbed accidentally.
    for old in output_dir.glob("*.channels.xml"):
        old.unlink()

    report = {"sources": {}, "channels": {}}

    for site in config.get("epg_sources", []):
        source_channels = load_site_channels(epg_root, site)
        root = ET.Element("channels")
        used_site_ids: set[str] = set()
        mapped_count = 0

        report["sources"][site] = {
            "available_definitions": len(source_channels),
            "mapped_channels": 0,
        }

        for target in selected:
            tvg_id = canonical_id(target.get("tvg_id", ""))
            if not tvg_id:
                continue

            chosen, score, reason = choose_candidate(source_channels, target)
            if chosen is None:
                continue

            site_id = chosen.get("site_id", "")
            if not site_id or site_id in used_site_ids:
                continue

            cloned = copy.deepcopy(chosen)
            # Critical: candidate output must use exactly the same ID as playlist.m3u.
            cloned.set("xmltv_id", tvg_id)
            root.append(cloned)
            used_site_ids.add(site_id)
            mapped_count += 1

            report["channels"].setdefault(tvg_id, {
                "name": target["name"],
                "sources": {},
            })
            report["channels"][tvg_id]["sources"][site] = {
                "site_id": site_id,
                "source_name": (chosen.text or "").strip(),
                "source_xmltv_id": chosen.get("xmltv_id", ""),
                "match_reason": reason,
                "match_score": round(score, 3),
            }

        report["sources"][site]["mapped_channels"] = mapped_count

        if mapped_count:
            write_xml(output_dir / f"{site}.channels.xml", root)

        print(f"{site}: {mapped_count} kanal eşleşti")

    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
