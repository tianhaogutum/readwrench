import sys

from readwrench.cli import main

raise SystemExit(main(["lineage", *sys.argv[1:]]))
