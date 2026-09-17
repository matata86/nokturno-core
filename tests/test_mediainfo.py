"""Čtení hlaviček souborů (`lib/mediainfo.py`) nad ručně sestavenými vzorky MKV, AVI a MP4 —
parser dřív neměl jediný test, přitom běží na ARM boxu nad cizími soubory. Plus párování
a řazení streamů v jádru (`Engine._merge`, `_merge_direct`, `streams.arrange`)."""
import http.server
import pathlib
import struct
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Engine                    # noqa: E402
from nokturno_core.lib import mediainfo, streams    # noqa: E402


# --- stavební kameny --------------------------------------------------------

def ebml(eid, payload):
    """Element EBML: id (s markerem, jak ho zapisuje spec) + délka (vint) + tělo."""
    id_bytes = eid.to_bytes((eid.bit_length() + 7) // 8, "big")
    n = len(payload)
    if n < 127:
        size = bytes([0x80 | n])
    else:
        size = bytes([0x40 | (n >> 8), n & 0xFF])
    return id_bytes + size + payload


def mkv_sample():
    video = ebml(0xAE, ebml(0x83, b"\x01") + ebml(0x86, b"V_MPEG4/ISO/AVC")
                 + ebml(0xE0, ebml(0xB0, (1920).to_bytes(2, "big")) + ebml(0xBA, (800).to_bytes(2, "big"))))
    audio_cz = ebml(0xAE, ebml(0x83, b"\x02") + ebml(0x86, b"A_AC3") + ebml(0x22B59C, b"cze")
                    + ebml(0xE1, ebml(0x9F, b"\x06")))
    audio_en = ebml(0xAE, ebml(0x83, b"\x02") + ebml(0x86, b"A_AAC") + ebml(0x22B59C, b"eng")
                    + ebml(0xE1, ebml(0x9F, b"\x02")))
    subs = ebml(0xAE, ebml(0x83, b"\x11") + ebml(0x86, b"S_TEXT/UTF8") + ebml(0x22B59C, b"cze"))
    info = ebml(0x1549A966, ebml(0x2AD7B1, (1_000_000).to_bytes(3, "big")) + ebml(0x4489, struct.pack(">f", 5_400_000.0)))
    segment = ebml(0x18538067, info + ebml(0x1654AE6B, video + audio_cz + audio_en + subs))
    return ebml(0x1A45DFA3, b"\x42\x86\x81\x01") + segment


def riff(tag, payload):
    return tag + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def avi_sample():
    avih = riff(b"avih", struct.pack("<I", 40_000) + b"\x00" * 12 + struct.pack("<I", 135_000) + b"\x00" * 36)
    strh_v = riff(b"strh", b"vids" + b"\x00" * 52)
    strf_v = riff(b"strf", struct.pack("<Iii", 40, 1280, -720) + b"\x00" * 28)
    strh_a = riff(b"strh", b"auds" + b"\x00" * 52)
    strf_a = riff(b"strf", struct.pack("<HH", 0x2000, 6) + b"\x00" * 14)
    hdrl = riff(b"LIST", b"hdrl" + avih + riff(b"LIST", b"strl" + strh_v + strf_v) + riff(b"LIST", b"strl" + strh_a + strf_a))
    return b"RIFF" + struct.pack("<I", 4 + len(hdrl)) + b"AVI " + hdrl


def box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4_lang(code):
    a, b, c = (ord(ch) - 0x60 for ch in code)
    return struct.pack(">H", (a << 10) | (b << 5) | c)


def mp4_sample():
    mvhd = box(b"mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 1000, 7_200_000) + b"\x00" * 80)

    def trak(handler, lang, entry):
        mdhd = box(b"mdhd", b"\x00\x00\x00\x00" + b"\x00" * 16 + mp4_lang(lang) + b"\x00\x00")
        hdlr = box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 12)
        stsd = box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry)
        return box(b"trak", box(b"mdia", mdhd + hdlr + box(b"minf", box(b"stbl", stsd))))
    avc1 = box(b"avc1", b"\x00" * 24 + struct.pack(">HH", 3840, 1600) + b"\x00" * 50)
    mp4a = box(b"mp4a", b"\x00" * 16 + struct.pack(">H", 2) + b"\x00" * 10)
    ac3 = box(b"ac-3", b"\x00" * 16 + struct.pack(">H", 2) + b"\x00" * 10 + box(b"dac3", bytes([0x10, 0x3D, 0x40])))
    moov = box(b"moov", mvhd + trak(b"vide", "und", avc1) + trak(b"soun", "cze", ac3) + trak(b"soun", "eng", mp4a))
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + moov


class TestParsery(unittest.TestCase):
    def test_mkv(self):
        tracks, duration = mediainfo._from_mkv(mkv_sample())
        self.assertEqual(duration, 5400.0, "Duration × TimecodeScale / 1e9")
        self.assertEqual([t.get("type") for t in tracks], [1, 2, 2, 17])
        self.assertEqual((tracks[0]["width"], tracks[0]["height"], tracks[0]["codec"]), (1920, 800, "V_MPEG4/ISO/AVC"))
        self.assertEqual((tracks[1]["lang"], tracks[1]["channels"], tracks[1]["codec"]), ("cze", 6, "A_AC3"))
        self.assertEqual(tracks[3]["lang"], "cze")

    def test_avi(self):
        tracks, duration = mediainfo._from_avi(avi_sample())
        self.assertEqual(duration, 5400.0, "micro_per_frame × total_frames")
        self.assertEqual(tracks, [{"type": 1, "width": 1280, "height": 720}, {"type": 2, "channels": 6}])

    def test_mp4(self):
        tracks, duration = mediainfo._from_mp4(mp4_sample())
        self.assertEqual(duration, 7200.0)
        self.assertEqual([t["type"] for t in tracks], [1, 2, 2])
        self.assertEqual((tracks[0]["width"], tracks[0]["height"], tracks[0]["codec"]), (3840, 1600, "avc1"))
        self.assertEqual((tracks[1]["lang"], tracks[1]["channels"]), ("cze", 6), "AC-3: kanály z dac3 (acmod 7 + LFE), ne z hlavičky (2)")
        self.assertEqual((tracks[2]["lang"], tracks[2]["channels"], tracks[2]["codec"]), ("eng", 2, "mp4a"))

    def test_probe_pres_http_s_range(self):
        data = mkv_sample()

        class Zdroj(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range", "bytes=0-")
                start, _, end = rng[6:].partition("-")
                start = int(start or 0)
                end = min(int(end) if end else len(data) - 1, len(data) - 1)
                kus = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(len(kus)))
                self.end_headers()
                self.wfile.write(kus)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Zdroj)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            info = mediainfo.probe(f"http://127.0.0.1:{srv.server_address[1]}/film.mkv")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(info["size"], len(data))
        self.assertEqual((info["width"], info["height"], info["duration"]), (1920, 800, 5400.0))
        self.assertEqual([(a["lang"], a["channels"], a["codec"]) for a in info["audio"]],
                         [("CZ", "5.1", "AC-3"), ("EN", "2.0", "AAC")])
        self.assertEqual(info["subs"], ["CZ"])
        self.assertEqual(mediainfo.quality_from_size(info["width"], info["height"]), "Full HD",
                         "1920×800 je širokoúhlé Full HD, podle výšky by vyšlo HD")
        text = mediainfo.describe(info)
        self.assertIn("CZ 5.1", text)

    def test_rozbita_hlavicka_nevyhodi(self):
        self.assertEqual(mediainfo._from_mkv(b"\x1a\x45\xdf\xa3" + b"\xff" * 20)[0], [])
        self.assertEqual(mediainfo._from_avi(b"RIFF\x00\x00\x00\x00AVI " + b"\xff" * 5)[0], [])
        self.assertEqual(mediainfo._from_mp4(b"\x00\x00\x00\x08ftyp" + b"\x00" * 3)[0], [])


class TestParovaniStreamu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = Engine({}, self.tmp)

    def test_merge_luna_se_sosacem(self):
        luna = [{"id": "tt1", "name": "Matrix", "year": 1999}, {"id": "tt2", "name": "Jiný film", "year": 2000}]
        sosac = [{"id": "sosacd_m_1", "_title": "Matrix", "_orig": "The Matrix", "year": 1999},
                 {"id": "sosacd_m_2", "_title": "Matrix", "year": 1999},   # duplicita v Sosáči
                 {"id": "sosacd_m_3", "_title": "Matrix", "year": 2020},   # jiný rok = jiný titul
                 {"id": "sosacd_m_4", "_title": "Jen v Sosáči", "year": 2001}]
        merged = self.engine._merge(luna, sosac)
        self.assertEqual([(m["id"], alt) for m, alt in merged],
                         [("tt1", "sosacd_m_1"), ("tt2", None), ("sosacd_m_3", None), ("sosacd_m_4", None)],
                         "druhá kopie téhož titulu v Sosáči se nezobrazí jako další dlaždice")

    def test_merge_direct_paruje_podle_velikosti(self):
        luna = {"label": "(WS) Full HD", "detail": "4.2 GB | Zvuk: CZ 5.1", "url": "luna:1", "source": "main"}
        ws = {"label": "Matrix.1999.1080p.CZ.mkv", "detail": "4.3 GB", "url": "ws:1", "source": "ws", "_direct": True}
        daleko = {"label": "Matrix.1999.1080p.EN.mkv", "detail": "6.0 GB", "url": "ws:2", "source": "ws", "_direct": True}
        jina_kvalita = {"label": "Matrix.1999.720p.mkv", "detail": "4.5 GB", "url": "ws:3", "source": "ws", "_direct": True}
        for s in (luna, ws, daleko, jina_kvalita):
            streams.parse_stream(s)
        out = self.engine._merge_direct([luna, ws, daleko, jina_kvalita])
        self.assertEqual([s["url"] for s in out], ["luna:1", "ws:2", "ws:3"])
        self.assertEqual(out[0]["_ws_url"], "ws:1", "tentýž soubor (±0,25 GB, stejná kvalita) se přibalí k Luně")
        self.assertEqual(out[0]["_ws_name"], "Matrix.1999.1080p.CZ.mkv")


class TestParseStreamBezHlavicky(unittest.TestCase):
    """`probe_audio=False` (hromadná klasifikace, viz `_build_lang_catalog()` v Kodi) nečte
    hlavičky souborů — WebShare/HellSpy/Sledujteto/FastShare tak v `detail` nemají „Zvuk:“
    ani „Tit.“ a jazyk se dá poznat jen z názvu souboru."""

    def test_websharovy_stream_bez_zvuk_pozna_jazyk_z_nazvu(self):
        s = {"label": "Matrix.1999.2160p.CZ.dabing.mkv", "detail": "12 GB", "source": "ws", "_direct": True}
        streams.parse_stream(s)
        self.assertEqual(s["langs"], {"CZ"})
        self.assertTrue(s.get("_langs_from_name"), "jazyk je jen odhad z názvu, ne ověřený „Zvuk:“")

    def test_krizovy_stream_bez_zvuk_bere_nazev_z_ws_name(self):
        s = {"label": "Luna popisek", "detail": "4.0 GB", "source": "main", "_ws_name": "Matrix.1999.CZtit.mkv"}
        streams.parse_stream(s)
        self.assertEqual(s["subs"], {"CZ"})

    def test_zdroj_bez_jazyka_v_nazvu_zustane_prazdny(self):
        s = {"label": "Matrix.1999.2160p.mkv", "detail": "14 GB", "source": "ws", "_direct": True}
        streams.parse_stream(s)
        self.assertEqual(s["langs"], set())
        self.assertNotIn("_langs_from_name", s)

    def test_overeny_zvuk_z_hlavicky_ma_prednost_pred_nazvem(self):
        s = {"label": "Matrix.1999.CZ.mkv", "detail": "13 GB | Zvuk: EN 5.1", "source": "ws", "_direct": True}
        streams.parse_stream(s)
        self.assertEqual(s["langs"], {"EN"}, "hlavička řekla EN, název jen hádá CZ — hlavička vyhrává")
        self.assertNotIn("_langs_from_name", s)


class TestRazeni(unittest.TestCase):
    def s(self, label, detail, url):
        return {"label": label, "detail": detail, "url": url}

    def test_jazyk_pak_kvalita(self):
        rows = [self.s("720p", "1.0 GB | Zvuk: CZ 2.0", "hd-cz"), self.s("1080p", "4.0 GB | Zvuk: EN 5.1", "fhd-en"),
                self.s("1080p", "4.0 GB | Zvuk: CZ 5.1", "fhd-cz"), self.s("2160p", "12 GB", "uhd-odhad")]
        out = streams.arrange(rows, pref_lang="CZ", order="quality")
        self.assertEqual([r["url"] for r in out], ["fhd-cz", "hd-cz", "uhd-odhad", "fhd-en"],
                         "preferovaný jazyk je hlavní klíč, uvnitř skupin rozhoduje kvalita")
        out = streams.arrange(rows, order="quality")
        self.assertEqual([r["url"] for r in out][:1], ["uhd-odhad"], "bez preferovaného jazyka dál kvalita")

    def test_jazyk_z_nazvu_mezi_overenym_a_ostatnimi(self):
        overeny = self.s("720p", "1.0 GB | Zvuk: CZ 2.0", "overeny")
        z_nazvu = self.s("Matrix.1999.2160p.CZ.dabing.mkv", "12 GB", "z-nazvu")
        cizi = self.s("Matrix.1999.2160p.mkv", "14 GB", "cizi")
        vyvraceny = self.s("Matrix.1999.2160p.CZ.mkv", "13 GB | Zvuk: EN 5.1", "vyvraceny")
        vyvraceny["_tracks"] = [{"lang": "EN", "channels": 6}]
        out = streams.arrange([cizi, vyvraceny, z_nazvu, overeny], pref_lang="CZ", order="size_desc")
        self.assertEqual([r["url"] for r in out], ["overeny", "z-nazvu", "cizi", "vyvraceny"],
                         "název souboru nepřebije hlavičku, která jazyk vyvrátila")

    def test_filtry_a_pad_na_puvodni_seznam(self):
        rows = [self.s("SD", "0.7 GB", "sd"), self.s("1080p", "9.0 GB", "velky"), self.s("1080p", "3.0 GB", "maly")]
        out = streams.arrange(rows, hide_sd=True, max_size_gb=5.0, order="size_desc")
        self.assertEqual([r["url"] for r in out], ["maly"])
        out = streams.arrange(rows, hide_sd=True, max_size_gb=0.5, order="size_asc")
        self.assertEqual([r["url"] for r in out], ["sd", "maly", "velky"], "když by filtr nic nenechal, vrátí vše")

    def test_prostorovy_zvuk_a_overene_napred(self):
        rows = [self.s("1080p", "4.0 GB", "odhad"), self.s("1080p", "4.0 GB | Zvuk: CZ 2.0", "stereo"),
                self.s("1080p", "4.0 GB | Zvuk: CZ 5.1", "surround")]
        out = streams.arrange(rows, pref_lang="CZ", pref_surround=True, order="source")
        self.assertEqual([r["url"] for r in out], ["surround", "stereo", "odhad"])


if __name__ == "__main__":
    unittest.main()
