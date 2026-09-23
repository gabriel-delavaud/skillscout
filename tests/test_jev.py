import io, json, sys, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import jev, net

KEY = "ts-secret-0123456789"
ANSWERS = {"exfiltration": {"noul": 0.01}}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def reply(status, payload):
    return lambda *a, **k: (status, payload)


class TestPostJson(unittest.TestCase):
    def _cm(self, raw: bytes, status=200):
        cm = MagicMock()
        cm.read.return_value = raw
        cm.status = status
        cm.__enter__ = lambda s: cm
        cm.__exit__ = lambda s, *a: False
        return cm

    def test_post_et_decode(self):
        captured = {}

        def fake(req, timeout=None):
            captured["req"], captured["timeout"] = req, timeout
            return self._cm(b'{"answers": {"a": 1}}')
        with patch("skillscout.net.urlopen", side_effect=fake):
            out = net.post_json("https://x/y", {"q": 1}, {"Authorization": "Bearer k"}, 7.0)
        self.assertEqual(out, (200, {"answers": {"a": 1}}))
        req = captured["req"]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data.decode()), {"q": 1})
        self.assertEqual(req.get_header("Authorization"), "Bearer k")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(captured["timeout"], 7.0)

    def test_erreur_http_rend_le_statut(self):
        err = HTTPError("https://x", 401, "Unauthorized", {}, io.BytesIO(b"no"))
        with patch("skillscout.net.urlopen", side_effect=err):
            self.assertEqual(net.post_json("https://x", {}, {}, 1.0), (401, None))

    def test_json_illisible_rend_none(self):
        with patch("skillscout.net.urlopen", return_value=self._cm(b"<html>")):
            self.assertEqual(net.post_json("https://x", {}, {}, 1.0), (200, None))

    def test_injoignable_leve_oserror(self):
        with patch("skillscout.net.urlopen", side_effect=URLError("dns")):
            with self.assertRaises(OSError):
                net.post_json("https://x", {}, {}, 1.0)


class TestJevClient(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.client = jev.JevClient(KEY, now=self.clock)

    def test_succes_renvoie_les_reponses_et_envoie_le_bon_corps(self):
        with patch("skillscout.net.post_json", return_value=(200, {"answers": ANSWERS})) as p:
            out = self.client.classify({"skill_md": "x"}, {"q": {"type": "noul"}})
        self.assertEqual(out, ANSWERS)
        url, body, headers, timeout = p.call_args.args
        self.assertEqual(url, jev.ENDPOINT)
        self.assertEqual(body, {"model": jev.MODEL, "state": {"skill_md": "x"},
                                "questions": {"q": {"type": "noul"}}})
        self.assertEqual(headers, {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(timeout, jev.TIMEOUT)
        self.assertEqual(self.client.calls, 1)

    def test_cle_refusee_coupe_le_client(self):
        for code in (401, 403):
            client = jev.JevClient(KEY, now=self.clock)
            with patch("skillscout.net.post_json", return_value=(code, None)) as p:
                self.assertIsNone(client.classify({}, {}))
                self.assertIsNone(client.classify({}, {}))
            self.assertEqual(p.call_count, 1)          # pas de second appel
            self.assertTrue(client.key_rejected)
            self.assertFalse(client.available)
            self.assertIn("refusée", client.last_error)

    def test_une_relance_apres_panne_reseau(self):
        with patch("skillscout.net.post_json",
                   side_effect=[TimeoutError("lent"), (200, {"answers": ANSWERS})]) as p:
            self.assertEqual(self.client.classify({}, {}), ANSWERS)
        self.assertEqual(p.call_count, 2)

    def test_deux_pannes_rendent_none(self):
        with patch("skillscout.net.post_json", side_effect=TimeoutError("lent")) as p:
            self.assertIsNone(self.client.classify({}, {}))
        self.assertEqual(p.call_count, 2)
        self.assertEqual(self.client.last_error, "Jev injoignable (TimeoutError)")

    def test_http_500_et_reponse_illisible(self):
        with patch("skillscout.net.post_json", return_value=(500, None)):
            self.assertIsNone(self.client.classify({}, {}))
        self.assertEqual(self.client.last_error, "HTTP 500")
        for payload in (None, [], {"foo": 1}, {"answers": "x"}):
            client = jev.JevClient(KEY, now=self.clock)   # disjoncteur neuf à chaque cas
            with patch("skillscout.net.post_json", return_value=(200, payload)) as p:
                self.assertIsNone(client.classify({}, {}))
            self.assertEqual(p.call_count, 2)
            self.assertEqual(client.last_error, "réponse Jev illisible")

    def test_disjoncteur_s_ouvre_puis_se_referme(self):
        with patch("skillscout.net.post_json", return_value=(500, None)):
            for _ in range(jev.BREAKER_THRESHOLD):
                self.client.classify({}, {})
        self.assertFalse(self.client.available)
        with patch("skillscout.net.post_json") as p:
            self.assertIsNone(self.client.classify({}, {}))
        p.assert_not_called()
        self.clock.t += jev.BREAKER_COOLDOWN_S + 1
        self.assertTrue(self.client.available)

    def test_un_succes_remet_le_compteur_a_zero(self):
        fail = (500, None)
        ok = (200, {"answers": ANSWERS})
        seq = [fail, fail, fail, fail, ok] + [fail] * 4  # 2 échecs, 1 succès, 2 échecs
        with patch("skillscout.net.post_json", side_effect=seq):
            for _ in range(5):
                self.client.classify({}, {})
        self.assertTrue(self.client.available)

    def test_la_cle_n_apparait_dans_aucune_erreur(self):
        scenarios = [TimeoutError(KEY), OSError(KEY), (500, None), (401, None)]
        for s in scenarios:
            client = jev.JevClient(KEY, now=self.clock)
            kw = {"side_effect": s} if isinstance(s, Exception) else {"return_value": s}
            with patch("skillscout.net.post_json", **kw):
                client.classify({}, {})
            self.assertNotIn(KEY, client.last_error)
            self.assertNotIn(KEY, repr(client))

    def test_from_env(self):
        self.assertIsNone(jev.JevClient.from_env({}))
        self.assertIsNone(jev.JevClient.from_env({jev.ENV_KEY: "  "}))
        self.assertIsInstance(jev.JevClient.from_env({jev.ENV_KEY: KEY}), jev.JevClient)


if __name__ == "__main__":
    unittest.main()
