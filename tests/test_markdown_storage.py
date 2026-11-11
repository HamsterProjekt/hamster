import sys, os.path
# a convoluted line to add hamster module to absolute path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "../src")))

import shutil
import tempfile
import types
import unittest

from hamster.lib import datetime as dt

CONFIGURATION = "hamster.lib.configuration"
_real_configuration = None


def setUpModule():
    # The real configuration needs the installed GSettings schema
    # and a D-Bus session. All that the storage wants is the day start.
    global _real_configuration
    _real_configuration = sys.modules.get(CONFIGURATION)
    stub = types.ModuleType(CONFIGURATION)
    stub.conf = types.SimpleNamespace(day_start=dt.time(5, 30))
    sys.modules[CONFIGURATION] = stub


def tearDownModule():
    if _real_configuration is None:
        del sys.modules[CONFIGURATION]
    else:
        sys.modules[CONFIGURATION] = _real_configuration


from hamster.lib.fact import Fact, FactError
from hamster.storage import markdown


DAY = dt.hday(2020, 3, 10)


def at(hour, minute=0, day=10):
    return dt.datetime(2020, 3, day, hour, minute)


class TestItems(unittest.TestCase):
    """Facts <-> list items."""

    def roundtrip(self, fact, day=DAY):
        lines = markdown.format_item(fact, day)
        self.assertEqual(markdown.parse_item(lines, day), fact)
        return lines

    def test_simple(self):
        fact = Fact(activity="coding", category="work", start=at(9), end=at(10, 30))
        self.assertEqual(self.roundtrip(fact), ["- 09:00 - 10:30 coding@work"])

    def test_ongoing(self):
        fact = Fact(activity="meeting", start=at(11))
        self.assertEqual(self.roundtrip(fact), ["- 11:00 meeting"])

    def test_description_and_tags(self):
        fact = Fact(activity="coding", category="work", description="login bug",
                    tags=["backend", "billable"], start=at(9), end=at(10, 30))
        self.assertEqual(self.roundtrip(fact),
                         ["- 09:00 - 10:30 coding@work, login bug, #backend #billable"])

    def test_multiline_description(self):
        fact = Fact(activity="coding", category="work", tags=["backend"],
                    description="first line\n\n  indented, with a comma\nlast #notatag line",
                    start=at(9), end=at(10, 30))
        self.assertEqual(self.roundtrip(fact),
                         ["- 09:00 - 10:30 coding@work, #backend",
                          "  first line",
                          "",
                          "    indented, with a comma",
                          "  last #notatag line"])

    def test_tag_like_text_in_description(self):
        # on one line, #urgent would be read back as a tag
        fact = Fact(activity="coding", description="see #urgent ticket", tags=["backend"],
                    start=at(9), end=at(10))
        lines = self.roundtrip(fact)
        self.assertEqual(lines, ["- 09:00 - 10:00 coding, #backend", "  see #urgent ticket"])

    def test_after_midnight(self):
        # 01:00 belongs to the hamster day that started the day before
        fact = Fact(activity="party", start=at(23), end=at(1, day=11))
        self.assertEqual(fact.date, DAY)
        self.assertEqual(self.roundtrip(fact), ["- 23:00 - 01:00 party"])

    def test_other_day(self):
        fact = Fact(activity="trip", start=at(9), end=at(18, day=12))
        self.assertEqual(self.roundtrip(fact), ["- 09:00 - 2020-03-12 18:00 trip"])

    def test_activity_starting_with_a_number(self):
        self.assertEqual(self.roundtrip(Fact(activity="5 km run", start=at(7), end=at(8))),
                         ["- 07:00 - 08:00 5 km run"])
        # "07:00 5 km run" would be 5 minutes of "km run"
        self.assertEqual(self.roundtrip(Fact(activity="5 km run", start=at(7))),
                         ["- 07:00 - -- 5 km run"])

    def test_comma_in_activity(self):
        fact = Fact(activity="this, that", start=at(9), end=at(10))
        with self.assertRaises(FactError):
            markdown.format_item(fact, DAY)

    def test_not_facts(self):
        for line in ["- buy milk", "- -15 relative", "- 09:00 - 10:00", "- [ ] 09:00 todo"]:
            self.assertIsNone(markdown.parse_item([line], DAY), line)


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="hamster-test-")
        self.addCleanup(shutil.rmtree, self.directory)
        self.storage = self.open()

    def open(self):
        return markdown.Storage(unsorted_localized="", database_dir=self.directory)

    def path(self, name):
        return os.path.join(self.directory, name)

    def read(self, name):
        with open(self.path(name), encoding="utf-8") as f:
            return f.read()

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf-8") as f:
            f.write(text)

    def add(self, text):
        """Add a fact of 2020-03-10, written as on the command line."""
        fact_id = self.storage.add_fact(Fact.parse(text, default_day=DAY))
        self.assertGreater(fact_id, 0)
        return fact_id

    def day_facts(self, storage=None):
        return (storage or self.storage).get_facts(DAY)

    def summary(self, storage=None):
        return [fact.serialized(default_day=DAY) for fact in self.day_facts(storage)]


class TestFacts(StorageTestCase):
    def test_file_content(self):
        self.add("10:30 - 11:00 email@work")
        self.add("09:00 - 10:30 coding@work, login bug, #backend #billable")
        self.add("08:30 - 09:30 gardening@home")
        self.storage.add_fact(Fact(activity="lunch", start=at(12, day=11), end=at(13, day=11)))
        self.assertEqual(self.read("2020-03.md"),
                         "# 2020-03\n"
                         "\n"
                         "## 2020-03-10\n"
                         "\n"
                         "- 08:30 - 09:30 gardening@home\n"
                         "- 09:30 - 10:30 coding@work, login bug, #backend #billable\n"
                         "- 10:30 - 11:00 email@work\n"
                         "\n"
                         "## 2020-03-11\n"
                         "\n"
                         "- 12:00 - 13:00 lunch\n")
        self.assertEqual(self.read("activities.md"),
                         "# Activities\n"
                         "\n"
                         "- lunch\n"
                         "\n"
                         "## home\n"
                         "\n"
                         "- gardening\n"
                         "\n"
                         "## work\n"
                         "\n"
                         "- coding\n"
                         "- email\n"
                         "\n"
                         "# Tags\n"
                         "\n"
                         "- backend\n"
                         "- billable\n")

    def test_reopen(self):
        self.add("09:00 - 10:30 coding@work, login bug, #backend")
        self.add("10:30 - 11:00 email@work")
        expected = self.day_facts()
        again = self.open()
        self.assertEqual(self.day_facts(again), expected)
        self.assertEqual([fact.activity_id for fact in self.day_facts(again)],
                         [fact.activity_id for fact in expected])
        self.assertEqual(again.get_categories(), self.storage.get_categories())

    def test_month_files(self):
        self.storage.add_fact(Fact(activity="a", start=dt.datetime(2020, 2, 29, 9), end=dt.datetime(2020, 2, 29, 10)))
        self.storage.add_fact(Fact(activity="b", start=dt.datetime(2020, 3, 1, 2), end=dt.datetime(2020, 3, 1, 3)))
        self.storage.add_fact(Fact(activity="c", start=dt.datetime(2020, 3, 1, 9), end=dt.datetime(2020, 3, 1, 10)))
        # 02:00 on March 1st is still the hamster day February 29th
        self.assertIn("- 02:00 - 03:00 b\n", self.read("2020-02.md"))
        self.assertIn("- 09:00 - 10:00 c\n", self.read("2020-03.md"))
        facts = self.storage.get_facts(dt.hday(2020, 2, 1), dt.hday(2020, 3, 31))
        self.assertEqual([fact.activity for fact in facts], ["a", "b", "c"])
        facts = self.storage.get_facts(dt.hday(2020, 3, 1))
        self.assertEqual([fact.activity for fact in facts], ["c"])

    def test_get_and_remove(self):
        first = self.add("09:00 - 10:00 coding@work")
        second = self.add("10:00 - 11:00 email@work")
        fact = self.storage.get_fact(first)
        self.assertEqual((fact.id, fact.activity, fact.category), (first, "coding", "work"))
        self.storage.remove_fact(first)
        self.assertEqual([fact.id for fact in self.day_facts()], [second])
        self.storage.remove_fact(second)
        self.assertFalse(os.path.exists(self.path("2020-03.md")))

    def test_update(self):
        fact_id = self.add("09:00 - 10:00 coding@work")
        fact = self.storage.get_fact(fact_id)
        new_id = self.storage.update_fact(fact_id, fact.copy(description="more\nthan a line",
                                                             end_time=at(10, 15)))
        self.assertGreater(new_id, 0)
        self.assertEqual(self.read("2020-03.md"),
                         "# 2020-03\n\n## 2020-03-10\n\n"
                         "- 09:00 - 10:15 coding@work\n  more\n  than a line\n")
        self.assertEqual(self.open().get_facts(DAY)[0].description, "more\nthan a line")

    def test_split(self):
        self.add("09:00 - 12:00 coding@work, long, #backend")
        self.add("10:00 - 10:30 coffee")
        self.assertEqual(self.summary(),
                         ["09:00 - 10:00 coding@work, long, #backend",
                          "10:00 - 10:30 coffee",
                          "10:30 - 12:00 coding@work, long, #backend"])

    def test_overlaps(self):
        self.add("09:00 - 10:00 a")
        self.add("11:00 - 12:00 b")
        self.add("09:30 - 11:30 c")
        self.assertEqual(self.summary(),
                         ["09:00 - 09:30 a", "09:30 - 11:30 c", "11:30 - 12:00 b"])

    def test_squeeze_in(self):
        self.add("09:00 - 10:00 a")
        self.add("11:00 - 12:00 b")
        # no end given: stops the one in progress, ends where the next one starts
        self.add("09:30 c")
        self.assertEqual(self.summary(),
                         ["09:00 - 09:30 a", "09:30 c", "11:00 - 12:00 b"])
        # same as the database storage: the one in progress wins over the next one
        self.add("10:15 d")
        self.assertEqual(self.summary(),
                         ["09:00 - 09:30 a", "09:30 - 10:15 c", "10:15 d", "11:00 - 12:00 b"])
        self.add("08:00 e")
        self.assertEqual(self.summary()[0], "08:00 - 09:00 e")

    def test_tracking(self):
        now = dt.datetime.now()
        start = now - dt.timedelta(minutes=30)
        first = self.storage.add_fact(Fact(activity="coding", category="work", start=start))
        self.assertGreater(first, 0)
        # same again: nothing to do
        self.assertEqual(self.storage.add_fact(Fact(activity="Coding", category="Work",
                                                    start=now - dt.timedelta(minutes=10))), 0)
        # something else: the first one is stopped
        second = self.storage.add_fact(Fact(activity="email", category="work",
                                            start=now - dt.timedelta(minutes=10)))
        facts = self.storage.get_todays_facts()
        self.assertEqual([(fact.activity, fact.end_time) for fact in facts],
                         [("coding", now - dt.timedelta(minutes=10)), ("email", None)])
        self.storage.stop_tracking(now)
        self.assertEqual(self.storage.get_todays_facts()[-1].end_time, now)
        self.assertEqual(self.storage.get_fact(second).end_time, now)
        # and it is all on disk
        self.assertEqual(self.open().get_todays_facts(), facts[:1] + [self.storage.get_fact(second)])

    def test_too_short(self):
        now = dt.datetime.now()
        self.storage.add_fact(Fact(activity="blink", start=now))
        self.storage.stop_tracking(now)
        self.assertEqual(self.storage.get_todays_facts(), [])

    def test_search(self):
        self.add("09:00 - 10:00 coding@work, login bug, #backend")
        self.add("10:00 - 11:00 email@work")
        self.add("11:00 - 12:00 lunch@home")

        def search(terms):
            return [fact.activity for fact in self.storage.get_facts(DAY, search_terms=terms)]
        self.assertEqual(search("work"), ["coding", "email"])
        self.assertEqual(search("WORK bug"), ["coding"])
        self.assertEqual(search("backend"), ["coding"])
        self.assertEqual(search("not work"), ["lunch"])
        self.assertEqual(search("nothing"), [])

    def test_check_fact(self):
        self.storage.check_fact(Fact(activity="fine", start=at(9), end=at(10)))
        with self.assertRaises(FactError):
            self.storage.check_fact(Fact(activity="this, that", start=at(9), end=at(10)))
        with self.assertRaises(FactError):
            self.storage.add_fact(Fact(activity="this, that", start=at(9), end=at(10)))
        self.assertEqual(self.day_facts(), [])
        self.assertFalse(os.path.exists(self.path("2020-03.md")))

    def test_failure_leaves_no_trace(self):
        self.add("09:00 - 12:00 coding@work")
        before = self.read("2020-03.md")
        original = self.storage._insert

        def failing(fact):
            raise RuntimeError("disk on fire")
        self.storage._insert = failing
        with self.assertRaises(RuntimeError):
            # would have split the existing fact
            self.storage.add_fact(Fact.parse("10:00 - 10:30 coffee@breaks", default_day=DAY))
        self.storage._insert = original
        self.assertEqual(self.summary(), ["09:00 - 12:00 coding@work"])
        self.assertEqual(self.read("2020-03.md"), before)
        self.assertEqual(self.storage.get_category_id("breaks"), 0)
        # and the storage still works
        self.add("10:00 - 10:30 coffee@breaks")
        self.assertEqual(len(self.day_facts()), 3)


class TestHandEditing(StorageTestCase):
    TEXT = ("# March\n"
            "Some notes of mine.\n"
            "\n"
            "## 2020-03-10 Tuesday\n"
            "\n"
            "Slept badly.\n"
            "- 09:00-10:00 coding@work\n"
            "- buy milk\n"
            "* 10:00 - 11:00 email@work, #inbox\n"
            "  two lines\n"
            "  of description\n"
            "\n"
            "## 2020-03-11\n"
            "- 08:00 standup@work\n")

    def test_read(self):
        self.write("2020-03.md", self.TEXT)
        self.assertEqual(self.summary(),
                         ["09:00 - 10:00 coding@work",
                          "10:00 - 11:00 email@work, two lines\nof description, #inbox"])
        self.assertEqual(self.storage.get_activities(""),
                         [dict(name="standup", category="work"),
                          dict(name="email", category="work"),
                          dict(name="coding", category="work")])

    def test_other_lines_are_kept(self):
        self.write("2020-03.md", self.TEXT)
        self.add("11:00 - 12:00 lunch")
        self.assertEqual(self.read("2020-03.md"),
                         "# March\n"
                         "Some notes of mine.\n"
                         "\n"
                         "## 2020-03-10 Tuesday\n"
                         "\n"
                         "Slept badly.\n"
                         "- 09:00 - 10:00 coding@work\n"
                         "- buy milk\n"
                         "- 10:00 - 11:00 email@work, #inbox\n"
                         "  two lines\n"
                         "  of description\n"
                         "- 11:00 - 12:00 lunch\n"
                         "\n"
                         "## 2020-03-11\n"
                         "\n"
                         "- 08:00 standup@work\n")

    def test_external_change(self):
        coding = self.add("09:00 - 10:00 coding@work")
        email = self.add("10:00 - 11:00 email@work")
        text = self.read("2020-03.md")
        text = text.replace("email@work", "email@work, edited by hand")
        text += "- 11:00 - 12:00 lunch\n"
        self.write("2020-03.md", text)
        facts = self.day_facts()
        self.assertEqual([fact.serialized(default_day=DAY) for fact in facts],
                         ["09:00 - 10:00 coding@work",
                          "10:00 - 11:00 email@work, edited by hand",
                          "11:00 - 12:00 lunch"])
        # the untouched fact is still known under the same id
        self.assertEqual(facts[0].id, coding)
        self.assertNotIn(facts[1].id, (coding, email))
        self.assertEqual(len(set(fact.id for fact in facts)), 3)
        with self.assertRaises(AssertionError):
            self.storage.get_fact(email)


class TestExternalChanges(StorageTestCase):
    """What a program synchronising the directory with another computer does."""

    def setUp(self):
        super().setUp()
        self.told = 0

        def told():
            self.told += 1
        self.storage.dispatch_overwrite = told
        self.add("09:00 - 10:00 coding@work")

    def replace(self, name, text):
        """Put a new version of the file in place of the old one."""
        self.write(".incoming", text)
        os.replace(self.path(".incoming"), self.path(name))

    def test_own_writes_are_not_announced(self):
        self.assertEqual(self.storage.check_files(), [])
        self.add("10:00 - 11:00 email@work")
        self.storage.add_category("home")
        self.assertEqual(self.storage.check_files(), [])
        self.assertEqual(self.told, 0)

    def test_change_is_announced_once(self):
        self.replace("2020-03.md", self.read("2020-03.md") + "- 10:00 meeting@work\n")
        self.assertEqual(self.storage.check_files(), ["2020-03.md"])
        self.assertEqual(self.told, 1)
        # nobody asked for the facts since: still nothing new to tell
        self.assertEqual(self.storage.check_files(), [])
        self.assertEqual(self.told, 1)
        # the activity started on the other computer is the last one now
        self.assertEqual(self.day_facts()[-1].activity, "meeting")
        self.assertIsNone(self.day_facts()[-1].end_time)
        self.assertEqual(self.storage.check_files(), [])
        self.assertEqual(self.told, 1)

    def test_change_after_own_write(self):
        self.replace("2020-03.md", self.read("2020-03.md") + "- 10:00 meeting@work\n")
        self.storage.check_files()
        self.add("12:00 - 13:00 lunch")
        self.assertEqual(self.storage.check_files(), [])
        self.replace("2020-03.md", self.read("2020-03.md") + "- 14:00 review@work\n")
        self.assertEqual(self.storage.check_files(), ["2020-03.md"])
        self.assertEqual(self.told, 2)

    def test_month_never_read(self):
        self.write("2019-12.md", "# 2019-12\n\n## 2019-12-02\n\n- 09:00 - 10:00 old@work\n")
        self.assertEqual(self.storage.check_files(), ["2019-12.md"])
        self.assertEqual(self.storage.check_files(), [])
        os.remove(self.path("2019-12.md"))
        self.assertEqual(self.storage.check_files(), ["2019-12.md"])
        self.assertEqual(self.told, 2)

    def test_activities_file(self):
        self.replace("activities.md", self.read("activities.md").replace(
            "# Tags", "## home\n\n- cooking\n\n# Tags"))
        self.assertEqual(self.storage.check_files(), ["activities.md"])
        self.assertTrue(self.storage.get_category_id("home"))
        self.assertEqual(self.storage.check_files(), [])

    def test_files_present_at_start_are_not_news(self):
        self.write("2019-12.md", "# 2019-12\n")
        storage = self.open()
        storage.dispatch_overwrite = self.fail
        self.assertEqual(storage.check_files(), [])

    def test_other_files_do_not_count(self):
        self.write("2020-03-conflict.md", "- 09:00 other")
        self.write("notes.md", "hello")
        self.assertEqual(self.storage.check_files(), [])


class TestActivities(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.add("09:00 - 10:00 coding@work, #backend")
        self.add("10:00 - 11:00 email@work")
        self.add("11:00 - 12:00 lunch")
        self.work = self.storage.get_category_id("work")

    def activities(self, category_id):
        return [row["name"] for row in self.storage.get_category_activities(category_id)]

    def test_categories(self):
        self.assertGreater(self.work, 0)
        self.assertEqual(self.storage.get_category_id("WORK"), self.work)
        self.assertEqual(self.storage.get_category_id(""), -1)
        self.assertEqual(self.storage.get_category_id("unknown"), 0)
        home = self.storage.add_category("home")
        self.assertEqual(self.storage.get_categories(),
                         [dict(id=home, name="home"), dict(id=self.work, name="work")])
        self.assertEqual(self.activities(self.work), ["coding", "email"])
        self.assertEqual(self.activities(-1), ["lunch"])
        self.assertEqual(self.activities(home), [])
        # an empty category survives a restart
        self.assertEqual([category["name"] for category in self.open().get_categories()],
                         ["home", "work"])

    def test_rename_category(self):
        self.storage.update_category(self.work, "job")
        self.assertEqual(self.summary(), ["09:00 - 10:00 coding@job, #backend",
                                          "10:00 - 11:00 email@job",
                                          "11:00 - 12:00 lunch"])
        self.assertEqual(self.summary(self.open()), self.summary())
        self.assertIn("## job\n", self.read("activities.md"))
        self.assertNotIn("work", self.read("activities.md"))

    def test_remove_category(self):
        self.storage.remove_category(self.work)
        self.assertEqual(self.summary(), ["09:00 - 10:00 coding, #backend",
                                          "10:00 - 11:00 email",
                                          "11:00 - 12:00 lunch"])
        self.assertEqual(self.storage.get_categories(), [])
        self.assertEqual(self.activities(-1), ["coding", "email", "lunch"])

    def test_update_activity(self):
        coding = self.storage.get_activity_by_name("coding", self.work)
        self.assertEqual(coding["category"], "work")
        home = self.storage.add_category("home")
        self.storage.update_activity(coding["id"], "hacking", home)
        self.assertEqual(self.summary()[0], "09:00 - 10:00 hacking@home, #backend")
        self.assertEqual(self.activities(home), ["hacking"])
        self.assertEqual(self.activities(self.work), ["email"])
        with self.assertRaises(ValueError):
            self.storage.update_activity(coding["id"], "this, that", home)
        self.assertEqual(self.activities(home), ["hacking"])

    def test_change_category(self):
        lunch = self.storage.get_activity_by_name("lunch", -1)
        self.assertFalse(self.storage.change_category(lunch["id"], -1))
        self.assertTrue(self.storage.change_category(lunch["id"], self.work))
        self.assertEqual(self.summary()[2], "11:00 - 12:00 lunch@work")
        self.assertEqual(self.activities(-1), [])

    def test_remove_activity(self):
        coding = self.storage.get_activity_by_name("coding", self.work)
        idle = self.storage.add_activity("idle", self.work)
        self.assertEqual(self.activities(self.work), ["coding", "email", "idle"])
        self.storage.remove_activity(coding["id"])   # has a fact: only hidden
        self.storage.remove_activity(idle)           # has none: gone
        self.assertEqual(self.activities(self.work), ["email"])
        self.assertIn("- ~~coding~~\n", self.read("activities.md"))
        self.assertNotIn("idle", self.read("activities.md"))
        self.assertEqual(self.activities(self.work), self.open().get_category_activities(self.work)
                         and ["email"])
        self.assertEqual([row["name"] for row in self.storage.get_activities("")], ["lunch", "email"])
        # using it again brings it back
        self.add("12:00 - 13:00 coding@work")
        self.assertEqual(self.activities(self.work), ["coding", "email"])

    def test_autocomplete(self):
        self.storage.add_activity("cooking", -1)
        self.assertEqual(self.storage.get_activities("co"),
                         [dict(name="coding", category="work"),
                          dict(name="cooking", category=None)])
        self.assertEqual(self.storage.get_activities("x"), [])

    def test_tags(self):
        def names(only_autocomplete=False):
            return [tag["name"] for tag in self.storage.get_tags(only_autocomplete)]
        self.assertEqual(names(), ["backend"])
        self.storage.update_autocomplete_tags("urgent, later")
        self.assertEqual(names(), ["backend", "later", "urgent"])
        # backend is used by a fact: kept, but not suggested
        self.assertEqual(names(only_autocomplete=True), ["later", "urgent"])
        self.assertIn("- ~~backend~~\n", self.read("activities.md"))
        self.storage.update_autocomplete_tags("urgent")
        self.assertEqual(names(), ["backend", "urgent"])
        tags = self.storage.get_tag_ids(["backend", "new"])
        self.assertEqual([(tag["name"], tag["autocomplete"]) for tag in tags],
                         [("backend", True), ("new", True)])
        self.assertEqual([tag["name"] for tag in self.open().get_tags(True)],
                         ["backend", "new", "urgent"])

    def test_edited_index(self):
        self.write("activities.md",
                   "# Activities\n\n- lunch\n\n## work\n- coding\n- ~~email~~\n- review\n\n## home\n\n# Tags\n- backend\n")
        self.assertEqual(self.activities(self.work), ["coding", "review"])
        self.assertEqual([category["name"] for category in self.storage.get_categories()],
                         ["home", "work"])
        # ids did not move
        self.assertEqual(self.storage.get_category_id("work"), self.work)


if __name__ == '__main__':
    unittest.main()
