# Bay Tahmin runtime bridge: switches the existing FastAPI app to the active football data provider.
import importlib.abc
import importlib.util
import os
import sys

BACKEND_DIR = os.path.dirname(__file__)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Install the prediction consistency layer for every Python entry point,
# including historical backtests that import v6 directly rather than main.py.
try:
    import football_intelligence_agent_v6 as _intelligence_v6
    from prediction_consistency import install as _install_consistency
    _install_consistency(_intelligence_v6)
except Exception:
    pass

class _MainPatch(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    TARGETS = {"main": "main.py", "backend.main": "backend/main.py"}

    def find_spec(self, fullname, path=None, target=None):
        relative = self.TARGETS.get(fullname)
        if not relative:
            return None
        candidates = [os.path.join(os.getcwd(), relative), os.path.join(BACKEND_DIR, "main.py")]
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

        if os.getenv("FIVE_DOLLAR_API_KEY"):
            from five_dollar_bridge import patch_main
            patch_main(module)
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
            try:
                import football_intelligence_agent as intelligence_facade
                from prediction_consistency import install as install_consistency
                install_consistency(intelligence_facade._impl)
                intelligence_facade.cand = intelligence_facade._impl.cand
            except Exception:
                pass
        elif os.getenv("API_FOOTBALL_KEY") or os.getenv("APIFOOTBALL_KEY"):
            from api_football_bridge import patch_main
            patch_main(module)

sys.meta_path.insert(0, _MainPatch())
