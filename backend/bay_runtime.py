import importlib.abc
import importlib.util
import os
import sys

BACKEND_DIR = os.path.dirname(__file__)

class MainLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "main":
            return None
        filename = os.path.join(BACKEND_DIR, "main.py")
        if not os.path.isfile(filename):
            return None
        return importlib.util.spec_from_file_location(fullname, filename, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        filename = module.__spec__.origin
        with open(filename, "rb") as fh:
            source = fh.read()
        exec(compile(source, filename, "exec"), module.__dict__)
        if os.getenv("FIVE_DOLLAR_API_KEY"):
            from five_dollar_bridge import patch_main as patch_five
            patch_five(module)
            import agent_adapter
            from agent_adapter import patch_main as patch_agent
            patch_agent(module)
            try:
                from iyms_fallback import build as build_iyms
                agent_adapter.build_iyms_candidates = lambda item, surprise=False: build_iyms(item, agent_adapter.market_probability, agent_adapter.find_market, surprise)
            except Exception:
                pass
            from admin_api import patch_main as patch_admin
            patch_admin(module)
        elif os.getenv("API_FOOTBALL_KEY") or os.getenv("APIFOOTBALL_KEY"):
            from api_football_bridge import patch_main
            patch_main(module)


def install():
    if not any(isinstance(x, MainLoader) for x in sys.meta_path):
        sys.meta_path.insert(0, MainLoader())
