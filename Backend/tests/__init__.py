"""Tests for the backend.

Deliberately plain `unittest` from the standard library, run with

    python -m unittest discover -s tests -t .

from the Backend directory. No pytest, and nothing added to
requirements.txt: the production image this application is deployed from
(Railway) should carry the application and its runtime dependencies and
nothing else, and a test runner is neither.
"""
