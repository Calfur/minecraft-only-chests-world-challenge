"""Opt-in tests against the official jar; no server, sockets, or worlds required."""
import os
from pathlib import Path
import tempfile
import unittest

from challenge.prefilter import SeedPrefilter
from challenge.server import find_java


@unittest.skipUnless(os.environ.get("MINECRAFT_PREFILTER_TESTS") == "1",
                     "Set MINECRAFT_PREFILTER_TESTS=1 to test against the official 26.3 jar")
class VanillaPrefilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        jar = Path(os.environ.get("MINECRAFT_SERVER_JAR", ".cache/server-26.3.jar"))
        cls.helper = SeedPrefilter(jar, find_java(), Path(cls.folder.name))
        cls.addClassCleanup(cls.helper.close)

    def test_known_supply_markers_and_spawn_bounds(self):
        # Real inspected worlds, including the user's reproduced chest and the
        # biome-boundary regression. Heights/loot stay with the verifier.
        cases = (
            (-7050104159075989928, (-368, -16), (-380, -58)),
            (-7050104159075984227, (-112, 160), (-92, 134)),
        )
        for seed, spawn, chest in cases:
            with self.subTest(seed=seed):
                result = self.helper.check(seed, 96)
                self.assertIn(list(chest), result["supply_positions"])
                x0, z0, x1, z1 = result["spawn_bounds"]
                self.assertTrue(x0 <= spawn[0] <= x1 and z0 <= spawn[1] <= z1)

    def test_known_distant_seed_can_be_discarded_without_a_world(self):
        result = self.helper.check(-7050104159075989927, 96)
        self.assertEqual(result["supply_positions"], [])
