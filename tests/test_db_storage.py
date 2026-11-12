import sys, os.path
# a convoluted line to add hamster module to absolute path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "../src")))

import stat
import tempfile
import unittest

from hamster.storage import db


class TestDatabaseFile(unittest.TestCase):
    def test_new_database_is_only_for_the_user(self):
        with tempfile.TemporaryDirectory() as directory:
            database_dir = os.path.join(directory, "hamster")
            storage = db.Storage(database_dir=database_dir)
            self.assertEqual(storage.db_path, os.path.join(database_dir, "hamster.db"))
            mode = stat.S_IMODE(os.stat(storage.db_path).st_mode)
            self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main()
