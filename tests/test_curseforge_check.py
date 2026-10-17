import importlib.util
import sys
import types
import unittest
from unittest.mock import AsyncMock
from pathlib import Path


class CurseForgeCheckTests(unittest.TestCase):
    def test_stale_file_does_not_replace_newer_saved_file(self):
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")
        sys.modules["bs4"] = types.ModuleType("bs4")
        sys.modules["bs4"].BeautifulSoup = object
        sys.modules["config"] = types.SimpleNamespace(CURSEFORGE_CHANNEL_ID=1)
        discord = types.ModuleType("discord")
        discord.ext = types.ModuleType("discord.ext")
        discord.ext.tasks = types.SimpleNamespace(loop=lambda **_: lambda fn: fn)
        sys.modules["discord"] = discord
        sys.modules["discord.ext"] = discord.ext

        spec = importlib.util.spec_from_file_location(
            "curseforge_check_under_test", Path(__file__).resolve().parents[1] / "curseforge_check.py"
        )
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def json(self):
                return {"files": [{"id": 199}, {"id": 201}, {"id": 200}]}

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def get(self, *args, **kwargs):
                return Response()

        sys.modules["aiohttp"].ClientSession = Session
        sys.modules["aiohttp"].ClientTimeout = lambda **kwargs: None
        with self.assertRaises(StopIteration) as result:
            checker._fetch_cfwidget_latest_file().send(None)
        self.assertEqual(result.exception.value[0], "201")

        state = {"last_file_id": "200"}
        announcements = []
        checker._load_cf_state = lambda: state
        checker._save_cf_state = lambda data: None
        checker._announce = AsyncMock(side_effect=lambda *args: announcements.append(args))

        async def run():
            for file_id in ("199", "200", "201", "199"):
                checker._fetch_cfwidget_latest_file = AsyncMock(
                    return_value=(file_id, f"https://example.com/{file_id}", "Release")
                )
                await checker.check_curseforge(None, 1)

        with self.assertRaises(StopIteration):
            run().send(None)
        self.assertEqual(state["last_file_id"], "201")
        self.assertEqual(len(announcements), 1)


if __name__ == "__main__":
    unittest.main()
