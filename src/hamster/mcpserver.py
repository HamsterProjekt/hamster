# This file is part of Hamster
# Copyright (c) The Hamster time tracker developers
# SPDX-License-Identifier: GPL-3.0-or-later


"""Model Context Protocol (MCP) server for Hamster.

Gives MCP clients (AI assistants) create, read, update and delete access
to the facts (the tracking log), the activities and the categories.

The server talks to the running hamster-service over D-Bus, exactly like
the command line and the windows do, so it works with either storage
(SQLite or Markdown) and the open windows see the changes at once.

Transport: stdio, newline delimited JSON-RPC 2.0, as in the MCP
specification. It is implemented here, on the standard library only,
so that no extra dependency is needed by distributions or the Flatpak.
"""


import json
import logging
import sys

from datetime import date as pdt_date, datetime as pdt_datetime

import hamster
from hamster.lib import datetime as dt
from hamster.lib.fact import Fact, FactError

logger = logging.getLogger(__name__)   # noqa: E402


# newest first; the first one is offered to clients that ask for another
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

# category_id of the activities that have no category
UNCATEGORIZED = -1

SERVER_NAME = "hamster"

INSTRUCTIONS = """\
Hamster is a time tracker. Its tracking log is made of facts: an activity,
with an optional category, description and tags, done from a start time to
an end time. A fact without an end is the one being tracked right now;
adding a fact without an end starts tracking it and stops the previous one.

Activities are the names that can be tracked, each in at most one category.
Facts refer to activities and categories by name: adding a fact with a new
activity or category name creates it.

Times are local, written "YYYY-MM-DD HH:MM" (seconds are dropped).
"HH:MM" alone means today, "-15" means 15 minutes ago.
Days are hamster days, which start at the "day start" of the preferences
(e.g. 05:00), so that late evenings belong to the day before.

Updating a fact gives it a new id: use the id that update_fact returns.
"""


class ToolError(Exception):
    """A tool call that cannot be done.

    Reported to the client as a tool result with isError,
    so that the assistant can read the message and correct itself.
    """


# --- conversions ---

def format_datetime(value):
    return str(value) if value else None


def parse_datetime(text, name="time"):
    """Datetime from ISO 8601 or from the hamster syntax ("HH:MM", "-15")."""
    text = text.strip()
    try:
        value = pdt_datetime.fromisoformat(text[:-1] + "+00:00"
                                           if text.endswith("Z") else text)
    except ValueError:
        value = None
    if value is not None:
        if value.tzinfo is not None:
            # hamster stores naive local times
            value = value.astimezone().replace(tzinfo=None)
        return dt.datetime.from_pdt(value)

    match = dt.datetime.re.search(text)
    if match and match.group("whole") == text:
        value = dt.datetime.parse(text, default_day=dt.hday.today())
        if isinstance(value, dt.timedelta):
            # relative, in minutes from now
            return dt.datetime.now() + value
        if value:
            return value
    raise ToolError("{}: cannot read '{}', expected 'YYYY-MM-DD HH:MM'"
                    .format(name, text))


def parse_day(text, name="date"):
    """Hamster day from "YYYY-MM-DD"."""
    try:
        return dt.hday.from_pdt(pdt_date.fromisoformat(text.strip()))
    except ValueError:
        raise ToolError("{}: cannot read '{}', expected 'YYYY-MM-DD'"
                        .format(name, text))


def fact_to_dict(fact):
    return {
        "id": fact.id,
        "activity": fact.activity,
        "category": fact.category or "",
        "description": fact.description or "",
        "tags": [tag.strip() for tag in fact.tags],
        "start": format_datetime(fact.start_time),
        "end": format_datetime(fact.end_time),
        "ongoing": fact.end_time is None,
        "duration_minutes": int(fact.delta.total_seconds() // 60),
        "activity_id": fact.activity_id,
    }


def activity_to_dict(row):
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "category_id": int(row["category_id"]),
        "category": str(row["category"] or ""),
    }


def category_to_dict(row):
    return {"id": int(row["id"]), "name": str(row["name"])}


# --- tool definitions ---

_TOOLS = []


def tool(description, properties=None, required=(),
         read_only=False, destructive=False, idempotent=False):
    """Declare a HamsterTools method as an MCP tool (same name)."""
    def decorator(method):
        _TOOLS.append({
            "name": method.__name__,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
                "additionalProperties": False,
            },
            "annotations": {
                "title": method.__name__.replace("_", " ").capitalize(),
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": idempotent,
                "openWorldHint": False,
            },
        })
        return method
    return decorator


def _id(what):
    return {"type": "integer", "description": "Id of the {}.".format(what)}


def _string(description):
    return {"type": "string", "description": description}


_TAGS = {"type": "array", "items": {"type": "string"},
         "description": "Tags, without '#'."}
_CATEGORY_ID = {"type": "integer",
                "description": "Id of the category, {} for none."
                               .format(UNCATEGORIZED)}


def check_arguments(schema, arguments):
    """Validate the arguments against the (simple) input schema."""
    if not isinstance(arguments, dict):
        raise ToolError("arguments must be an object")
    properties = schema["properties"]
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise ToolError("unknown argument(s): {}".format(", ".join(unknown)))
    missing = [key for key in schema["required"] if key not in arguments]
    if missing:
        raise ToolError("missing argument(s): {}".format(", ".join(missing)))
    for key, value in arguments.items():
        types = properties[key]["type"]
        types = types if isinstance(types, list) else [types]
        if not any(_has_type(value, t, properties[key]) for t in types):
            raise ToolError("{} must be of type {}".format(key, " or ".join(types)))


def _has_type(value, type_name, spec):
    if type_name == "null":
        return value is None
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "array":
        item_type = spec["items"]["type"]
        return isinstance(value, list) and all(_has_type(item, item_type, None)
                                               for item in value)
    return False


def _name(value, what):
    value = value.strip()
    if not value:
        raise ToolError("the {} name cannot be empty".format(what))
    return value


class HamsterTools(object):
    """The MCP tools, on top of a hamster.client.Storage."""

    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def definitions():
        return list(_TOOLS)

    def call(self, name, arguments):
        definition = next((t for t in _TOOLS if t["name"] == name), None)
        if definition is None:
            raise KeyError(name)
        arguments = arguments or {}
        check_arguments(definition["inputSchema"], arguments)
        return getattr(self, name)(**arguments)

    # --- facts ---

    @tool("List the facts (time tracking entries) of a span of hamster days, "
          "oldest first, with their total duration.",
          {"start_date": _string("First day, YYYY-MM-DD. Default: today."),
           "end_date": _string("Last day, YYYY-MM-DD, included. "
                               "Default: same as start_date."),
           "search": _string("Only facts matching these terms, in activity, "
                             "category, description or tags. Space means AND, "
                             "comma means OR, a leading 'not ' inverts.")},
          read_only=True, idempotent=True)
    def list_facts(self, start_date=None, end_date=None, search=""):
        start = parse_day(start_date, "start_date") if start_date else dt.hday.today()
        end = parse_day(end_date, "end_date") if end_date else start
        if end < start:
            raise ToolError("end_date is before start_date")
        facts = [fact_to_dict(fact)
                 for fact in self.storage.get_facts(start, end, search)]
        return {
            "start_date": str(start),
            "end_date": str(end),
            "facts": facts,
            "total_minutes": sum(fact["duration_minutes"] for fact in facts),
        }

    @tool("Get one fact by id.",
          {"id": _id("fact")}, ["id"],
          read_only=True, idempotent=True)
    def get_fact(self, id):
        return {"fact": fact_to_dict(self._fact(id))}

    @tool("Add a fact. Without end, it is tracked from start until stopped "
          "(and the fact being tracked, if any, is stopped).",
          {"activity": _string("Activity name. Created if it does not exist."),
           "category": _string("Category name. Created if it does not exist."),
           "description": _string("Free text."),
           "tags": _TAGS,
           "start": _string("Start, YYYY-MM-DD HH:MM. Default: now."),
           "end": _string("End, YYYY-MM-DD HH:MM. Default: none (ongoing).")},
          ["activity"])
    def add_fact(self, activity, category=None, description=None, tags=None,
                 start=None, end=None):
        fact = Fact(activity=_name(activity, "activity"),
                    category=category,
                    description=description,
                    tags=tags,
                    start=parse_datetime(start, "start") if start else dt.datetime.now(),
                    end=parse_datetime(end, "end") if end else None)
        new_id = self._save(fact)
        return {"fact": fact_to_dict(self._fact(new_id))}

    @tool("Change a fact. Only the given fields change. "
          "The fact gets a new id, which is returned.",
          {"id": _id("fact"),
           "activity": _string("Activity name."),
           "category": _string("Category name, '' for none."),
           "description": _string("Free text, '' to clear."),
           "tags": _TAGS,
           "start": _string("Start, YYYY-MM-DD HH:MM."),
           "end": {"type": ["string", "null"],
                   "description": "End, YYYY-MM-DD HH:MM; null to make the fact ongoing."}},
          ["id"], idempotent=False)
    def update_fact(self, id, **changes):
        fact = self._fact(id)
        if "activity" in changes:
            fact.activity = _name(changes["activity"], "activity")
        if "category" in changes:
            fact.category = changes["category"]
        if "description" in changes:
            fact.description = changes["description"]
        if "tags" in changes:
            fact.tags = changes["tags"]
        if "start" in changes:
            fact.start_time = parse_datetime(changes["start"], "start")
        if "end" in changes:
            end = changes["end"]
            fact.end_time = parse_datetime(end, "end") if end else None
        new_id = self._save(fact, replace_id=id)
        return {"previous_id": id, "fact": fact_to_dict(self._fact(new_id))}

    @tool("Delete a fact. This cannot be undone.",
          {"id": _id("fact")}, ["id"],
          destructive=True, idempotent=True)
    def delete_fact(self, id):
        fact = self._fact(id)
        self.storage.remove_fact(id)
        return {"deleted": fact_to_dict(fact)}

    @tool("Stop tracking the ongoing fact, if any.",
          {"end": _string("End, YYYY-MM-DD HH:MM. Default: now.")})
    def stop_tracking(self, end=None):
        ongoing = [fact for fact in self.storage.get_todays_facts()
                   if not fact.end_time]
        if not ongoing:
            return {"stopped": None, "message": "nothing is being tracked"}
        fact = ongoing[-1]
        end_time = parse_datetime(end, "end") if end else dt.datetime.now()
        if end_time < fact.start_time:
            raise ToolError("end {} is before the start of the ongoing fact ({})"
                            .format(end_time, fact.start_time))
        self.storage.stop_tracking(end_time)
        return {"stopped": fact_to_dict(self._fact(fact.id))}

    def _fact(self, id):
        try:
            fact = self.storage.get_fact(id)
        except Exception as error:
            logger.debug("get_fact(%s): %s", id, error)
            fact = None
        if not fact or fact.id != id:
            raise ToolError("there is no fact with id {}".format(id))
        return fact

    def _save(self, fact, replace_id=None):
        try:
            self.storage.check_fact(fact)
        except FactError as error:
            raise ToolError(str(error))
        if replace_id is None:
            new_id = self.storage.add_fact(fact)
        else:
            new_id = self.storage.update_fact(replace_id, fact)
        if not new_id:
            raise ToolError("hamster did not save the fact")
        return int(new_id)

    # --- activities ---

    @tool("List the activities, by category. Activities without category "
          "have category_id {}.".format(UNCATEGORIZED),
          {"category_id": {"type": "integer",
                           "description": "Only the activities of this category "
                                          "({} for those without).".format(UNCATEGORIZED)},
           "search": _string("Only the activities whose name contains this text.")},
          read_only=True, idempotent=True)
    def list_activities(self, category_id=None, search=""):
        if category_id is None:
            activities = self._activities()
        else:
            self._category(category_id)
            activities = self._activities([category_id])
        search = search.strip().lower()
        if search:
            activities = [a for a in activities if search in a["name"].lower()]
        return {"activities": activities}

    @tool("Add an activity. If it exists already in that category, "
          "it is returned instead.",
          {"name": _string("Activity name."), "category_id": _CATEGORY_ID},
          ["name"], idempotent=True)
    def add_activity(self, name, category_id=UNCATEGORIZED):
        name = _name(name, "activity")
        self._category(category_id)
        existing = self._find_activity(name, category_id)
        if existing:
            return {"activity": existing, "created": False}
        new_id = self.storage.add_activity(name, category_id)
        return {"activity": self._activity(int(new_id)), "created": True}

    @tool("Rename an activity and/or move it to another category. "
          "Its facts follow.",
          {"id": _id("activity"),
           "name": _string("New name."),
           "category_id": _CATEGORY_ID},
          ["id"], idempotent=True)
    def update_activity(self, id, name=None, category_id=None):
        activity = self._activity(id)
        name = activity["name"] if name is None else _name(name, "activity")
        if category_id is None:
            category_id = activity["category_id"]
        self._category(category_id)
        clash = self._find_activity(name, category_id)
        if clash and clash["id"] != id:
            raise ToolError("there is already an activity '{}' in that category (id {})"
                            .format(clash["name"], clash["id"]))
        self.storage.update_activity(id, name, category_id)
        return {"activity": self._activity(id)}

    @tool("Delete an activity. Its facts are kept; the activity just "
          "disappears from the lists and the autocompletion.",
          {"id": _id("activity")}, ["id"],
          destructive=True, idempotent=True)
    def delete_activity(self, id):
        activity = self._activity(id)
        self.storage.remove_activity(id)
        return {"deleted": activity}

    def _activities(self, category_ids=None):
        if category_ids is None:
            category_ids = [c["id"] for c in self._categories()] + [UNCATEGORIZED]
        activities = []
        for category_id in category_ids:
            activities += [activity_to_dict(row) for row in
                           self.storage.get_category_activities(category_id)]
        return activities

    def _activity(self, id):
        for activity in self._activities():
            if activity["id"] == id:
                return activity
        raise ToolError("there is no activity with id {}".format(id))

    def _find_activity(self, name, category_id):
        for activity in self._activities([category_id]):
            if activity["name"].lower() == name.lower():
                return activity
        return None

    # --- categories ---

    @tool("List the categories.", read_only=True, idempotent=True)
    def list_categories(self):
        return {"categories": self._categories()}

    @tool("Add a category. If it exists already, it is returned instead.",
          {"name": _string("Category name.")}, ["name"], idempotent=True)
    def add_category(self, name):
        name = self._category_name(name)
        existing = self._find_category(name)
        if existing:
            return {"category": existing, "created": False}
        new_id = int(self.storage.add_category(name))
        return {"category": self._category(new_id), "created": True}

    @tool("Rename a category. Its facts follow.",
          {"id": _id("category"), "name": _string("New name.")},
          ["id", "name"], idempotent=True)
    def update_category(self, id, name):
        self._category(id, allow_uncategorized=False)
        name = self._category_name(name)
        clash = self._find_category(name)
        if clash and clash["id"] != id:
            raise ToolError("there is already a category '{}' (id {})"
                            .format(clash["name"], clash["id"]))
        self.storage.update_category(id, name)
        return {"category": self._category(id)}

    @tool("Delete a category. Its activities, and their facts, "
          "are moved to no category.",
          {"id": _id("category")}, ["id"],
          destructive=True, idempotent=True)
    def delete_category(self, id):
        category = self._category(id, allow_uncategorized=False)
        moved = self._activities([id])
        self.storage.remove_category(id)
        return {"deleted": category,
                "uncategorized_activities": [a["name"] for a in moved]}

    def _categories(self):
        return [category_to_dict(row) for row in self.storage.get_categories()]

    def _category(self, id, allow_uncategorized=True):
        if id == UNCATEGORIZED:
            if allow_uncategorized:
                return {"id": UNCATEGORIZED, "name": ""}
            raise ToolError("'no category' ({}) cannot be changed".format(UNCATEGORIZED))
        for category in self._categories():
            if category["id"] == id:
                return category
        raise ToolError("there is no category with id {}".format(id))

    def _find_category(self, name):
        for category in self._categories():
            if category["name"].lower() == name.lower():
                return category
        return None

    @staticmethod
    def _category_name(name):
        name = _name(name, "category")
        if "," in name:
            raise ToolError("a category name cannot contain a comma")
        return name


# --- JSON-RPC over stdio ---

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class McpServer(object):
    """Minimal MCP server: lifecycle, ping and tools."""

    def __init__(self, tools, version=None):
        self.tools = tools
        self.version = version or hamster.__version__

    def handle(self, message):
        """Answer one JSON-RPC message; None for notifications and responses."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in message:
            # a response; this server sends no requests
            return None
        notification = "id" not in message
        request_id = message.get("id")
        method = message["method"]
        params = message.get("params") or {}

        if method.startswith("notifications/"):
            return None
        handler = {
            "initialize": self._initialize,
            "ping": lambda params: {},
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
        }.get(method)
        if handler is None:
            return None if notification else _error(
                request_id, METHOD_NOT_FOUND, "method not found: {}".format(method))
        try:
            result = handler(params)
        except _ProtocolError as error:
            return None if notification else _error(request_id, error.code, str(error))
        return None if notification else {"jsonrpc": "2.0", "id": request_id,
                                          "result": result}

    def _initialize(self, params):
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Hamster time tracker",
                           "version": self.version},
            "instructions": INSTRUCTIONS,
        }

    def _tools_list(self, params):
        return {"tools": self.tools.definitions()}

    def _tools_call(self, params):
        name = params.get("name")
        try:
            result = self.tools.call(name, params.get("arguments"))
        except KeyError:
            raise _ProtocolError(INVALID_PARAMS, "unknown tool: {}".format(name))
        except ToolError as error:
            return _tool_error(str(error))
        except Exception as error:
            logger.exception("tool %s failed", name)
            message = getattr(error, "get_dbus_message", lambda: None)() or str(error)
            return _tool_error("hamster-service: {}".format(message.strip()))
        return {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False, indent=1)}],
            "structuredContent": result,
            "isError": False,
        }

    def serve(self, instream, outstream):
        """Read requests line by line until the end of instream."""
        for line in instream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response = _error(None, PARSE_ERROR, "parse error")
            else:
                if isinstance(message, list):
                    # batches were removed from the protocol (2025-06-18)
                    response = _error(None, INVALID_REQUEST, "batches are not supported")
                else:
                    response = self.handle(message)
            if response is not None:
                outstream.write(json.dumps(response, ensure_ascii=False) + "\n")
                outstream.flush()


class _ProtocolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _tool_error(message):
    return {"content": [{"type": "text", "text": message}], "isError": True}


def main(argv=None):
    import argparse
    import io

    parser = argparse.ArgumentParser(
        description="Hamster MCP server (stdio): gives AI assistants access to "
                    "the facts, activities and categories of hamster-service.")
    parser.add_argument("--log", dest="log_level",
                        choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
                        default='WARNING',
                        help="Set the logging level, on stderr (default: %(default)s)")
    args = parser.parse_args(argv)

    # stdout carries the protocol; the hamster logger writes to stderr
    hamster.logger.setLevel(args.log_level)

    from hamster.client import Storage
    server = McpServer(HamsterTools(Storage()))
    instream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    outstream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    try:
        server.serve(instream, outstream)
    except KeyboardInterrupt:
        pass
