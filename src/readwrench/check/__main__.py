import sys

from readwrench.cli import main

raise SystemExit(main(["check", *sys.argv[1:]]))
