# Bay Tahmin runtime bridge: switches the existing FastAPI app to the active football data provider.
# Supports both Render start styles: `main:app` from backend/ and `backend.main:app` from repo root.
import importlib.abc
import importlib.util
import os
import sys


BACKEND_DIR = os.path.dirname(__file__)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class _MainPatch(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    TARGETS = {"main": "main.py", "backend.main": "backend/main.py"}

    def find_spec(self, fullname, path=None, target=None):
        relative = self.TARGETS.get(fullname)
        if not relative:
            return None

        candidates = [
            os.path.join(os.getcwd(), relative),
            os.path.join(BACKEND_DIR, "main.py"),
        ]
        for filename in candidates:
            if os.path.isfile(filename):
                return importlib.util.spec_from_file_location(fullname, filename, loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        with open(module.__spec__.origin, "rb") as fh:
            source = fh.read()
        exec(compile(source, module.__spec__.origin, "exec"), module.__dict__)

        # 5DollarFootballAPI is the active provider for Bay Tahmin.
        # The date-aware Football Agent adapter is applied only after the provider bridge,
        # so all Agent operations use the same 5DollarFootballAPI data source.
        if os.getenv("FIVE_DOLLAR_API_KEY"):
            from five_dollar_bridge import patch_main
            patch_main(module)
            from agent_adapter import patch_main as patch_agent
            patch_agent(module)
        elif os.getenv("API_FOOTBALL_KEY") or os.getenv("APIFOOTBALL_KEY"):
            from api_football_bridge import patch_main
            patch_main(module)


sys.meta_path.insert(0, _MainPatch())
