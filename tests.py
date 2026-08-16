#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bp = load("build_playlist_mod", "build_playlist.py")
bes = load("build_epg_sources_mod", "build_epg_sources.py")
me = load("merge_epg_mod", "merge_epg.py")


SAMPLE_M3U = """#EXTM3U
#EXTINF:-1 tvg-id="TRT1.tr@HD" tvg-logo="http://logo/trt.png",TRT 1 (1080p)
https://example.test/trt1.m3u8
#EXTINF:-1 tvg-id="TV8.tr@SD",TV 8 (720p)
https://example.test/tv8-low.m3u8
#EXTINF:-1 tvg-id="TV8.tr@HD",TV8 (1080p)
https://example.test/tv8-high.m3u8
"""


class PlaylistTests(unittest.TestCase):
    def test_quality_suffix_and_best_stream(self):
        entries = bp.parse_m3u(SAMPLE_M3U)
        spec = {"name": "TV8", "aliases": ["TV8", "TV 8"], "ids": ["TV8.tr"]}
        hit, method = bp.select_entry(entries, spec, 0.90, 0.04)
        self.assertEqual(method, "exact")
        self.assertEqual(hit.url, "https://example.test/tv8-high.m3u8")

    def test_dynamic_epg_url(self):
        old_repo = __import__("os").environ.get("GITHUB_REPOSITORY")
        old_ref = __import__("os").environ.get("GITHUB_REF_NAME")
        try:
            __import__("os").environ["GITHUB_REPOSITORY"] = "YananHelena/iptvson"
            __import__("os").environ["GITHUB_REF_NAME"] = "main"
            url = bp.resolve_epg_url({"epg_url": "https://wrong.example/old.xml"})
            self.assertEqual(
                url,
                "https://raw.githubusercontent.com/YananHelena/iptvson/main/epg.xml",
            )
        finally:
            if old_repo is None:
                __import__("os").environ.pop("GITHUB_REPOSITORY", None)
            else:
                __import__("os").environ["GITHUB_REPOSITORY"] = old_repo
            if old_ref is None:
                __import__("os").environ.pop("GITHUB_REF_NAME", None)
            else:
                __import__("os").environ["GITHUB_REF_NAME"] = old_ref


class EPGTests(unittest.TestCase):
    def test_source_mapper_prefers_exact_id(self):
        target = {"name": "TRT 1", "tvg_id": "TRT1.tr", "aliases": ["TRT 1"]}
        channel = ET.fromstring(
            '<channel site="dsmart.com.tr" site_id="abc" lang="tr" '
            'xmltv_id="TRT1.tr@SD">TRT 1</channel>'
        )
        score, reason = bes.score_candidate(channel, target)
        self.assertGreater(score, 120)
        self.assertIn("id", reason)

    def test_merge_falls_back_when_first_source_has_zero_programmes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            candidates = td / "epg_candidates"
            candidates.mkdir()

            # D-Smart: channel metadata exists but zero programme.
            (candidates / "dsmart.com.tr.xml").write_text(
                '<?xml version="1.0"?><tv><channel id="TRT1.tr">'
                '<display-name>TRT 1</display-name></channel></tv>',
                encoding="utf-8",
            )

            # Türksat: contains current/future data.
            (candidates / "turksatkablo.com.tr.xml").write_text(
                '<?xml version="1.0"?><tv><channel id="TRT1.tr">'
                '<display-name>TRT 1</display-name></channel>'
                '<programme start="20260817010000 +0000" stop="20260817020000 +0000" '
                'channel="TRT1.tr"><title>Program</title></programme></tv>',
                encoding="utf-8",
            )

            selected = [{
                "name": "TRT 1",
                "group": "Ulusal",
                "tvg_id": "TRT1.tr",
                "tvg_logo": "",
                "aliases": ["TRT 1"],
            }]
            (td / "selected.json").write_text(json.dumps(selected), encoding="utf-8")
            (td / "config.json").write_text(json.dumps({
                "epg_sources": [
                    "dsmart.com.tr",
                    "turksatkablo.com.tr",
                    "digiturk.com.tr",
                    "tvplus.com.tr",
                ]
            }), encoding="utf-8")

            old_argv = sys.argv
            try:
                sys.argv = [
                    "merge_epg.py",
                    "--selected", str(td / "selected.json"),
                    "--config", str(td / "config.json"),
                    "--candidate-dir", str(candidates),
                    "--output", str(td / "out.xml"),
                    "--report", str(td / "report.json"),
                    "--now", "2026-08-17T01:30:00+00:00",
                ]
                me.main()
            finally:
                sys.argv = old_argv

            report = json.loads((td / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(
                report["channels"][0]["source"],
                "turksatkablo.com.tr",
            )
            self.assertEqual(report["channels_with_programmes"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
