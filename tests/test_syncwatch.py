"""SyncWatch: kód, hodiny a simulace skupiny bez sítě a bez Kodi."""
import unittest

from nokturno_core.lib import syncwatch as sw

REPLAY = "plugin://plugin.video.nokturno/?action=play&type=movie&id=tt0133093&url=ws%3Aabc"


class FakeTime(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakePlayer(object):
    def __init__(self, hub, name):
        self.hub, self.name = hub, name
        self.pos = None       # None = nic nehraje
        self.is_playing = False
        self.is_caching = False
        self.loads = []
        self.total = 7200.0

    def position(self):
        return self.pos

    def playing(self):
        return self.pos is not None and self.is_playing

    def caching(self):
        return self.is_caching

    def pause(self):
        self.is_playing = False

    def resume(self):
        self.is_playing = True

    def seek(self, pos):
        self.pos = pos

    def load(self, url):
        self.loads.append(url)

    def stop(self):
        self.pos, self.is_playing = None, False

    # -- simulace Kodi
    def start(self, pos=0.0, replay=REPLAY):
        """Stream doběhl do onAVStarted a hraje."""
        self.pos, self.is_playing = pos, True
        return {"replay": replay, "title": "Matrix", "total": self.total}

    def advance(self, dt):
        if self.playing() and not self.is_caching:
            self.pos += dt


class Hub(object):
    """Server v paměti: jeden stav skupiny s pořadím a záznamy členů."""

    def __init__(self):
        self.time = FakeTime()
        self.clock = sw.Clock(self.time)
        self.state = None
        self.seq = 0
        self.me = {}
        self.nodes = []

    def add(self, name, leader=False):
        player = FakePlayer(self, name)
        mid = len(self.nodes) + 1
        notes = []
        coord = sw.Coordinator(player, name, leader, self.clock,
                               publish=lambda state, mid=mid: self._put(mid, state),
                               publish_me=lambda me, mid=mid: self.me.__setitem__(mid, me),
                               notify=lambda code, **kw: notes.append(sw.notice_text(code, **kw)))
        coord.mid = mid
        coord.notes = notes
        self.nodes.append(coord)
        return coord

    def _put(self, mid, state):
        self.seq += 1
        self.state = dict(state, seq=self.seq, at=int(self.time() * 1000), by=mid)
        return self.seq

    def deliver(self):
        """Každý dostane odpověď long-pollu; dokud se něco mění, kolo se opakuje."""
        for _ in range(5):
            before = (self.seq, repr(sorted(self.me.items())))
            for node in self.nodes:
                members = [dict(self.me.get(n.mid, {}), mid=n.mid, leader=n.leader, online=True)
                           for n in self.nodes]
                node.on_poll({"ver": 1, "state": dict(self.state) if self.state else None, "members": members})
            if (self.seq, repr(sorted(self.me.items()))) == before:
                return

    def run(self, seconds, step=0.5):
        t = 0.0
        while t < seconds:
            self.time.t += step
            t += step
            for node in self.nodes:
                node.player.advance(step)
                node.tick()
            self.deliver()


def group():
    hub = Hub()
    leader = hub.add("Obývák", leader=True)
    a = hub.add("Kuchyň")
    b = hub.add("Ložnice")
    hub.deliver()
    return hub, leader, a, b


def start_title(hub, leader, followers, pos=0.0):
    leader.on_local("started", item=leader.player.start(pos))
    hub.deliver()
    for f in followers:
        assert f.player.loads, f.name
        f.on_local("started", item=f.player.start(0.0, replay=f.player.loads[-1]))
    hub.deliver()
    hub.run(1)


class TestKod(unittest.TestCase):
    def test_novy_kod_je_platny_a_ma_prefix(self):
        code = sw.new_code()
        self.assertTrue(code.startswith("SW-"))
        self.assertTrue(sw.valid_code(code))

    def test_opsany_kod_s_chybami(self):
        self.assertTrue(sw.valid_code("sw 7k2q 9mfx"))
        self.assertTrue(sw.valid_code("7K2Q9MFX"))
        self.assertEqual(sw.keys("SW-7K2Q-9MFX").ident, sw.keys("7k2q-9mfx").ident)
        self.assertFalse(sw.valid_code("SW-7K2Q"))

    def test_jina_sul_nez_prenos_nastaveni(self):
        from nokturno_core.lib import transfer
        self.assertNotEqual(sw.keys("7K2Q9MFX").ident, transfer.ident("7K2Q9MFX"))

    def test_prehrat_jde_jen_adresa_nokturna(self):
        self.assertTrue(sw.valid_replay(REPLAY))
        self.assertFalse(sw.valid_replay("plugin://plugin.video.jiny/?action=play"))
        self.assertFalse(sw.valid_replay("plugin://plugin.video.nokturno/?action=clear_cache"))
        self.assertFalse(sw.valid_replay(None))
        self.assertTrue(sw.with_sw(REPLAY).endswith("&sw=1"))


class TestHodiny(unittest.TestCase):
    def test_cil_se_posouva_jen_kdyz_se_hraje(self):
        state = {"pos": 100.0, "at": 1000 * 1000, "playing": True}
        self.assertAlmostEqual(sw.target_position(state, 1010.0), 110.0)
        state["playing"] = False
        self.assertAlmostEqual(sw.target_position(state, 1010.0), 100.0)

    def test_posun_hodin_je_median(self):
        t = FakeTime()
        clock = sw.Clock(t)
        for offset in (5.0, 5.1, 40.0, 4.9, 5.0):
            clock.sample((1000 + offset) * 1000, 1000.0, 1000.0)
        self.assertAlmostEqual(clock.offset, 5.0)


class TestSkupina(unittest.TestCase):
    def test_vedouci_ceka_na_nacteni_vsech(self):
        hub, leader, a, b = group()
        leader.on_local("started", item=leader.player.start(0.0))
        hub.deliver()
        self.assertFalse(leader.player.is_playing, "vedoucí stojí, dokud se ostatní nenačtou")
        self.assertEqual(hub.state["phase"], "loading")
        self.assertTrue(a.player.loads[-1].endswith("&sw=1"))
        a.on_local("started", item=a.player.start(0.0, replay=a.player.loads[-1]))
        hub.deliver()
        hub.run(1)
        self.assertFalse(leader.player.is_playing, "Ložnice se ještě nenačetla")
        b.on_local("started", item=b.player.start(0.0, replay=b.player.loads[-1]))
        hub.deliver()
        hub.run(1)
        self.assertEqual(hub.state["phase"], "live")
        for node in (leader, a, b):
            self.assertTrue(node.player.playing(), node.name)

    def test_pomaleho_neceka_navzdy(self):
        hub, leader, a, b = group()
        leader.on_local("started", item=leader.player.start(0.0))
        hub.deliver()
        a.on_local("started", item=a.player.start(0.0, replay=a.player.loads[-1]))
        hub.deliver()
        hub.run(sw.START_WAIT + 2)
        self.assertEqual(hub.state["phase"], "live")
        self.assertTrue(any("Ložnice" in n for n in leader.notes))

    def test_pauza_od_kohokoli_plati_pro_vsechny(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        hub.run(sw.ECHO_WINDOW + 1)
        b.player.pause()
        b.on_local("paused")
        hub.deliver()
        for node in (leader, a, b):
            self.assertFalse(node.player.playing(), node.name)
        self.assertTrue(any("Ložnice: pauza" in n for n in a.notes))
        # ozvěna: přehrávač u ostatních pošle vlastní „paused" — nesmí se vrátit jako nový příkaz
        seq = hub.seq
        a.on_local("paused")
        hub.deliver()
        self.assertEqual(hub.seq, seq)

    def test_play_po_pauze(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        hub.run(sw.ECHO_WINDOW + 1)
        a.player.pause()
        a.on_local("paused")
        hub.deliver()
        hub.run(sw.ECHO_WINDOW + 1)
        leader.player.resume()
        leader.on_local("resumed")
        hub.deliver()
        for node in (leader, a, b):
            self.assertTrue(node.player.playing(), node.name)

    def test_pretoceni_o_deset_minut(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        hub.run(sw.ECHO_WINDOW + 1)
        a.player.seek(a.player.pos + 600)
        a.on_local("seek", pos=a.player.pos)
        hub.deliver()
        hub.run(1)
        for node in (leader, b):
            self.assertAlmostEqual(node.player.pos, a.player.pos, delta=sw.TOLERANCE)

    def test_odchylka_se_srovna(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        b.player.pos -= 8   # zaseklo se mu to
        hub.run(sw.CORRECT_EVERY + 2)
        self.assertAlmostEqual(b.player.pos, leader.player.pos, delta=sw.TOLERANCE)

    def test_bufferovani_zastavi_ostatni_a_pak_pusti(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        hub.run(sw.ECHO_WINDOW + 1)
        a.player.is_caching = True
        hub.run(sw.BUFFER_AFTER + 1)
        self.assertFalse(leader.player.playing())
        self.assertFalse(b.player.playing())
        a.player.is_caching = False
        hub.run(1)
        for node in (leader, a, b):
            self.assertTrue(node.player.playing(), node.name)

    def test_pozdni_prichozi_naskoci_na_pozici(self):
        hub, leader, a, b = group()
        hub.nodes.remove(b)
        start_title(hub, leader, [a])
        hub.run(60)
        late = hub.add("Chata")
        hub.deliver()
        self.assertTrue(late.player.loads)
        late.on_local("started", item=late.player.start(0.0, replay=late.player.loads[-1]))
        hub.deliver()
        self.assertAlmostEqual(late.player.pos, leader.player.pos, delta=sw.TOLERANCE)
        self.assertTrue(late.player.playing())

    def test_clen_pusti_neco_jineho(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        hub.run(sw.ECHO_WINDOW + 1)
        a.on_local("started", item=a.player.start(0.0, replay="plugin://plugin.video.nokturno/?action=play&id=tt1"))
        hub.deliver()
        self.assertTrue(a.detached)
        leader.player.pause()
        leader.on_local("paused")
        hub.deliver()
        self.assertTrue(a.player.playing(), "vlastní film člena skupina nepauzuje")

    def test_vedouci_zastavi_konec_u_vsech(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        leader.player.stop()
        leader.on_local("stopped")
        hub.deliver()
        self.assertIsNone(a.player.pos)
        self.assertIsNone(b.player.pos)

    def test_zastaveni_stareho_po_startu_noveho_neni_konec(self):
        hub, leader, a, b = group()
        start_title(hub, leader, [a, b])
        seq = hub.seq
        leader.on_local("stopped")   # hraje se dál — jen opožděná zpráva o předchozím souboru
        hub.deliver()
        self.assertEqual(hub.seq, seq)

    def test_clen_nemuze_pustit_titul_vsem(self):
        hub, leader, a, b = group()
        a.on_local("started", item=a.player.start(0.0))
        hub.deliver()
        self.assertIsNone(hub.state)
        self.assertFalse(b.player.loads)

    def test_podvrzena_adresa_se_nepusti(self):
        hub, leader, a, b = group()
        hub._put(1, {"load": {"lid": "x", "replay": "plugin://jiny.addon/?action=play"},
                     "playing": True, "pos": 0, "phase": "live"})
        hub.deliver()
        self.assertFalse(a.player.loads)


if __name__ == "__main__":
    unittest.main()
