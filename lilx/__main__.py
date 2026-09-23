"""Allows running lilx with ``python -m lilx``."""

import sys

from lilx.app import main

if __name__ == "__main__":
    sys.exit(main())
