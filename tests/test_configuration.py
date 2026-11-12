import sys, os.path
# a convoluted line to add hamster module to absolute path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "../src")))

import types
import unittest

from gi.repository import Gio as gio
from gi.repository import GLib as glib

SCHEMA = "io.github.HamsterProjekt.Hamster"
schemas = gio.SettingsSchemaSource.get_default()
installed = schemas is not None and schemas.lookup(SCHEMA, True) is not None


# The configuration module needs the installed GSettings schema
# and a D-Bus session, as in CI.
@unittest.skipUnless(installed, "the GSettings schema {} is not installed".format(SCHEMA))
class TestGSettingsStore(unittest.TestCase):
    def store(self, values):
        """GSettingsStore.get, with the given settings in place of GSettings."""
        from hamster.lib.configuration import GSettingsStore
        settings = types.SimpleNamespace(get_value=values.get)
        return lambda key, default=None: GSettingsStore.get(
            types.SimpleNamespace(_settings=settings), key, default)

    def test_known_key(self):
        get = self.store({"day-start-minutes": glib.Variant("i", 330)})
        self.assertEqual(get("day-start-minutes"), 330)

    def test_unknown_key_gives_the_default(self):
        get = self.store({})
        with self.assertLogs("hamster", level="WARNING"):
            self.assertEqual(get("no-such-key", default=42), 42)
        self.assertIsNone(get("no-such-key"))


if __name__ == '__main__':
    unittest.main()
