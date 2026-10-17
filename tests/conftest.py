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

# Never touch the real system keychain from tests: use an in-memory one.
import keyring
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


keyring.set_keyring(MemoryKeyring())

for var in ("LC_ALL", "LC_TELEPHONE"):
    os.environ.pop(var, None)
os.environ["LANG"] = "C"
