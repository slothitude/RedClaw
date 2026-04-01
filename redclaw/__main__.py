"""Entry point for `python -m redclaw`."""

import sys
from redclaw.cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
