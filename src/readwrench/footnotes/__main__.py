import sys

from readwrench.cli import main

raise SystemExit(main(["footnotes", *sys.argv[1:]]))
