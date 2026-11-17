import sys

from readwrench.cli import main

raise SystemExit(main(["reanchor", *sys.argv[1:]]))
