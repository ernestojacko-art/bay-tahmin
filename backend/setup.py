from pathlib import Path
import site

from setuptools import setup

ROOT = Path(__file__).resolve().parent
runtime = ROOT / "bay_runtime.py"
for target in site.getsitepackages():
    try:
        target_path = Path(target)
        (target_path / "bay_runtime.py").write_bytes(runtime.read_bytes())
        (target_path / "bay_tahmin_runtime.pth").write_text("import bay_runtime; bay_runtime.install()\n", encoding="utf-8")
        break
    except (OSError, PermissionError):
        continue

setup(name="bay-tahmin-runtime", version="1.0.0", py_modules=["bay_runtime"])
