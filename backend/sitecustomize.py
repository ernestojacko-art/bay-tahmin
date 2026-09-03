# Bay Tahmin runtime bridge: transparently switches the existing FastAPI app to API-Football.
import importlib.abc
import importlib.util
import sys


class _MainPatch(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "main":
            return None
        for p in sys.path:
            root = p or "."
            filename = root.rstrip("/") + "/main.py"
            try:
                open(filename, "rb").close()
            except OSError:
                continue
            return importlib.util.spec_from_file_location("main", filename, loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        with open(module.__spec__.origin, "rb") as fh:
            source = fh.read()
        exec(compile(source, module.__spec__.origin, "exec"), module.__dict__)
        try:
            from api_football_bridge import patch_main
            patch_main(module)
        except Exception:
            pass


sys.meta_path.insert(0, _MainPatch())
