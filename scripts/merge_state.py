"""Führt data/state.json feldweise zusammen (siehe storage.merge_states).

Usage:
    python scripts/merge_state.py BASE.json OURS.json THEIRS.json --out data/state.json

Wird vom Workflow-Schritt "State committen & pushen" benutzt, wenn der Push
abgelehnt wurde, weil das Dashboard währenddessen Status-Änderungen nach main
committed hat.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("ours")
    ap.add_argument("theirs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    merged = storage.merge_states(
        storage.load_state(args.base), storage.load_state(args.ours), storage.load_state(args.theirs)
    )
    storage.save_state(args.out, merged)


if __name__ == "__main__":
    main()
