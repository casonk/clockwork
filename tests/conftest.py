"""Keep the suite off the network: the To Code add form would ask a local decision model to sort
an item with a blank group or repository. Tests that exercise that path inject a fake client."""

import os

os.environ.setdefault("CLOCKWORK_TRIAGE", "0")
