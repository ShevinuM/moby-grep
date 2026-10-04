# MobyGrep contracts

These documents are the interfaces between the foundation (the repository, the local infrastructure and the shared code) and the units built on it: the model unit, the ingestion unit, the detection unit, the search unit, the scorecard unit and the production deployment unit. They are the authority. Where a document and the code differ, one of them has a defect, and the rule under [How a contract changes](#how-a-contract-changes) says which is corrected first. Each document ends with a change log.

## The system in one picture

```
Hydrophone streams ──> Ingestor ──> Redis Stream ──> Detection workers ──> Postgres + clip storage ──> API
                       (program 1)   (the queue)      (program 2)                                      (program 3)
```

- The **ingestor** (asyncio) downloads audio in chunks of about 10 seconds and puts one message for each chunk on the queue.
- **Detection workers** (synchronous, one chunk at a time) take chunks off the queue, run a bioacoustics model (Perch 2.0), and store the audio clip and the fingerprint of each call.
- The **API** (FastAPI) serves similarity search and queries. Grafana is the only user interface.

All three are programs of one Python package, `mobygrep`, and run on one server under Docker Compose with one Postgres database.

## Terms

| Term | Meaning |
|---|---|
| Source | Where audio comes from: a live hydrophone, an archive, or a reference library of labelled recordings |
| Chunk | One short piece of audio from a source (about 10 s for live audio). The unit of work on the queue |
| Chunk ID | The text identifier of a chunk, `<source>/<group>/<item>`. The idempotency key for everything |
| Ledger | The `chunks` table: one row for every chunk ever enqueued, whatever the outcome |
| Detection | One whale call found in a chunk |
| Embedding | The fingerprint of 1536 numbers that Perch 2.0 produces for a detection |
| Staged audio | The audio file of a chunk, waiting on a shared disk volume between the ingestor and a worker |
| Clip | The saved audio of a detection, kept in object storage |
| Dead stream | The Redis stream that holds messages that could not be processed |

## The documents

| Document | What it fixes |
|---|---|
| [chunk-id.md](chunk-id.md) | The chunk ID format, how the parts are derived, examples |
| [configuration.md](configuration.md) | Every environment variable, with its value on the host and inside Compose |
| [observability.md](observability.md) | Metric naming, log fields, the trace ID, health paths, startup checks, shutdown, the watchdog, the error classes |
| [schema.md](schema.md) | Tables, columns, keys, ledger status transitions, ledger operations, roles, migration rules |
| [queue.md](queue.md) | Streams, the consumer group, the message, the produce and consume rules, defaults, metrics, obligations on other units |
| [idempotency.md](idempotency.md) | The rule, the handler steps, and what each store holds after a crash at each point |
| [storage.md](storage.md) | The staging store and the clip store: interfaces, key rules, retention, obligations |

## Who depends on what

| Document | Model unit | Ingestion unit | Detection unit | Search unit | Scorecard and production deployment |
|---|---|---|---|---|---|
| [chunk-id.md](chunk-id.md) | | Creates chunk IDs | Reads them; derives clip keys | Displays them; prefix queries | |
| [configuration.md](configuration.md) | | Adds its settings | Adds its settings; consumer name | Adds its settings | Production values, secrets |
| [observability.md](observability.md) | | Metrics, logs, health, shutdown, error classes | The same; raises permanent and transient errors | The same; health paths behind the proxy | Dashboards, alerts, metric conventions |
| [schema.md](schema.md) | Embedding dimension | `sources`, `source_status`, `chunks`, `feed_gaps` | `detections`, `embeddings`, ledger updates | Queries; owns the vector index | Read-only role, Grafana queries, role creation in production |
| [queue.md](queue.md) | | `enqueue`, back-pressure, the sweep | The handler, consumer names, model load before the first read | | Queue metrics, requeue as the recovery step |
| [idempotency.md](idempotency.md) | | Repeat enqueue | The handler steps | | Failure drills |
| [storage.md](storage.md) | Where decoding lives | Writes staged audio; runs the purge; disk floor | Reads staged audio; decodes; writes clips | Clip URLs; redirect route; decodes uploads the same way | R2 bucket and credentials |

## Unconfirmed statements

A statement about how Redis, Postgres or the object store behaves that the design depends on, and that nothing has shown yet, ends with `(unconfirmed: to be proven by test)`. When a test proves it, the tag is removed and a change log row says so; the test is then the record. To list what is still unconfirmed: `git grep -n "(unconfirmed" docs/contracts/`.

## How a contract changes

When a test shows a behaviour different from what a document says:

1. Correct the document and add a row to its change log.
2. Then change the code.
3. Then change the expectation in the test.

A test is never loosened to make it pass.

A unit that needs a contract changed proposes the change to the document first, in a pull request, with a change log row that says what changes and why. The other units read the change log to see what moved.

Rules for the documents themselves:

- `## Change log` is the last level-2 heading of each of the seven documents. Each row has an ISO date, the change and the reason.
- Documents refer to each other by relative link, and to the units by role.
- No secret and no real credential appears in any document.
