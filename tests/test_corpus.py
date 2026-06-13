"""Runs the on-device fingerprint corpus as a regression test WHEN PRESENT.

The corpus (./corpus, gitignored — contains device PII) is captured locally via
scripts/spike2_capture_corpus.py. CI and fresh clones have no corpus, so this
test skips. Locally, it re-fingerprints the saved dumps and fails on any
stability / under-merge / over-merge violation — turning the eyeball check into a
repeatable gate against fingerprint-code regressions.
"""

from pathlib import Path

import pytest

CORPUS = Path(__file__).resolve().parent.parent / "corpus"


@pytest.mark.skipif(not CORPUS.exists(), reason="no local fingerprint corpus captured")
def test_corpus_has_no_fingerprint_violations():
    import sys

    sys.path.insert(0, str(CORPUS.parent / "scripts"))
    from spike2_corpus_check import main  # type: ignore

    assert main(str(CORPUS)) == 0
