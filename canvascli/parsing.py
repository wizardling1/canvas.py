from __future__ import annotations

import argparse
import sys


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Argument parser that shows the relevant help before an error."""

    def error(self, message: str) -> None:
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")
