import sys

from readwrench.cli import main

raise SystemExit(main(["concordance", *sys.argv[1:]]))
