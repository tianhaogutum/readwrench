import sys

from readwrench.cli import main

raise SystemExit(main(["quote", *sys.argv[1:]]))
