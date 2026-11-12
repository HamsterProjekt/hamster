# This file is part of Hamster
# Copyright (c) The Hamster time tracker developers
# SPDX-License-Identifier: GPL-3.0-or-later

"""Storage backend that keeps everything in Markdown files.

Layout of the storage directory:

    2026-11.md      facts, one file per month
    activities.md   categories, activities and tags

A month file has one section per hamster day, and one list item per fact,
written in the same syntax as the hamster command line:

    # 2026-11

    ## 2026-11-11

    - 09:00 - 10:30 coding@work,, login bug, #backend #billable
    - 10:30 - 11:00 email@work
    - 11:00 meeting@work
      A description that does not fit on the line (several lines,
      or text the one-line syntax cannot express) goes here, indented.

The files are meant to be edited by hand too. Lines that are not facts
are kept where they are. Changes made by another program are picked up
while the service is running.

activities.md plays the role of the activities, categories and tags
tables. An entry that is struck through (~~like this~~) is kept but not
offered for autocompletion:

    # Activities

    - an activity without category

    ## work

    - coding
    - ~~retired activity~~

    # Tags

    - backend

Differences with the database backend:
- Fact ids are attributed when a file is read. They are stable for as long
  as the service runs and the fact is not modified by another program,
  but they are not kept across restarts.
- Searching is a case-insensitive substring match on activity, category,
  description and tags; all terms must match. "not " reverses the search.
- An activity that was removed and is used again comes back in its own
  category (the database moves it to the unsorted ones).
- Activities are sorted by last use among the months read so far.
"""

import functools
import logging
import os
import re
import time

from hamster.lib import datetime as dt
from hamster.lib.fact import Fact, FactError
from hamster.storage import storage

logger = logging.getLogger(__name__)

try:
    from gi.repository import Gio as gio
    from gi.repository import GLib as glib
except ImportError:
    print("Could not import gio - requires pygobject. File monitoring will be disabled")
    gio = None
    glib = None

# A program that synchronises the directory with other computers may write
# a file in several steps: the clients are told once it has been quiet for
# this long (milliseconds).
SETTLE_MS = 500
# The files are also looked at this often (seconds), should the
# notifications of the system not arrive (too many watched files, a
# network file system).
POLL_SECONDS = 30


SETTINGS_SCHEMA = "io.github.HamsterProjekt.Hamster"
DIRECTORY_KEY = "markdown-directory"
INDEX_FILE = "activities.md"
MONTH_FILE_RE = re.compile(r"^(\d{4})-(\d{2})\.md$")
DAY_HEADING_RE = re.compile(r"^##\s+(\d{4})-(\d{2})-(\d{2})(?!\d)")
STRUCK_RE = re.compile(r"^~~(.+)~~$")
INDENT = "  "
UNSORTED_ID = -1


# --- facts <-> text ---

def _is_indented(line):
    return line[:1] in (" ", "\t") and line.strip() != ""


def _dedent(line):
    if line.startswith(INDENT):
        return line[len(INDENT):]
    if line.startswith("\t"):
        return line[1:]
    return line.lstrip()


def parse_item(lines, day):
    """Read a fact from the lines of a list item.

    lines (list of str): the "- ..." line, followed by its continuation lines.
    day (dt.hday): day of the section, for times given without a date.

    Return a Fact, or None if the item is not a fact.
    """
    head = lines[0][2:].strip()
    # A fact starts with its start time.
    # This also keeps relative times ("-15") out; they are meaningless here.
    if not head[:1].isdigit():
        return None
    try:
        fact = Fact.parse(head, default_day=day)
    except Exception as error:  # the parser is not meant for arbitrary text
        logger.debug("not a fact: '{}' ({})".format(head, error))
        return None
    if not fact.start_time or not fact.activity:
        return None
    extra = "\n".join(_dedent(line) for line in lines[1:]).strip()
    if extra:
        fact.description = "{}\n{}".format(fact.description, extra) if fact.description else extra
    return fact


def format_item(fact, day):
    """Write a fact as the lines of a list item.

    Raise FactError if the fact cannot be written
    in a way that reads back identical.
    """
    description = fact.description or ""
    candidates = []
    if "\n" not in description:
        # everything on one line
        candidates.append((fact, []))
    # description on continuation lines
    candidates.append((fact.copy(description=""), description.split("\n") if description else []))

    for inline, extra in candidates:
        heads = [inline.serialized(default_day=day),
                 # with the missing end spelled out ("09:00 - -- 5 km run"),
                 # for when the activity could be taken for a time
                 "{} {}".format(inline.range.format(default_day=day, explicit_none=True),
                                inline.serialized_name())]
        for head in heads:
            lines = ["- " + head]
            lines.extend((INDENT + line).rstrip() for line in extra)
            if "\n" not in head and parse_item(lines, day) == fact:
                return lines

    raise FactError(
        "This activity cannot be stored in a Markdown file: '{}'\n"
        "Activities, categories and tags must not contain commas, "
        "and tags must not contain '#'.".format(fact.serialized_name()))


class _Day(object):
    """Section of a month file."""

    def __init__(self, date, heading=None):
        self.date = date
        self.heading = heading or "## {}".format(date.strftime("%Y-%m-%d"))
        # Fact, or str for any other line (kept verbatim)
        self.items = []

    @property
    def facts(self):
        return [item for item in self.items if isinstance(item, Fact)]


class _Month(object):
    """Content of a month file."""

    def __init__(self, key):
        self.key = key
        # lines before the first day
        self.preamble = []
        self.days = []
        # stat signature of the file when read or written; None if absent
        self.signature = None
        self.dirty = False

    @property
    def title(self):
        return "# {:04d}-{:02d}".format(*self.key)

    @property
    def facts(self):
        return [fact for day in self.days for fact in day.facts]


def _strip_blank(lines):
    """Remove the leading and trailing blank text lines, in place."""
    while lines and lines[0] == "":
        del lines[0]
    while lines and lines[-1] == "":
        del lines[-1]


def parse_month(text, month):
    """Fill month (_Month) from the content of its file."""
    lines = [line.rstrip() for line in text.split("\n")]
    current = None
    i = 0
    while i < len(lines):
        line = lines[i]
        match = DAY_HEADING_RE.match(line)
        if match:
            try:
                date = dt.hday(*(int(group) for group in match.groups()))
            except ValueError:
                date = None
            if date:
                current = _Day(date, heading=line)
                month.days.append(current)
                i += 1
                continue
        if current is None:
            month.preamble.append(line)
            i += 1
            continue
        if line.startswith(("- ", "* ")):
            # gather the continuation lines; a blank line belongs to the item
            # only if what follows is indented too
            j = i + 1
            while j < len(lines):
                if _is_indented(lines[j]):
                    j += 1
                    continue
                k = j
                while k < len(lines) and lines[k] == "":
                    k += 1
                if k > j and k < len(lines) and _is_indented(lines[k]):
                    j = k
                    continue
                break
            item = lines[i:j]
            fact = parse_item(item, current.date)
            if fact:
                current.items.append(fact)
            else:
                current.items.extend(item)
            i = j
            continue
        current.items.append(line)
        i += 1

    _strip_blank(month.preamble)
    for day in month.days:
        _strip_blank(day.items)


def format_month(month):
    """Return the content of the file of month (_Month),
    or None if there is nothing worth a file."""
    days = [day for day in month.days if day.items]
    if not days and month.preamble in ([], [month.title]):
        return None
    lines = list(month.preamble) or [month.title]
    for day in days:
        lines.extend(["", day.heading, ""])
        for item in day.items:
            if isinstance(item, Fact):
                lines.extend(format_item(item, day.date))
            else:
                lines.append(item)
    return "\n".join(lines) + "\n"


# --- files ---

def _signature(path):
    """Identify the current version of a file. None if there is no file."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write(path, text):
    """Replace the file atomically; remove it if text is None."""
    if text is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    directory, name = os.path.split(path)
    tmp_path = os.path.join(directory, ".{}.tmp".format(name))
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def default_directory():
    """Where the files are, unless told otherwise."""
    xdg_data_home = (os.environ.get("XDG_DATA_HOME")
                     or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(xdg_data_home, "hamster", "markdown")


def configured_directory():
    """Return the folder chosen in the settings; empty if there is none."""
    if not gio:
        return ""
    source = gio.SettingsSchemaSource.get_default()
    schema = source.lookup(SETTINGS_SCHEMA, True) if source else None
    # an older schema, without the key, may be the one installed
    if not schema or not schema.has_key(DIRECTORY_KEY):
        return ""
    directory = gio.Settings.new(SETTINGS_SCHEMA).get_string(DIRECTORY_KEY).strip()
    return os.path.expanduser(directory)


def _month_key(day):
    return (day.year, day.month)


def _mutator(method):
    """Decorator for the methods that modify the storage.

    The files are written when the outermost one returns
    (or at the end of the transaction, if there is one).
    If anything fails, the storage goes back to what is on disk.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._depth == 0:
            self._refresh_index()
        self._depth += 1
        try:
            result = method(self, *args, **kwargs)
        except Exception:
            self._depth = 0
            self._in_transaction = False
            self._rollback()
            raise
        self._depth -= 1
        if self._depth == 0 and not self._in_transaction:
            self._commit()
        return result
    return wrapper


# Note: the class must be named Storage, like the base class.
#       The base class calls the private methods (e.g. self.__get_facts);
#       name mangling resolves them only if the class names are the same.
class Storage(storage.Storage):
    def __init__(self, unsorted_localized="Unsorted", database_dir=None):
        """Markdown storage.

        Args:
            unsorted_localized (str):
                Fact.category value for returned facts without category.
                (same as for the database storage)
            database_dir (path):
                Directory holding the Markdown files, or None to use
                $HAMSTER_MARKDOWN_DIR, or else the "markdown-directory"
                setting, or else the default location.

        Note: Zero id means failure.
              Unsorted category id is hard-coded as -1
        """
        storage.Storage.__init__(self)

        self._unsorted_localized = unsorted_localized
        self.directory = self._init_directory(database_dir)
        logger.info("markdown directory: '{}'".format(self.directory))

        # ids are not persistent; starting from the clock makes it unlikely
        # for an id kept by a client across a service restart
        # to designate another fact.
        self._next_id = (int(time.time()) % 20000000) * 100 + 1

        self._months = {}       # key -> _Month
        self._by_id = {}        # fact id -> Fact
        self._where = {}        # fact id -> (_Month, _Day)

        self._categories = []   # dict(id, name)
        self._activities = []   # dict(id, name, category_id, deleted)
        self._tags = []         # dict(id, name, autocomplete)
        self._last_used = {}    # activity id -> latest start seen
        self._index_signature = None
        self._index_dirty = False

        self._depth = 0
        self._in_transaction = False

        self._load_index()

        # So the clients are told when another program changes the files,
        # for instance one that brings in what was tracked on another computer.
        self._announced = {name: _signature(os.path.join(self.directory, name))
                           for name in self._file_names()}
        self._settle_source = 0
        self._monitor = None
        if gio:
            directory = gio.File.new_for_path(self.directory)
            self._monitor = directory.monitor_directory(gio.FileMonitorFlags.WATCH_MOVES, None)
            self._monitor.connect("changed", self._on_directory_change)
            glib.timeout_add_seconds(POLL_SECONDS, self._on_poll)

        self.run_fixtures()

    @staticmethod
    def _init_directory(database_dir):
        directory = (database_dir
                     or os.environ.get("HAMSTER_MARKDOWN_DIR")
                     or configured_directory()
                     or default_directory())
        # No fallback if that fails (e.g. a drive that is not mounted):
        # the facts would silently end up in two places.
        os.makedirs(directory, exist_ok=True)
        return os.path.realpath(directory)

    # --- transactions ---

    def start_transaction(self):
        self._refresh_index()
        self._in_transaction = True

    def end_transaction(self):
        self._in_transaction = False
        self._commit()

    def _commit(self):
        """Write what has been modified."""
        for month in self._months.values():
            if month.dirty:
                path = self._month_path(month.key)
                _write(path, format_month(month))
                month.signature = _signature(path)
                month.dirty = False
        if self._index_dirty:
            path = os.path.join(self.directory, INDEX_FILE)
            _write(path, self._format_index())
            self._index_signature = _signature(path)
            self._index_dirty = False

    def _rollback(self):
        """Forget what has been modified and not written."""
        logger.warning("storage modification failed, going back to the files")
        for key in [key for key, month in self._months.items() if month.dirty]:
            self._load_month(key)
        self._load_index()

    def _file_names(self):
        """Names of the files of the storage, on disk or read earlier."""
        names = {INDEX_FILE}
        names.update(os.path.basename(self._month_path(key)) for key in self._months)
        try:
            names.update(name for name in os.listdir(self.directory) if MONTH_FILE_RE.match(name))
        except OSError:
            pass   # e.g. the directory is on a drive that went away
        return names

    def _on_directory_change(self, monitor, gio_file, other_file, event):
        for changed in (gio_file, other_file):
            if changed is None:
                continue
            name = changed.get_basename()
            if name == INDEX_FILE or MONTH_FILE_RE.match(name):
                # not right now: the file may still be being written
                if self._settle_source:
                    glib.source_remove(self._settle_source)
                self._settle_source = glib.timeout_add(SETTLE_MS, self._on_settled)
                return

    def _on_settled(self):
        self._settle_source = 0
        self.check_files()
        return False

    def _on_poll(self):
        if not self._settle_source:
            self.check_files()
        return True

    def check_files(self):
        """Tell the clients if another program changed the files.

        Once per change: what this storage wrote itself does not count,
        nor does a change that the clients were told about already.
        Return the names of the changed files.
        """
        changed = []
        current = {}
        # also those that were there and are gone
        for name in self._file_names().union(self._announced):
            signature = _signature(os.path.join(self.directory, name))
            if signature is not None:
                current[name] = signature
            if signature == self._announced.get(name):
                continue
            if name == INDEX_FILE:
                if self._index_dirty or signature == self._index_signature:
                    continue
            else:
                match = MONTH_FILE_RE.match(name)
                month = self._months.get((int(match.group(1)), int(match.group(2))))
                if month and (month.dirty or signature == month.signature):
                    continue
            changed.append(name)
        self._announced = current
        if changed:
            logger.warning("{} modified externally. Calling all stations"
                           .format(", ".join(sorted(changed))))
            self.dispatch_overwrite()
        return sorted(changed)

    # --- month files ---

    def _month_path(self, key):
        return os.path.join(self.directory, "{:04d}-{:02d}.md".format(*key))

    def _month_keys(self):
        """Keys of all the months that have a file or facts in memory, sorted."""
        keys = set(self._months)
        for name in os.listdir(self.directory):
            match = MONTH_FILE_RE.match(name)
            if match:
                keys.add((int(match.group(1)), int(match.group(2))))
        return sorted(keys)

    def _month(self, key):
        """Return the up-to-date _Month."""
        month = self._months.get(key)
        if month is None or (not month.dirty
                             and month.signature != _signature(self._month_path(key))):
            month = self._load_month(key)
        return month

    def _load_month(self, key):
        previous = self._months.get(key)
        # facts that are still there keep their id
        known_ids = {}
        if previous:
            for fact in previous.facts:
                known_ids.setdefault(self._identity(fact), []).append(fact.id)
                self._by_id.pop(fact.id, None)
                self._where.pop(fact.id, None)

        path = self._month_path(key)
        month = _Month(key)
        month.signature = _signature(path)
        parse_month(_read(path), month)
        for day in month.days:
            for fact in day.facts:
                ids = known_ids.get(self._identity(fact))
                self._register(fact, month, day, ids.pop(0) if ids else None)
                if _month_key(fact.date) != key:
                    logger.warning("{}: '{}' belongs to another month, it may be overlooked"
                                   .format(os.path.basename(path), fact))
        self._months[key] = month
        return month

    @staticmethod
    def _identity(fact):
        return (fact.start_time, fact.end_time, fact.activity, fact.category,
                fact.description, tuple(fact.tags))

    def _register(self, fact, month, day, fact_id=None):
        if fact_id is None:
            fact_id = self._next_id
            self._next_id += 1
        fact.id = fact_id
        self._by_id[fact_id] = fact
        self._where[fact_id] = (month, day)
        self._adopt(fact)

    def _adopt(self, fact):
        """Link the fact to the activities, creating what is missing."""
        category_id = self._find_category_id(fact.category) or self._new_category(fact.category)
        activity = (self._find_activity(fact.activity, category_id)
                    or self._new_activity(fact.activity, category_id))
        fact.activity_id = activity["id"]
        last = self._last_used.get(activity["id"])
        if last is None or fact.start_time > last:
            self._last_used[activity["id"]] = fact.start_time
        for name in fact.tags:
            if not self._find_tag(name):
                self._new_tag(name)

    # --- facts in memory ---

    def _query(self, start=None, end=None):
        """Return the facts (the stored objects, not copies) that
        are not finished before start and do not begin after end,
        sorted by start time. None means no limit."""
        keys = self._month_keys()
        if start:
            # one month of margin, for the facts begun before the range
            first = _month_key(start.hday() - dt.timedelta(days=31))
            keys = [key for key in keys if key >= first]
        if end:
            last = _month_key(end.hday())
            keys = [key for key in keys if key <= last]
        facts = []
        for key in keys:
            for fact in self._month(key).facts:
                if ((start is None or fact.end_time is None or fact.end_time >= start)
                        and (end is None or fact.start_time <= end)):
                    facts.append(fact)
        facts.sort(key=lambda fact: fact.start_time)
        return facts

    def _insert(self, fact):
        """Store a new fact."""
        date = fact.date
        month = self._month(_month_key(date))
        day = next((day for day in month.days if day.date == date), None)
        if day is None:
            day = _Day(date)
            position = len([other for other in month.days if other.date <= date])
            month.days.insert(position, day)
        # before the first fact that starts later
        position = len(day.items)
        for i, item in enumerate(day.items):
            if isinstance(item, Fact) and item.start_time > fact.start_time:
                position = i
                break
        day.items.insert(position, fact)
        month.dirty = True
        self._register(fact, month, day, fact.id)

    def _delete(self, fact):
        month, day = self._where.pop(fact.id)
        del self._by_id[fact.id]
        # by identity; facts compare equal when they have the same content
        day.items[:] = [item for item in day.items if item is not fact]
        month.dirty = True

    def _touch(self, fact):
        """Mark the file of a fact as to be written."""
        self._where[fact.id][0].dirty = True

    def _set_start(self, fact, start):
        if start.hday() == fact.date:
            fact.range.start = start
            self._touch(fact)
        else:
            # goes to another section, possibly another file
            self._delete(fact)
            fact.range.start = start
            self._insert(fact)

    def _set_end(self, fact, end):
        fact.range.end = end
        self._touch(fact)

    def _export(self, fact):
        """Return the copy of a stored fact meant for the caller."""
        return fact.copy(id=fact.id,
                         activity_id=fact.activity_id,
                         category=fact.category or self._unsorted_localized)

    # --- facts ---

    @classmethod
    def check_fact(cls, fact, default_day=None):
        storage.Storage.check_fact(fact, default_day=default_day)
        format_item(fact, fact.date)

    def __get_fact(self, id):
        self._query(*dt.Range.today())  # the usual suspects, if not read yet
        assert id in self._by_id, "No fact with id {}".format(id)
        fact = self._export(self._by_id[id])
        logger.info("got fact {}".format(fact))
        return fact

    @_mutator
    def __touch_fact(self, fact, end_time=None):
        end_time = end_time or dt.datetime.now()
        stored = self._by_id.get(fact.id)
        if stored is None:
            logger.warning("no fact with id {}".format(fact.id))
        # tasks under one minute do not count
        elif end_time - stored.start_time < dt.timedelta(minutes=1):
            self._delete(stored)
        else:
            self._set_end(stored, end_time)

    def __squeeze_in(self, start_time):
        """ tries to put task in the given date
            if there are conflicts, we will only truncate the ongoing task
            and replace it's end part with our activity """

        # we are checking if our start time is in the middle of anything
        # or maybe there is something after us - so we know to adjust end time
        # in the latter case go only few hours ahead. everything else is madness, heh
        half_day = dt.timedelta(hours=12)
        for fact in self._query(start_time, start_time + half_day):
            if fact.end_time:
                in_the_middle = fact.start_time < start_time < fact.end_time
            else:
                in_the_middle = start_time - half_day < fact.start_time < start_time
            if in_the_middle:
                # truncate it to our start
                self._set_end(fact, start_time)
                return None
            elif start_time < fact.start_time < start_time + half_day:
                # otherwise we have found a task that is after us
                return fact.start_time
        return None

    def __solve_overlaps(self, start_time, end_time):
        """finds facts that happen in given interval and shifts them to
        make room for new fact
        """
        if end_time is None or start_time is None:
            return

        # possible combinations and the clauses that catch them
        # (the side of the number marks if it catches the end or start time)
        #             |----------------- NEW -----------------|
        #      |--- old --- 1|   |2 --- old --- 1|   |2 --- old ---|
        # |3 -----------------------  big old   ------------------------ 3|
        conflicts = [
            fact for fact in self._query(start_time, end_time)
            if (fact.end_time and start_time < fact.end_time < end_time)
            or (start_time < fact.start_time < end_time)
            or (fact.end_time and fact.start_time < start_time and fact.end_time > end_time)
        ]

        for fact in conflicts:
            fact_end_time = fact.end_time or dt.datetime.now()

            # won't eliminate as it is better to have overlapping entries than loosing data
            if start_time < fact.start_time and end_time > fact_end_time:
                continue

            # split - truncate until beginning of new entry and create new activity for end
            if (fact.start_time < start_time < fact_end_time
                    and fact.start_time < end_time < fact_end_time):
                logger.info("splitting %s" % fact.activity)
                # truncate until beginning of the new entry
                self._set_end(fact, start_time)
                # create new fact for the end
                new_fact = Fact(activity=fact.activity,
                                category=fact.category,
                                description=fact.description,
                                tags=list(fact.tags),
                                start_time=end_time,
                                end_time=fact_end_time)
                storage.Storage.check_fact(new_fact)
                self.__add_fact(new_fact)

            # overlap start
            elif start_time < fact.start_time < end_time:
                logger.info("Overlapping start of %s" % fact.activity)
                self._set_start(fact, end_time)

            # overlap end
            elif start_time < fact_end_time < end_time:
                logger.info("Overlapping end of %s" % fact.activity)
                self._set_end(fact, start_time)

    @_mutator
    def __add_fact(self, fact, temporary=False):
        """Add fact to the storage.

        Args:
            fact (Fact)
        Returns:
            int, the new fact id (> 0) on success,
                 0 if nothing needed to be done
                 (e.g. if the same fact was already on-going)
        """
        logger.info("adding fact {}".format(fact))

        start_time = fact.start_time
        end_time = fact.end_time

        # this will create any missing tags too
        self.get_tag_ids(fact.tags)

        # now check if maybe there is also a category
        category_id = self.__get_category_id(fact.category)
        if not category_id:
            category_id = self.__add_category(fact.category)

        # try to find activity, resurrect if not temporary
        activity = self.__get_activity_by_name(fact.activity, category_id,
                                               resurrect=not temporary)
        if activity:
            activity_id = activity['id']
        else:
            activity_id = self.__add_activity(fact.activity, category_id, temporary)

        # the names as they are known, whatever the case they were given in
        new_fact = Fact(activity=self._activity(activity_id)["name"],
                        category=self._category_name(category_id),
                        description=fact.description,
                        tags=list(fact.tags),
                        start_time=start_time,
                        end_time=end_time)
        # better fail before changing anything else
        format_item(new_fact, new_fact.date)

        # if we are working on +/- current day - check the last_activity
        if (dt.timedelta(days=-1) <= dt.datetime.now() - start_time <= dt.timedelta(days=1)):
            # pull in previous facts
            facts = self._query(*dt.Range.today())

            previous = None
            if facts and facts[-1].end_time is None:
                previous = facts[-1]

            if previous and previous.start_time <= start_time:
                # check if maybe that is the same one, in that case no need to restart
                if (previous.activity_id == activity_id
                        and set(previous.tags) == set(fact.tags)
                        and (previous.description or "") == (fact.description or "")):
                    logger.info("same fact, already on-going, nothing to do")
                    return 0

                # if no description is added
                # see if maybe previous was too short to qualify as an activity
                if (not previous.description
                        and 60 >= (start_time - previous.start_time).seconds >= 0):
                    self._delete(previous)

                    # now that we removed the previous one, see if maybe the one
                    # before that is actually same as the one we want to start
                    # (glueing)
                    if (len(facts) > 1 and facts[-2].end_time
                            and 60 >= (start_time - facts[-2].end_time).seconds >= 0):
                        before = facts[-2]
                        if (before.activity_id == activity_id
                                and set(before.tags) == set(fact.tags)):
                            # resume and return
                            self._set_end(before, None)
                            return before.id
                else:
                    # otherwise stop
                    self._set_end(previous, start_time)

        # done with the current activity, now we can solve overlaps
        if not end_time:
            new_fact.range.end = self.__squeeze_in(start_time)
        else:
            self.__solve_overlaps(start_time, end_time)

        # finally add the new entry
        self._insert(new_fact)
        logger.info("fact successfully added, with id #{}".format(new_fact.id))
        return new_fact.id

    def __get_todays_facts(self):
        return self.__get_facts(dt.Range.today())

    def __get_facts(self, range, search_terms=""):
        datetime_from = range.start
        datetime_to = range.end

        logger.info("searching for facts from {} to {}".format(datetime_from, datetime_to))

        facts = self._query(datetime_from, datetime_to)

        if datetime_from:
            # ignore old on-going facts
            limit = datetime_from - dt.timedelta(days=30)
            facts = [fact for fact in facts if fact.start_time >= limit]

        if search_terms:
            # flip the query around when it starts with "not "
            reverse_search_terms = search_terms.lower().startswith("not ")
            if reverse_search_terms:
                search_terms = search_terms[4:]
            terms = search_terms.casefold().split()

            def matches(fact):
                text = " ".join([fact.activity, fact.category, fact.description]
                                + fact.tags).casefold()
                return all(term in text for term in terms)

            facts = [fact for fact in facts if matches(fact) != reverse_search_terms]

        return [self._export(fact) for fact in facts]

    @_mutator
    def __remove_fact(self, fact_id):
        logger.info("removing fact #{}".format(fact_id))
        fact = self._by_id.get(fact_id)
        if fact is None:
            logger.warning("no fact with id {}".format(fact_id))
        else:
            self._delete(fact)

    def _rewrite_facts(self, selected, change):
        """Apply change(fact) to all the stored facts for which selected(fact)."""
        for fact in self._query():
            if selected(fact):
                change(fact)
                self._touch(fact)
                self._adopt(fact)

    def _check_names(self, activity="activity", category=""):
        """Raise ValueError if the names cannot be written."""
        probe = Fact(activity=activity, category=category,
                     start_time=dt.datetime(2000, 1, 1, 12, 0))
        try:
            storage.Storage.check_fact(probe)
            format_item(probe, probe.date)
        except FactError as error:
            raise ValueError(str(error))

    # --- activities.md ---

    def _load_index(self):
        path = os.path.join(self.directory, INDEX_FILE)
        self._index_signature = _signature(path)
        self._index_dirty = False

        # whatever keeps its name keeps its id
        category_ids = {category["name"].lower(): category["id"] for category in self._categories}
        activity_ids = {(activity["name"].lower(), activity["category_id"]): activity["id"]
                        for activity in self._activities}
        tag_ids = {tag["name"]: tag["id"] for tag in self._tags}
        self._categories, self._activities, self._tags = [], [], []

        section = None
        category_id = UNSORTED_ID
        for line in _read(path).split("\n"):
            line = line.strip()
            if line.startswith("# "):
                section = line[2:].strip().lower()
                category_id = UNSORTED_ID
            elif line.startswith("## ") and section == "activities":
                name = line[3:].strip()
                category_id = (self._find_category_id(name)
                               or self._new_category(name, category_ids.get(name.lower())))
            elif line.startswith(("- ", "* ")):
                name = line[2:].strip()
                struck = STRUCK_RE.match(name)
                if struck:
                    name = struck.group(1).strip()
                if not name:
                    continue
                if section == "activities" and not self._find_activity(name, category_id):
                    self._new_activity(name, category_id, deleted=bool(struck),
                                       id=activity_ids.get((name.lower(), category_id)))
                elif section == "tags" and not self._find_tag(name):
                    self._new_tag(name, autocomplete=not struck, id=tag_ids.get(name))
        self._index_dirty = False

        # the facts already read
        for fact in self._by_id.values():
            self._adopt(fact)

    def _refresh_index(self):
        """Read activities.md again if another program changed it."""
        path = os.path.join(self.directory, INDEX_FILE)
        if not self._index_dirty and self._index_signature != _signature(path):
            self._load_index()

    def _format_index(self):
        def entry(name, hidden):
            return "- ~~{}~~".format(name) if hidden else "- {}".format(name)

        def activities(category_id):
            rows = sorted((activity for activity in self._activities
                           if activity["category_id"] == category_id),
                          key=lambda activity: activity["name"].lower())
            return [entry(activity["name"], activity["deleted"]) for activity in rows]

        lines = ["# Activities"]
        unsorted = activities(UNSORTED_ID)
        if unsorted:
            lines.extend([""] + unsorted)
        for category in sorted(self._categories, key=lambda category: category["name"].lower()):
            lines.extend(["", "## {}".format(category["name"])])
            rows = activities(category["id"])
            if rows:
                lines.extend([""] + rows)
        lines.extend(["", "# Tags"])
        tags = sorted(self._tags, key=lambda tag: tag["name"])
        if tags:
            lines.extend([""] + [entry(tag["name"], not tag["autocomplete"]) for tag in tags])
        return "\n".join(lines) + "\n"

    def _new_id(self, rows):
        return max((row["id"] for row in rows), default=0) + 1

    def _find_category_id(self, name):
        """Return the category id; -1 for no category, 0 if not found."""
        if not name:
            return UNSORTED_ID
        lower = name.lower()
        for category in self._categories:
            if category["name"].lower() == lower:
                return category["id"]
        return 0

    def _new_category(self, name, id=None):
        category = dict(id=id or self._new_id(self._categories), name=name)
        self._categories.append(category)
        self._index_dirty = True
        return category["id"]

    def _category_name(self, category_id):
        """Return the name of the category; empty for no category."""
        for category in self._categories:
            if category["id"] == category_id:
                return category["name"]
        return ""

    def _activity(self, activity_id):
        for activity in self._activities:
            if activity["id"] == activity_id:
                return activity
        return None

    def _find_activity(self, name, category_id=None):
        """Return the most recent, preferably not deleted, activity.

        Any category if category_id is None or 0.
        """
        lower = name.lower()
        found = [activity for activity in self._activities
                 if activity["name"].lower() == lower
                 and (not category_id or activity["category_id"] == category_id)]
        found.sort(key=lambda activity: (activity["deleted"], -activity["id"]))
        return found[0] if found else None

    def _new_activity(self, name, category_id, deleted=False, id=None):
        activity = dict(id=id or self._new_id(self._activities), name=name,
                        category_id=category_id, deleted=deleted)
        self._activities.append(activity)
        self._index_dirty = True
        return activity

    def _find_tag(self, name):
        for tag in self._tags:
            if tag["name"] == name:
                return tag
        return None

    def _new_tag(self, name, autocomplete=True, id=None):
        tag = dict(id=id or self._new_id(self._tags), name=name, autocomplete=autocomplete)
        self._tags.append(tag)
        self._index_dirty = True
        return tag

    def _used_activity_ids(self):
        return set(fact.activity_id for fact in self._query())

    # --- tags ---

    def __get_tags(self, only_autocomplete=False):
        self._refresh_index()
        return [dict(tag) for tag in sorted(self._tags, key=lambda tag: tag["name"])
                if tag["autocomplete"] or not only_autocomplete]

    @_mutator
    def __get_tag_ids(self, tags):
        """look up tags by their name. create if not found"""
        changes = False
        found = []
        for name in tags:
            tag = self._find_tag(name)
            if tag is None:
                tag = self._new_tag(name)
                changes = True
            elif not tag["autocomplete"]:
                # resurrection
                tag["autocomplete"] = True
                self._index_dirty = True
                changes = True
            if tag not in found:
                found.append(tag)
        return [dict(tag) for tag in found], changes

    @_mutator
    def __update_autocomplete_tags(self, tags):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]  # split by comma

        # first we will create new ones
        kept, changes = self.__get_tag_ids(tags)
        kept = set(tag["name"] for tag in kept)

        # now we will find which ones are gone from the list
        used = set(name for fact in self._query() for name in fact.tags)
        for tag in list(self._tags):
            if tag["name"] in kept:
                continue
            if tag["name"] not in used:
                self._tags.remove(tag)
            elif tag["autocomplete"]:
                tag["autocomplete"] = False
            else:
                continue
            self._index_dirty = True
            changes = True

        return changes

    # --- categories ---

    def __get_categories(self):
        self._refresh_index()
        return [dict(category) for category in
                sorted(self._categories, key=lambda category: category["name"].lower())]

    def __get_category_id(self, name):
        """Return category id from its name.

        0 means none found.
        """
        self._refresh_index()
        return self._find_category_id(name)

    @_mutator
    def __add_category(self, name):
        self._check_names(category=name)
        return self._find_category_id(name) or self._new_category(name)

    @_mutator
    def __update_category(self, id, name):
        old_name = self._category_name(id)
        if id == UNSORTED_ID or not old_name:
            # ignore unsorted, if that was somehow triggered
            return
        self._check_names(category=name)
        for category in self._categories:
            if category["id"] == id:
                category["name"] = name
        self._index_dirty = True

        def rename(fact):
            fact.category = name
        self._rewrite_facts(lambda fact: fact.category.lower() == old_name.lower(), rename)

    @_mutator
    def __remove_category(self, id):
        """move all activities to unsorted and remove category"""
        name = self._category_name(id)
        if id == UNSORTED_ID or not name:
            return
        for activity in [activity for activity in self._activities
                         if activity["category_id"] == id]:
            self._move_activity(activity, UNSORTED_ID)
        self._categories[:] = [category for category in self._categories
                               if category["id"] != id]
        self._index_dirty = True

    # --- activities ---

    def _move_activity(self, activity, category_id, name=None):
        """Rename and/or change the category of an activity, facts included.

        If the destination exists already, the facts go to it.
        """
        old_name = activity["name"]
        old_category = self._category_name(activity["category_id"])
        name = name or old_name
        category = self._category_name(category_id)

        existing = self._find_activity(name, category_id)
        if existing and existing is not activity:
            self._activities.remove(activity)
            name = existing["name"]
        else:
            activity["name"] = name
            activity["category_id"] = category_id
        self._index_dirty = True

        def move(fact):
            fact.activity = name
            fact.category = category
        self._rewrite_facts(lambda fact: (fact.activity.lower() == old_name.lower()
                                          and fact.category.lower() == old_category.lower()),
                            move)

    @_mutator
    def __update_activity(self, id, name, category_id):
        activity = self._activity(id)
        if activity is None:
            logger.warning("no activity with id {}".format(id))
            return
        category_id = category_id or UNSORTED_ID
        self._check_names(activity=name, category=self._category_name(category_id))
        self._move_activity(activity, category_id, name)

    @_mutator
    def __change_category(self, id, category_id):
        """Change the category of an activity.

        If an activity already exists with the same name
        in the target category,
        make all relevant facts use this target activity.

        Args:
            id (int): id of the source activity
            category_id (int): id of the target category
        Returns:
            boolean: whether the storage was changed.
        """
        activity = self._activity(id)
        category_id = category_id or UNSORTED_ID
        if activity is None or activity["category_id"] == category_id:
            return False
        self._move_activity(activity, category_id)
        return True

    def __get_activity_by_name(self, name, category_id=None, resurrect=True):
        """Get most recent, preferably not deleted activity by it's name.
        If category_id is None or 0, show all activities matching name.
        Otherwise, filter on the specified category.
        """
        self._refresh_index()
        activity = self._find_activity(name, category_id)
        if not activity:
            return None

        res = dict(id=activity["id"],
                   name=activity["name"],
                   deleted=activity["deleted"],
                   category=(self._category_name(activity["category_id"])
                             or self._unsorted_localized))

        # if the activity was marked as deleted, resurrect on first call
        if activity["deleted"] and resurrect:
            self.__resurrect_activity(activity)

        return res

    @_mutator
    def __resurrect_activity(self, activity):
        activity["deleted"] = False
        self._index_dirty = True

    @_mutator
    def __add_activity(self, name, category_id=None, temporary=False):
        # first check that we don't have anything like that yet
        activity = self.__get_activity_by_name(name, category_id)
        if activity:
            return activity['id']

        # now do the create bit
        category_id = category_id or UNSORTED_ID
        self._check_names(activity=name, category=self._category_name(category_id))
        return self._new_activity(name, category_id, deleted=bool(temporary))["id"]

    @_mutator
    def __remove_activity(self, id):
        """ check if we have any facts with this activity and behave accordingly
            if there are facts - sets activity to deleted = True
            else, just remove it"""
        activity = self._activity(id)
        if activity is None:
            return
        if id in self._used_activity_ids():
            activity["deleted"] = True
        else:
            self._activities.remove(activity)
        self._index_dirty = True

    def __get_category_activities(self, category_id):
        """returns list of activities, ordered by name"""
        self._refresh_index()
        category = self._category_name(category_id) or None
        return [dict(id=activity["id"], name=activity["name"],
                     category_id=activity["category_id"], category=category)
                for activity in sorted(self._activities,
                                       key=lambda activity: activity["name"].lower())
                if activity["category_id"] == category_id and not activity["deleted"]]

    def __get_activities(self, search):
        """returns list of activities for autocomplete, most recently used first"""
        self._refresh_index()
        self._query(*dt.Range.today())  # so the recent ones are known
        search = search.lower()
        found = [activity for activity in self._activities
                 if not activity["deleted"] and activity["name"].lower().startswith(search)]
        # most recent first, then by name; the never used ones at the end
        found.sort(key=lambda activity: activity["name"].lower())
        found.sort(key=lambda activity: (activity["id"] in self._last_used,
                                         self._last_used.get(activity["id"])),
                   reverse=True)
        return [dict(name=activity["name"],
                     category=self._category_name(activity["category_id"]) or None)
                for activity in found[:50]]
