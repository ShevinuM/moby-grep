# MobyGrep

This is a completely open source search engine for all the whale calls in the world. My goal is to help researchers advance their research on marine bioacoustics and cetacean communication. 

MobyGrep listens to live underwater microphones (hydrophones), detects whale calls in the audio, turns each call into a numeric fingerprint, and makes the calls searchable. It is one Python codebase that runs as three programs (an ingestor, detection workers and a search API) around a Redis Stream and one Postgres database, on a single server under Docker Compose.

## Development

Requires [uv](https://docs.astral.sh/uv/) 0.12.22. uv installs Python 3.13 by itself.

```sh
uv sync                  # create .venv from uv.lock
uv run ruff format --check
uv run ruff check
uv run mypy
uv run lint-imports      # the import rules between package areas
uv run pytest
```


## Licence

[MIT](LICENSE).
