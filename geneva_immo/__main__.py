"""Allow ``python -m geneva_immo``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
