import sys
import os

_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
for k in list(sys.modules.keys()):
    if k == "app" or k.startswith("app."):
        del sys.modules[k]
if _src in sys.path:
    sys.path.remove(_src)
sys.path.insert(0, _src)
