import sys

from readwrench.cli import main

raise SystemExit(main(["subset-proof", *sys.argv[1:]]))
