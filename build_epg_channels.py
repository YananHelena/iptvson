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

PRIORITY = [
    "digiturk.com.tr",
    "dsmart.com.tr",
    "turksatkablo.com.tr",
    "tvplus.com.tr",
]


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


def site_priority(site: str) -> int:
    try:
        return 40 - PRIORITY.index(site) * 5
    except ValueError:
        return 0


def candidate_score(channel: ET.Element, target: dict) -> tuple[float, str]:
    site = channel.get("site", "")
    xmltv_id = canonical_id(channel.get("xmltv_id", ""))
    target_id = canonical_id(target.get("tvg_id", ""))
    text = (channel.text or "").strip()

    score = float(site_priority(site))
    reason = ""

    if target_id and xmltv_id and xmltv_id.casefold() == target_id.casefold():
        return score + 120.0, "id"

    aliases = list(target.get("aliases", [])) + [target.get("name", "")]
    text_norm = normalize(text)
    text_compact = compact(text)

    for alias in aliases:
        if text_norm and text_norm == normalize(alias):
            return score + 90.0, "name"
        if text_compact and text_compact == compact(alias):
            return score + 85.0, "compact_name"

    # Conservative fuzzy fallback for EPG source display names.
    if len(text_compact) >= 5:
        best = 0.0
        for alias in aliases:
            alias_compact = compact(alias)
            if len(alias_compact) < 5:
                continue
            best = max(best, difflib.SequenceMatcher(None, alias_compact, text_compact).ratio())
        if best >= 0.94:
            return score + best * 60.0, f"fuzzy:{best:.3f}"

    return -1.0, ""


def load_candidates(epg_root: Path) -> list[ET.Element]:
    sites_root = epg_root / "sites"
    files: list[Path] = []

    # Priority sources first, then all remaining iptv-org EPG channel definitions.
    for site in PRIORITY:
        files.extend(sorted((sites_root / site).glob("*.channels.xml")))

    priority_set = {p.resolve() for p in files}
    for path in sorted(sites_root.glob("**/*.channels.xml")):
        if path.resolve() not in priority_set:
            files.append(path)

    candidates: list[ET.Element] = []
    for path in files:
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue
        for channel in tree.getroot().findall("channel"):
            # Keep source file/site metadata as supplied by iptv-org.
            candidates.append(channel)

    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a custom iptv-org EPG channel list")
    parser.add_argument("--selected", default="selected_channels.json")
    parser.add_argument("--epg-root", required=True)
    parser.add_argument("--output", default="epg_channels.xml")
    parser.add_argument("--report", default="epg_mapping.json")
    args = parser.parse_args()

    selected = json.loads(Path(args.selected).read_text(encoding="utf-8"))
    epg_root = Path(args.epg_root)
    candidates = load_candidates(epg_root)

    root = ET.Element("channels")
    report = {"mapped": [], "unmapped": []}
    used_source_keys: set[tuple[str, str]] = set()

    for target in selected:
        ranked: list[tuple[float, str, ET.Element]] = []
        for channel in candidates:
            score, reason = candidate_score(channel, target)
            if score >= 0:
                ranked.append((score, reason, channel))

        ranked.sort(key=lambda x: x[0], reverse=True)
        chosen = None
        chosen_reason = ""
        chosen_score = 0.0

        for score, reason, channel in ranked:
            key = (channel.get("site", ""), channel.get("site_id", ""))
            if key in used_source_keys:
                continue
            chosen = channel
            chosen_reason = reason
            chosen_score = score
            used_source_keys.add(key)
            break

        if chosen is None:
            report["unmapped"].append({
                "name": target["name"],
                "tvg_id": target.get("tvg_id", ""),
            })
            continue

        cloned = copy.deepcopy(chosen)
        # Force EPG output ID to exactly match our playlist TVG ID.
        cloned.set("xmltv_id", canonical_id(target.get("tvg_id", "")))
        root.append(cloned)

        report["mapped"].append({
            "name": target["name"],
            "tvg_id": canonical_id(target.get("tvg_id", "")),
            "site": chosen.get("site", ""),
            "site_id": chosen.get("site_id", ""),
            "source_name": (chosen.text or "").strip(),
            "reason": chosen_reason,
            "score": round(chosen_score, 3),
        })

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(args.output, encoding="utf-8", xml_declaration=True)

    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"EPG eşleşen: {len(report['mapped'])}")
    print(f"EPG eşleşmeyen: {len(report['unmapped'])}")
    for item in report["unmapped"]:
        print(f"  - {item['name']} ({item['tvg_id']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
