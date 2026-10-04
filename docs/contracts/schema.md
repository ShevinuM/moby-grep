# Database schema and ledger

This document fixes the tables of the one Postgres database, their keys and constraints, the status transitions of the ledger, the ledger operations in shared code, the database roles and the migration rules. The ingestion unit writes `sources`, `source_status`, `chunks` and `feed_gaps`. The detection unit writes `detections` and `embeddings` and updates the ledger. The search unit queries all of them and owns the vector index. The scorecard unit reads through the read-only role.

## Conventions

- All timestamps are `timestamptz`.
- Every text "enumeration" is a text column with a named CHECK constraint, not a Postgres enum type. Enum types are awkward to change in a migration.
- Every constraint and index has a deterministic name that comes from a naming convention.
- The embedding dimension is one constant in shared code, `EMBEDDING_DIM = 1536`. Migrations hardcode the literal, because a migration is a snapshot.

## Tables

There are six tables. The column types are added to these tables when the models are built.

### `sources`

Where audio comes from.

| Column | Notes |
|---|---|
| `id` | Identity primary key |
| `slug` | Unique. Must be a valid chunk ID segment (see [chunk-id.md](chunk-id.md#format)) |
| `kind` | `live`, `archive` or `reference` |
| `name` | Display name |
| `latitude` | Optional |
| `longitude` | Optional |
| `attribution` | Optional credit and licence text |
| `config` | JSON object for source-specific settings; default empty. It lets the ingestion unit add knobs without a migration. **It never holds secrets**: the Grafana role can read this table |
| `enabled` | Default true |
| `created_at` | |
| `updated_at` | |

### `source_status`

Mutable state, one row for each source, kept apart from the static description.

| Column | Notes |
|---|---|
| `source_id` | Primary key, and foreign key to `sources` |
| `status` | `unknown`, `online` or `offline`; default `unknown` |
| `status_changed_at` | |
| `last_chunk_at` | |
| `last_checked_at` | |
| `detail` | Optional text |

### `chunks`

The ledger: one row for every chunk ever enqueued.

| Column | Notes |
|---|---|
| `chunk_id` | **Primary key.** The text chunk ID, declared with the `C` collation |
| `source_id` | Foreign key to `sources` |
| `group_key` | The second segment of the chunk ID, `C` collation. Kept for grouping and display. No index of its own |
| `item_key` | The third segment of the chunk ID, `C` collation. Kept for grouping and display. No index of its own |
| `started_at` | When the audio starts; null for audio with no known time |
| `duration_ms` | Optional; positive |
| `audio_key` | Key of the staged audio |
| `label` | Optional known species label, for reference audio |
| `status` | `queued`, `processed` or `failed`; default `queued` |
| `trace_id` | 32 hexadecimal characters; not null |
| `created_at` | When the ledger row was written, just before the enqueue |
| `enqueued_at` | When the message was last added to its stream. Null means the row was written and the add has not succeeded (yet) |
| `completed_at` | When the chunk reached `processed` or `failed` |
| `processing_ms` | Optional |
| `detector` | `primary` or `fallback`; null until processed |
| `model_version` | Null until processed |
| `detection_count` | Null until processed; zero means noise |
| `attempts` | The delivery count when the chunk completed |
| `worker` | The consumer name that completed it |
| `error_type` | Set when failed |
| `error` | Set when failed |
| `audio_purged_at` | When the staged audio was deleted |

- The chunk ID is the primary key, not a surrogate integer with a unique constraint. A unique index on it is needed for idempotency anyway, and this is the table with the most rows (about 60,000 a day for seven live sources), so one index is better than two.
- The `C` collation makes comparison bytewise, so it cannot change when the locale data of the operating system changes. It also lets a prefix search ("every chunk of this source and folder") use the primary key index (unconfirmed: to be proven by test).
- CHECK constraints: `completed_at` is set exactly when the status is `processed` or `failed`; `detection_count` is set when the status is `processed`.
- Indexes: `(source_id, started_at)`; a partial index on `created_at` for rows still `queued`; a partial index on `completed_at` for terminal rows whose audio has not been purged.

### `detections`

One row for each detected call.

| Column | Notes |
|---|---|
| `id` | Identity primary key |
| `chunk_id` | Foreign key to `chunks`; the chunk in which the call starts |
| `source_id` | Foreign key to `sources`; repeated here so that queries by time and source need no join |
| `start_offset_ms` | Position of the start within the chunk |
| `duration_ms` | Length of the call |
| `started_at` | Absolute start time; null when the chunk has none |
| `detector` | `primary` or `fallback` |
| `model_version` | |
| `confidence` | Optional, between 0 and 1 |
| `clip_key` | Key in the clip store; optional. Only keys are stored, never URLs (see [storage.md](storage.md#clip-keys)) |
| `created_at` | |

- Unique on `(chunk_id, start_offset_ms)`. This is a backstop, not the idempotency mechanism: the handler's transaction already guarantees that the detections of a chunk come from exactly one attempt (see [idempotency.md](idempotency.md)).
- Indexes on `started_at`, `(source_id, started_at)` and `created_at`.
- The table does not decide whether a detection is one model window or several merged windows, or whether a call may run past the end of its chunk. Those belong to the model unit and the detection unit, and the shape tolerates all of them.

### `embeddings`

Fingerprints.

| Column | Notes |
|---|---|
| `detection_id` | Primary key, and foreign key to `detections` with cascade on delete |
| `model` | Name of the model that produced it |
| `embedding` | `vector(1536)` |
| `created_at` | |

- The foundation creates no approximate-nearest-neighbour index. Exact search is fast enough up to tens of thousands of rows, and the search unit owns the choice of index.
- A detection made by the fallback detector has no row here.
- Changing the dimension later means a new migration that drops any vector index, alters the column type and recreates the index, plus the constant. With rows present, every row must be embedded again.

### `feed_gaps`

Stretches where audio is missing.

| Column | Notes |
|---|---|
| `id` | Identity primary key |
| `source_id` | Foreign key to `sources` |
| `started_at` | Start of the gap |
| `ended_at` | Null while the gap is open. CHECK: not before `started_at` |
| `reason` | Text. The ingestion unit defines the vocabulary |
| `missing_chunks` | Optional count |
| `group_key` | Optional |
| `detail` | Optional |
| `created_at` | |

Unique on `(source_id, started_at)`.

## Idempotency keys

| Table | Key |
|---|---|
| `sources` | `slug` |
| `chunks` | `chunk_id` |
| `detections` | `(chunk_id, start_offset_ms)` |
| `embeddings` | `detection_id` |
| `feed_gaps` | `(source_id, started_at)` |

## Ledger status transitions

| From | To | By |
|---|---|---|
| (none) | `queued` | The producer's insert |
| `queued` | `processed` | A handler's commit |
| `queued` | `failed` | The consumer, when it dead-letters |
| `failed` | `queued` | The admin requeue command, only while the staged audio exists |
| `failed` | `processed` | A late duplicate message that succeeds |

`processed` is final. Processing a `processed` chunk again (for example to replace a fallback detection once the main detector is back) is not supported by the foundation. If the detection unit wants it, it defines an explicit reset that deletes the detections of the chunk and resets the row in one transaction. Such a reset can only work while the staged audio still exists.

## Ledger operations

The operations are in the module `mobygrep.shared.db.ledger`. Each exists in a sync and an async form.

| Operation | Behaviour |
|---|---|
| Insert if absent | Insert a `queued` row with a proposed trace ID; do nothing if the chunk ID exists. Returns the row as it now stands: whether it was created, its status, its trace ID, its `enqueued_at`, and the kind of its source. Resolves the source by slug and fails clearly if the source does not exist |
| Mark enqueued | Set `enqueued_at` |
| Mark processed | Update to `processed` with the outcome, **only if the status is not already `processed`**. Returns whether anything changed |
| Mark failed | Update to `failed` with the error type and text, only if the status is `queued`. Changing no row is a success, not an error |
| Reset for requeue | Update a `failed` row back to `queued`: clear the completion fields, the error fields and `enqueued_at`, and keep the trace ID. Only if its audio has not been purged. Returns whether anything changed |
| Get | Return the status of the row, its trace ID and the fields needed to rebuild its message, or none |
| List purgeable | Rows whose audio is not yet purged, and that are `processed` and completed before one cutoff, or `failed` and completed before another; limited to a batch |
| Mark audio purged | Set `audio_purged_at` for a set of chunk IDs |
| List stale queued | Rows still `queued`, **with `enqueued_at` null**, older than a cutoff. For the ingestion unit's re-enqueue sweep |

```python
@dataclass(frozen=True)
class NewChunk:
    chunk_id: ChunkId
    audio_key: str
    started_at: datetime | None
    duration_ms: int | None
    label: str | None = None


@dataclass(frozen=True)
class ChunkOutcome:
    detector: str  # "primary" | "fallback"
    model_version: str
    detection_count: int
    processing_ms: int
    attempts: int
    worker: str
```

- The kind of the source is read from the `sources` row. It is not passed in by the caller. A kind supplied by the caller would be a second copy of the same fact, and a mismatch would send a chunk to the wrong stream.
- The conditional updates are what make "processing a chunk twice changes nothing" true at the ledger. "Mark processed" locks the row, so the second of two overlapping attempts waits, then finds the row `processed` and changes nothing (unconfirmed: to be proven by test).
- **Who commits.** The operations that the producer and the consumer call for themselves (insert if absent, mark enqueued, mark failed) commit. **The operations a handler calls take the handler's own session and never commit**, because the handler's transaction must contain "mark processed" and its detection rows together.

## Roles

| Role | Kind | Created by | Privileges |
|---|---|---|---|
| The owner (the `POSTGRES_USER` of the image) | Login, superuser | The Postgres image | Owns the schema. Used only to run migrations |
| `mobygrep_app` | Login | The init script; password from the environment | Read and write rows in every table, use sequences. No schema changes |
| `mobygrep_readonly` | No login; a group | The init script | Read every table |
| `grafana_reader` | Login; member of `mobygrep_readonly` | The init script; password from the environment | Read only, with a statement timeout and a connection limit, so that a bad dashboard query cannot starve the programs |

- Roles are created once, when the database is first initialised, by a script in `ops/postgres/initdb/`. Privileges are granted by migrations. Migrations never create or drop roles. **Obligation on the production deployment unit:** create the same three roles in its own runbook.
- The initial migration grants `mobygrep_app` and `mobygrep_readonly` their privileges on all tables and sets default privileges, so tables created by later migrations are covered without those migrations doing anything.
- The three programs, and the queue and chunk commands of the admin command, connect as `mobygrep_app`. Only the `migrate` service connects as the owner. The variables are in [configuration.md](configuration.md).

## Migration rules

- The Alembic environment and versions ship inside the package (`mobygrep/shared/db/migrations/`), so they are in the Docker image. `mobygrep-admin migrate` applies them.
- One initial migration creates the `vector` extension, all six tables and the grants.
- Migrations always run as the database owner.
- **Each unit adds its own migrations on top of the foundation's.**
- **Migrations are linear, with exactly one head.** Several units add migrations in parallel from the same starting revision. Whoever merges second sets the parent of their migration to the current head before merging. CI fails if there is more than one head.

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
