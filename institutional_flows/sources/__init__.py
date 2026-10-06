"""Pure parsers + thin, injectable fetchers for the three institutional-flow sources.

Each module follows the `exchange_watch.sources` convention: `parse_*` takes only already-
fetched text/JSON and returns a status, never touches the network; `fetch_*` is a thin wrapper
that performs exactly one request (an injectable `get`/`nse` seam for tests) and classifies any
failure through `operations.connectivity`.
"""
