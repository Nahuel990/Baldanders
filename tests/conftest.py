"""Keep tests away from the user's real ~/.baldanders.

A model already downloaded there is reused (read-only) so the NER
integration tests can run.
"""

import os
import tempfile
from pathlib import Path

_real_home = Path(os.environ.get("BALDANDERS_HOME", Path.home() / ".baldanders"))
_test_home = Path(tempfile.mkdtemp(prefix="baldanders-test-"))
if (_real_home / "models").is_dir():
    try:
        (_test_home / "models").symlink_to(_real_home / "models", target_is_directory=True)
    except OSError:
        pass  # e.g. Windows without symlink rights: NER integration tests skip
os.environ["BALDANDERS_HOME"] = str(_test_home)

for var in ("LC_ALL", "LC_TELEPHONE"):
    os.environ.pop(var, None)
os.environ["LANG"] = "C"
