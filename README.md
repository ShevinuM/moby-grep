# MobyGrep

MobyGrep listens to live underwater microphones (hydrophones), detects whale calls in the audio, turns each call into a numeric fingerprint, and makes the calls searchable. It is one Python codebase that runs as three programs (an ingestor, detection workers and a search API) around a Redis Stream and one Postgres database, on a single server under Docker Compose. Its focus is backend reliability, and the aim is to back that with published speed, uptime and accuracy numbers.

**Status: foundation in progress.** The repository is tooled and the package is empty. Nothing runs yet.

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

### Git hooks

The hooks run ruff format, ruff check and mypy through `uv run`, so they use the versions in `uv.lock`. Run them with [prek](https://prek.j178.dev/), a drop-in replacement for pre-commit that reads the same `.pre-commit-config.yaml`:

```sh
uv tool install prek
prek install
```

## Licence

[MIT](LICENSE).
