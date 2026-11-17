import sys

from readwrench.cli import main

raise SystemExit(main(["pdf-pages", *sys.argv[1:]]))
