import sys, os.path
# a convoluted line to add hamster module to absolute path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "../src")))

import io
import json
import shutil
import tempfile
import types
import unittest

from hamster.lib import datetime as dt

CONFIGURATION = "hamster.lib.configuration"
_real_configuration = None
db = None


def setUpModule():
    # The real configuration needs the installed GSettings schema
    # and a D-Bus session. All that the tools want is the day start.
    global _real_configuration, db
    _real_configuration = sys.modules.get(CONFIGURATION)
    stub = types.ModuleType(CONFIGURATION)
    stub.conf = types.SimpleNamespace(day_start=dt.time(5, 30))
    sys.modules[CONFIGURATION] = stub
    from hamster.storage import db


def tearDownModule():
    if _real_configuration is None:
        del sys.modules[CONFIGURATION]
    else:
        sys.modules[CONFIGURATION] = _real_configuration


from hamster.mcpserver import (
    HamsterTools,
    McpServer,
    PROTOCOL_VERSIONS,
    ToolError,
    UNCATEGORIZED,
    parse_datetime,
)
from hamster.storage import markdown


class ToolsTests(object):
    """The tools, on a real storage (the backend has the client API)."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="hamster-test-")
        self.addCleanup(shutil.rmtree, self.directory)
        self.tools = HamsterTools(self.open())

    def call(self, tool, **arguments):
        return self.tools.call(tool, arguments)

    def add_coding(self, **overrides):
        arguments = dict(activity="coding", category="work",
                         description="the MCP server", tags=["hamster"],
                         start="2020-03-10 09:00", end="2020-03-10 10:30")
        arguments.update(overrides)
        return self.call("add_fact", **arguments)["fact"]

    # categories

    def test_categories(self):
        work = self.call("add_category", name="work")
        self.assertTrue(work["created"])
        work = work["category"]
        self.assertEqual(work["name"], "work")
        again = self.call("add_category", name="Work")
        self.assertEqual(again, {"category": work, "created": False})
        home = self.call("add_category", name="home")["category"]
        self.assertEqual(self.call("list_categories"),
                         {"categories": [home, work]})

        renamed = self.call("update_category", id=work["id"], name="job")
        self.assertEqual(renamed["category"], {"id": work["id"], "name": "job"})
        with self.assertRaisesRegex(ToolError, "already a category"):
            self.call("update_category", id=work["id"], name="home")

        self.call("add_activity", name="coding", category_id=work["id"])
        deleted = self.call("delete_category", id=work["id"])
        self.assertEqual(deleted["uncategorized_activities"], ["coding"])
        self.assertEqual(self.call("list_categories"), {"categories": [home]})
        activity = self.call("list_activities")["activities"][0]
        self.assertEqual(activity["category_id"], UNCATEGORIZED)

    def test_category_errors(self):
        with self.assertRaisesRegex(ToolError, "comma"):
            self.call("add_category", name="a, b")
        with self.assertRaisesRegex(ToolError, "empty"):
            self.call("add_category", name="  ")
        with self.assertRaisesRegex(ToolError, "no category with id 42"):
            self.call("update_category", id=42, name="x")
        with self.assertRaisesRegex(ToolError, "cannot be changed"):
            self.call("delete_category", id=UNCATEGORIZED)

    # activities

    def test_activities(self):
        work = self.call("add_category", name="work")["category"]
        coding = self.call("add_activity", name="coding", category_id=work["id"])
        self.assertTrue(coding["created"])
        coding = coding["activity"]
        self.assertEqual(coding["name"], "coding")
        self.assertEqual(coding["category"], "work")
        self.assertFalse(self.call("add_activity", name="Coding",
                                   category_id=work["id"])["created"])
        lunch = self.call("add_activity", name="lunch")["activity"]
        self.assertEqual(lunch["category_id"], UNCATEGORIZED)

        self.assertEqual(self.call("list_activities")["activities"], [coding, lunch])
        self.assertEqual(self.call("list_activities", category_id=work["id"]),
                         {"activities": [coding]})
        self.assertEqual(self.call("list_activities", search="UNC"),
                         {"activities": [lunch]})

        moved = self.call("update_activity", id=lunch["id"], name="dinner",
                          category_id=work["id"])["activity"]
        self.assertEqual((moved["name"], moved["category"]), ("dinner", "work"))
        with self.assertRaisesRegex(ToolError, "already an activity"):
            self.call("update_activity", id=lunch["id"], name="coding")

        self.assertEqual(self.call("delete_activity", id=coding["id"]),
                         {"deleted": coding})
        self.assertEqual(self.call("list_activities")["activities"], [moved])

    def test_activity_errors(self):
        with self.assertRaisesRegex(ToolError, "no category with id 7"):
            self.call("add_activity", name="coding", category_id=7)
        with self.assertRaisesRegex(ToolError, "no activity with id 7"):
            self.call("delete_activity", id=7)

    # facts

    def test_facts(self):
        fact = self.add_coding()
        self.assertEqual(fact["activity"], "coding")
        self.assertEqual(fact["category"], "work")
        self.assertEqual(fact["description"], "the MCP server")
        self.assertEqual(fact["tags"], ["hamster"])
        self.assertEqual((fact["start"], fact["end"]),
                         ("2020-03-10 09:00", "2020-03-10 10:30"))
        self.assertEqual(fact["duration_minutes"], 90)
        self.assertFalse(fact["ongoing"])
        self.assertEqual(self.call("get_fact", id=fact["id"]), {"fact": fact})

        listed = self.call("list_facts", start_date="2020-03-10")
        self.assertEqual(listed["facts"], [fact])
        self.assertEqual(listed["total_minutes"], 90)
        self.assertEqual(self.call("list_facts", start_date="2020-03-11")["facts"], [])
        self.assertEqual(self.call("list_facts", start_date="2020-03-09",
                                   end_date="2020-03-11", search="hamster")["facts"],
                         [fact])

        # the activity and the category were created by the fact
        activity = self.call("list_activities")["activities"]
        self.assertEqual([(a["name"], a["category"]) for a in activity],
                         [("coding", "work")])

        updated = self.call("update_fact", id=fact["id"], description="",
                            end="2020-03-10 11:00")
        self.assertEqual(updated["previous_id"], fact["id"])
        new = updated["fact"]
        self.assertEqual(new["description"], "")
        self.assertEqual(new["duration_minutes"], 120)
        self.assertEqual(new["tags"], ["hamster"])
        self.assertEqual(self.call("list_facts", start_date="2020-03-10")["facts"], [new])

        self.assertEqual(self.call("delete_fact", id=new["id"]), {"deleted": new})
        with self.assertRaisesRegex(ToolError, "no fact with id"):
            self.call("get_fact", id=new["id"])
        self.assertEqual(self.call("list_facts", start_date="2020-03-10")["facts"], [])

    def test_fact_errors(self):
        with self.assertRaisesRegex(ToolError, "negative"):
            self.add_coding(end="2020-03-10 08:00")
        with self.assertRaisesRegex(ToolError, "start: cannot read"):
            self.add_coding(start="next tuesday")
        with self.assertRaisesRegex(ToolError, "empty"):
            self.add_coding(activity=" ")
        with self.assertRaisesRegex(ToolError, "before start_date"):
            self.call("list_facts", start_date="2020-03-10", end_date="2020-03-09")
        with self.assertRaisesRegex(ToolError, "expected 'YYYY-MM-DD'"):
            self.call("list_facts", start_date="10/03/2020")
        with self.assertRaisesRegex(ToolError, "no fact with id 99"):
            self.call("update_fact", id=99, description="x")

    def test_ongoing_fact(self):
        self.assertIsNone(self.call("stop_tracking")["stopped"])
        fact = self.call("add_fact", activity="writing", start="-1")["fact"]
        self.assertTrue(fact["ongoing"])
        self.assertIsNone(fact["end"])
        self.assertEqual(self.call("list_facts")["facts"], [fact])

        stopped = self.call("stop_tracking")["stopped"]
        self.assertEqual(stopped["id"], fact["id"])
        self.assertFalse(stopped["ongoing"])
        self.assertIsNone(self.call("stop_tracking")["stopped"])

        reopened = self.call("update_fact", id=fact["id"], end=None)["fact"]
        self.assertTrue(reopened["ongoing"])

    # arguments

    def test_arguments(self):
        with self.assertRaisesRegex(ToolError, "unknown argument.*colour"):
            self.call("list_categories", colour="blue")
        with self.assertRaisesRegex(ToolError, "missing argument.*name"):
            self.call("add_category")
        with self.assertRaisesRegex(ToolError, "id must be of type integer"):
            self.call("get_fact", id=True)
        with self.assertRaisesRegex(ToolError, "tags must be of type array"):
            self.add_coding(tags=["ok", 3])
        with self.assertRaises(KeyError):
            self.call("drop_database")


class TestMarkdownTools(ToolsTests, unittest.TestCase):
    def open(self):
        return markdown.Storage(unsorted_localized="", database_dir=self.directory)


class TestSqliteTools(ToolsTests, unittest.TestCase):
    def open(self):
        return db.Storage(database_dir=self.directory)


class TestParseDatetime(unittest.TestCase):
    def test_formats(self):
        expected = dt.datetime(2020, 3, 10, 9, 5)
        for text in ("2020-03-10 09:05", "2020-03-10T09:05",
                     "2020-03-10T09:05:42", " 2020-03-10 09:05 "):
            self.assertEqual(parse_datetime(text), expected, text)

    def test_time_only_is_today(self):
        value = parse_datetime("09:05")
        self.assertEqual((value.hour, value.minute), (9, 5))
        self.assertEqual(value.hday(), dt.hday.today())

    def test_relative(self):
        delta = dt.datetime.now() - parse_datetime("-15")
        self.assertEqual(delta, dt.timedelta(minutes=15))

    def test_garbage(self):
        for text in ("tomorrow", "2020-03-10 09:05 and more", ""):
            with self.assertRaises(ToolError, msg=text):
                parse_datetime(text)


class FakeTools(HamsterTools):
    def __init__(self):
        pass

    def list_categories(self):
        return {"categories": [{"id": 1, "name": "wörk"}]}

    def get_fact(self, id):
        raise ToolError("there is no fact with id {}".format(id))

    def delete_fact(self, id):
        raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.server = McpServer(FakeTools(), version="1.2.3")

    def request(self, method, params=None, id=1):
        message = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            message["params"] = params
        return self.server.handle(message)

    def test_initialize(self):
        response = self.request("initialize", {"protocolVersion": "2025-06-18",
                                               "capabilities": {},
                                               "clientInfo": {"name": "test"}})
        result = response["result"]
        self.assertEqual(response["id"], 1)
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["version"], "1.2.3")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("facts", result["instructions"])

        result = self.request("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSIONS[0])

    def test_tools_list(self):
        tools = self.request("tools/list")["result"]["tools"]
        self.assertEqual(sorted(tool["name"] for tool in tools), sorted([
            "list_facts", "get_fact", "add_fact", "update_fact", "delete_fact",
            "stop_tracking",
            "list_activities", "add_activity", "update_activity", "delete_activity",
            "list_categories", "add_category", "update_category", "delete_category",
            ]))
        for tool in tools:
            self.assertTrue(tool["description"], tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object")
            annotations = tool["annotations"]
            read_only = tool["name"].startswith(("list_", "get_"))
            self.assertEqual(annotations["readOnlyHint"], read_only, tool["name"])
            self.assertEqual(annotations["destructiveHint"],
                             tool["name"].startswith("delete_"), tool["name"])

    def test_tools_call(self):
        result = self.request("tools/call", {"name": "list_categories",
                                             "arguments": {}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"],
                         {"categories": [{"id": 1, "name": "wörk"}]})
        self.assertEqual(json.loads(result["content"][0]["text"]),
                         result["structuredContent"])

    def test_tool_errors(self):
        result = self.request("tools/call", {"name": "get_fact",
                                             "arguments": {"id": 4}})["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "there is no fact with id 4")

        result = self.request("tools/call", {"name": "get_fact",
                                             "arguments": {}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("missing argument", result["content"][0]["text"])

        with self.assertLogs("hamster.mcpserver", "ERROR"):
            result = self.request("tools/call", {"name": "delete_fact",
                                                 "arguments": {"id": 4}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("ServiceUnknown", result["content"][0]["text"])

    def test_protocol_errors(self):
        response = self.request("tools/call", {"name": "nope"})
        self.assertEqual(response["error"]["code"], -32602)
        response = self.request("resources/list")
        self.assertEqual(response["error"]["code"], -32601)
        self.assertEqual(self.request("ping", id=7), {"jsonrpc": "2.0", "id": 7, "result": {}})
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0",
                                              "method": "notifications/initialized"}))
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "id": 3, "result": {}}))
        self.assertEqual(self.server.handle({"id": 1, "method": "ping"})["error"]["code"],
                         -32600)

    def test_serve(self):
        instream = io.StringIO("\n".join([
            '{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
            '',
            '{"jsonrpc": "2.0", "method": "notifications/initialized"}',
            'not json',
            '[{"jsonrpc": "2.0", "id": 2, "method": "ping"}]',
            '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", '
            '"params": {"name": "list_categories"}}',
            ]) + "\n")
        outstream = io.StringIO()
        self.server.serve(instream, outstream)
        responses = [json.loads(line) for line in outstream.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in responses], [1, None, None, 3])
        self.assertEqual(responses[1]["error"]["code"], -32700)
        self.assertEqual(responses[2]["error"]["code"], -32600)
        self.assertIn("wörk", outstream.getvalue())


if __name__ == '__main__':
    unittest.main()
