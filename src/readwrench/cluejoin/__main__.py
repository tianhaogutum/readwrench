import sys

from readwrench.cli import main

raise SystemExit(main(["clues", *sys.argv[1:]]))
