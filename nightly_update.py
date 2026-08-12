"""Refresh the catalogue/change log, then archive every newly discovered PDF."""
import argparse

import download
import monitor


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true",
                        help="refresh without recording the pre-installation backlog")
    args = parser.parse_args()
    monitor.run(record_changes=not args.baseline)
    download.run(["Spc", "Pil", "Par"], workers=4, storage="r2")
