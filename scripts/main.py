#!/usr/bin/env python3
from _compat import alias, run

if __name__ == "__main__":
    run("main")
else:
    alias("main", __name__)
