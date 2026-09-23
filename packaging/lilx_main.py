"""Entry point for frozen builds (PyInstaller): the same as ``python -m lilx``."""

import sys

from lilx.app import main

if __name__ == "__main__":
    sys.exit(main())
