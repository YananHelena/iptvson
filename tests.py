#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

bp = load("build_playlist_mod", "build_playlist.py")
be = load("build_epg_mod", "build_epg_channels.py")

SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="TRT1.tr@HD" tvg-logo="http://logo/trt.png",TRT 1 (1080p)
https://example.test/trt1.m3u8
#EXTINF:-1 tvg-id="TV8.tr@SD",TV 8 (720p)
https://example.test/tv8-720.m3u8
#EXTINF:-1 tvg-id="TV8.tr@HD",TV8 (1080p)
https://example.test/tv8-1080.m3u8
#EXTINF:-1 tvg-id="TV85.tr@SD",TV 8,5 (720p)
https://example.test/tv85.m3u8
#EXTINF:-1 tvg-id="HaberturkTV.tr@SD",HABERTURK (720p)
https://example.test/haberturk.m3u8
#EXTINF:-1 tvg-id="A2TV.tr@SD",A2TV (720p)
https://example.test/a2.m3u8
#EXTINF:-1 tvg-id="TRTSporYildiz.tr@SD",TRT Spor Yıldız (720p)
https://example.test/yildiz.m3u8
"""

class PlaylistTests(unittest.TestCase):
    def test_id_suffix_and_quality_selection(self):
        entries = bp.parse_m3u(SAMPLE)
        spec = {"name":"TV8","aliases":["TV8","TV 8"],"ids":["TV8.tr"]}
        hit, method = bp.select_entry(entries, spec, 0.90, 0.04)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.url, "https://example.test/tv8-1080.m3u8")
        self.assertEqual(method, "exact")

    def test_punctuation_tolerance(self):
        entries = bp.parse_m3u(SAMPLE)
        spec = {"name":"TV8.5","aliases":["TV8.5","TV 8.5","TV8,5"],"ids":[]}
        hit, _ = bp.select_entry(entries, spec, 0.90, 0.04)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.tvg_id, "TV85.tr@SD")

    def test_short_name_is_not_fuzzy(self):
        entries = bp.parse_m3u(SAMPLE)
        spec = {"name":"24","aliases":["24"],"ids":[]}
        hit, reason = bp.select_entry(entries, spec, 0.90, 0.04)
        self.assertIsNone(hit)
        self.assertEqual(reason, "missing")

    def test_trt_spor_does_not_guess_yildiz(self):
        entries = bp.parse_m3u(SAMPLE)
        spec = {"name":"TRT Spor","aliases":["TRT Spor"],"ids":["TRTSpor.tr"]}
        hit, _ = bp.select_entry(entries, spec, 0.90, 0.04)
        self.assertIsNone(hit)

    def test_output_normalizes_tvg_id_and_logo_https(self):
        entries = bp.parse_m3u(SAMPLE)
        spec = {"name":"TRT 1","group":"Ulusal","aliases":["TRT 1"],"ids":["TRT1.tr"]}
        lines, selected = bp.render_channel(spec, entries[0])
        self.assertIn('tvg-id="TRT1.tr"', lines[0])
        self.assertIn('tvg-logo="https://logo/trt.png"', lines[0])
        self.assertEqual(selected["tvg_id"], "TRT1.tr")

class EPGTests(unittest.TestCase):
    def test_epg_id_and_name_matching(self):
        target = {"name":"TRT 1","tvg_id":"TRT1.tr","aliases":["TRT 1","TRT1"]}
        by_id = ET.fromstring('<channel site="digiturk.com.tr" site_id="19" lang="tr" xmltv_id="TRT1.tr@SD">TRT 1</channel>')
        score, reason = be.candidate_score(by_id, target)
        self.assertGreater(score, 100)
        self.assertEqual(reason, "id")

        target2 = {"name":"CNBC-e","tvg_id":"CNBCe.tr","aliases":["CNBC-e","CNBC e"]}
        by_name = ET.fromstring('<channel site="digiturk.com.tr" site_id="561" lang="tr" xmltv_id="">CNBC-e</channel>')
        score2, reason2 = be.candidate_score(by_name, target2)
        self.assertGreater(score2, 80)
        self.assertEqual(reason2, "name")

if __name__ == "__main__":
    unittest.main(verbosity=2)
