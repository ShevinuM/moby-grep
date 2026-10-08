# MobyGrep

What if you could search a whale call? MobyGrep listens to live underwater microphones (hydrophones) and runs the audio through Perch 2.0, a pretrained bioacoustics model, to turn each sound into a numeric fingerprint. A classifier trained on expert-labeled Orcasound recordings decides whether it’s a whale call, and every call becomes searchable by similarity in Postgres with pgvector. Accuracy is measured against OrcaHello, the existing open-source orca detector.

It is one Python codebase that runs as three programs (an ingestor, detection workers and a search API) around a Redis Stream and one Postgres database, on a single server under Docker Compose.

My goal is to help researchers advance their research on marine bioacoustics and cetacean communication. 

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
