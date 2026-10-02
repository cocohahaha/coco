import sys

# Launcher commands run before the dependencies may be installed: keep them away from the
# CLI module and its imports.
if len(sys.argv) > 1 and sys.argv[1] in ("start", "stop", "restart", "status", "setup", "shortcut", "update"):
    from .launcher import main as launch
    sys.exit(launch(sys.argv[1:]))

from .cli import main

main()
