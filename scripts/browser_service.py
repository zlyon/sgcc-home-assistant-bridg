#!/usr/bin/env python3
from _compat import alias, run

if __name__ == "__main__":
    run("browser_service")
else:
    alias("browser_service", __name__)
