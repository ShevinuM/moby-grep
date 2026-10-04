# Observability and runtime conventions

This document fixes what makes the three programs behave the same from the outside: how metrics are named, what every log line carries, the trace ID, the health paths, the startup checks, graceful shutdown, the watchdog and the shared error classes. Every unit follows it when it builds its program. The scorecard unit builds dashboards and alerts on the metric conventions. The production deployment unit relies on the health paths and the shutdown behaviour.

## Metrics

### Naming rules

- The form is `mobygrep_<area>_<what>_<unit>`, with `_total` on counters. The areas are `ingest`, `detect`, `search`, `queue`, `storage` and `process`.
- Base units only: seconds and bytes. The unit is in the name, in the plural.
- Allowed labels are bounded sets: `source`, `stream`, `kind`, `reason`, `outcome`. A chunk ID, a trace ID or an error message is never a label.
- The program is identified by Prometheus's own `job` label, not by a custom label.
- Durations use classic histograms with one shared bucket set, in seconds: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120.
- Every program sets `mobygrep_build_info` to 1 at startup, with the labels `version` and `revision`. The version comes from the package metadata. The revision comes from the environment variable `MOBYGREP_BUILD_REVISION`, set when the image is built, and is `unknown` when the variable is absent (see [configuration.md](configuration.md#variables-not-read-by-the-settings-classes)). Build info is the one metric whose labels are outside the allowed set.
- One process runs in each container. Prometheus multiprocess mode is not used.
- Every unit exports its metrics as it builds its program, following these rules. The scorecard unit builds dashboards on top and does not add instrumentation afterwards.

### Metrics the foundation registers

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `mobygrep_build_info` | Gauge | `version`, `revision` | Always 1; identifies the build |
| `mobygrep_queue_messages_enqueued_total` | Counter | `stream` | Messages added to a stream |
| `mobygrep_queue_enqueue_duration_seconds` | Histogram | | Time of one `enqueue` call |
| `mobygrep_queue_enqueue_refused_total` | Counter | `stream` | Enqueues refused by back-pressure |
| `mobygrep_queue_messages_processed_total` | Counter | `stream` | Messages whose handler returned |
| `mobygrep_queue_messages_failed_total` | Counter | `stream` | Deliveries that ended in an unknown error or ran out the transient retry limit |
| `mobygrep_queue_messages_duplicate_total` | Counter | `stream` | Messages for chunks that were already processed |
| `mobygrep_queue_messages_dead_lettered_total` | Counter | `stream`, `reason` | Messages moved to the dead stream. `reason` is `invalid_message`, `unknown_chunk`, `max_deliveries` or `permanent_error` |
| `mobygrep_queue_handler_retries_total` | Counter | `stream` | In-place retries after a transient failure |
| `mobygrep_queue_messages_reclaimed_total` | Counter | `stream` | Entries claimed from another consumer |
| `mobygrep_queue_messages_lost_total` | Counter | `stream` | Pending entries whose payload had been trimmed |
| `mobygrep_queue_handler_duration_seconds` | Histogram | `stream` | Time of one handler call |
| `mobygrep_queue_oldest_pending_age_seconds` | Gauge | `stream` | Age of the oldest pending entry |
| `mobygrep_storage_staging_free_bytes` | Gauge | | Free bytes on the disk that holds the staging root |

Stream length, group lag and pending counts come from the Redis exporter and are not duplicated by the library.

Prometheus scrapes the programs inside the Compose network by service name. Programs run on the host are not scraped; their metrics are readable at `localhost:<port>/metrics`. This is a known limit.

## Logs

- Lines are JSON on stdout when the program is not attached to a terminal, or when JSON is forced by the log format setting. Otherwise a console renderer is used. Timestamps are UTC, ISO-8601.
- Every line carries `timestamp`, `level`, `event` and `program`.
- `trace_id` and `span_id` are added when a valid trace context is current. When there is none, the keys are absent. Zeros are never logged.
- Records from the standard library and from third parties (uvicorn, SQLAlchemy, botocore, OpenTelemetry) are rendered through the same chain, so the stream is uniformly JSON.
- Context for one unit of work (`chunk_id`, `source`) is bound by the code that handles that unit of work, and is cleared afterwards.
- `chunk_id` belongs in logs and is never a metric label.
- There is no log store. To find the logs for a trace, search the container logs for the `trace_id`.

## Trace ID

- **Every chunk has its own trace ID**, with or without a tracing backend. It is the correlation ID: the same ID is in the `traceparent` field of the queue message, in every log line written while the chunk is handled, and in the ledger row (`chunks.trace_id`, 32 hexadecimal characters).
- The producer decides the trace ID. An ambient span is never inherited: if the ingestion unit wraps a whole poll in a span, each chunk in that poll still gets its own trace ID.
- A repeated enqueue or a requeue keeps the original trace ID of the row.
- With the tracing SDK absent or disabled, a generated `traceparent` is still extracted as a valid context (unconfirmed: to be proven by test).
- Planned span rules, to be completed when tracing is built. Tracing is the first thing to be cut if time runs short; the trace ID is not cut.
  - The producer span is a new root for each chunk.
  - The consumer runs each handler in a span that is a child of the message's context, so a redelivery is another span in the same trace.
  - The tracing backend being unavailable never blocks or fails a program.
  - Later units add their own work spans inside these.

## Health and ops endpoints

| Path | Meaning | Response |
|---|---|---|
| `/metrics` | Prometheus exposition | 200 |
| `/healthz` | Liveness: the main loop is still turning | 200, or 503 when the last heartbeat is older than the liveness timeout |
| `/readyz` | Readiness: the startup checks passed and no shutdown is in progress | 200 or 503 |

| Program | Port | Serves |
|---|---|---|
| API | 8000 | The API, plus `/metrics`, `/healthz`, `/readyz` |
| Ingestor | 9101 | `/metrics`, `/healthz`, `/readyz` |
| Worker | 9102 | `/metrics`, `/healthz`, `/readyz` |

- The paths and the status codes are identical across the three programs. Response bodies are not part of the contract.
- The API has no main loop. Its `/healthz` returns 200 whenever the server can answer at all.
- The API serves these paths on the same port as its public routes. **Obligation on the production deployment unit:** the reverse proxy must expose only the API's own routes.
- The ops endpoints answer during the startup checks: `/healthz` answers, and `/readyz` says "not ready" until the checks pass.

## Startup checks

Each check passes, or fails with a message that names the dependency and the reason.

| Check | Passes when |
|---|---|
| Postgres | A connection succeeds **and** the schema is at exactly the migration this build expects. Database behind: the message says to run the `migrate` service. Database ahead: the message says the database is newer than this build and the image must be updated. Both fail |
| Queue Redis | `PING` succeeds; then the consumer groups are ensured |
| Cache Redis | `PING` succeeds |
| Staging | Ingestor: the root exists and is writable, and the check creates a marker file `.mobygrep-staging` there if it is absent. Worker: the root exists, is readable, and contains the marker |
| Clip store | A GET of a fixed sentinel key returns "no such key". "No such bucket", "access denied" and bad credentials are a **misconfiguration** and fail. A timeout or a server error is **unavailability** |

- Checks are retried with backoff for a bounded time: 30 s in total by default. After that the program exits non-zero and Compose restarts it. A shutdown request during the checks ends them at once.
- **Every failing check is retried for the whole time, a misconfiguration result included.** A program does not exit on the first "no such bucket": the local object store gave exactly that answer for a short time after it reported healthy, and then the expected "no such key". A result counts as a misconfiguration only if it is still there when the time ends.
- The retry exists because a dependency can report healthy slightly before it is usable. The local object store's bucket became usable about 0.7 s after its health endpoint first answered, in four fresh starts, so 30 s is ample.
- **Why the clip store check is a GET.** The local object store creates a bucket on the first put to it, so a put would hide a wrong bucket name. A GET or a HEAD does not create one: against a missing bucket the GET returns "no such bucket". Nothing may ever write the sentinel key.
- **The queue instance at its memory limit.** Creating a stream and its group is refused at the memory limit, and the Queue Redis check ensures the groups. Whether the check passes when the groups already exist is (unconfirmed: to be proven by test). If it does not, a program that restarts while the instance is full cannot start. See [queue.md](queue.md#trimming).
- **The staging marker.** The image creates the staging directory, so the directory exists even when the shared volume was never mounted. A worker started without the mount fails at startup with a message that names the cause (the shared volume is not mounted, or the ingestor has never started). Without the marker it would find the audio of every chunk missing and dead-letter all of them.
- **The clip store exception.** The clip store is the one dependency outside the server. If it is still unavailable (not misconfigured) when the retry time ends, the program logs a warning and starts anyway. The API can search without it, and the worker's handling of transient failures holds chunks until it returns. Postgres, Redis and staging stay fail-fast.

## Graceful shutdown

Identical for every program:

1. On SIGTERM or SIGINT, readiness turns false immediately.
2. The program stops taking new work.
3. Work in flight is finished and acknowledged.
4. Telemetry is flushed.
5. The process exits 0.
6. If this takes longer than the grace period (default 30 s), the process exits non-zero. Anything unacknowledged stays pending on the queue and is reclaimed by another worker.
7. A second signal exits immediately.

Compose sets `stop_grace_period` a little above the program's grace period.

A program in a container exits 0 within the grace period (unconfirmed: to be proven by test).

## Watchdog

A daemon thread enforces steps 6 and 7 of the shutdown, because the main thread may be the thing that is stuck.

- Once shutdown is requested, the watchdog waits the grace period and then ends the process with a non-zero code.
- A second signal ends the process at once, from the signal handler itself.
- With heartbeat checking on, the watchdog ends the process with a non-zero code when the last heartbeat is older than the liveness timeout (default 120 s). Compose restarts a container only when it exits, not when it is unhealthy, so a hung program has to exit by itself.
- Heartbeat checking is on for the ingestor and the worker, and off for the API. It starts only when the main loop starts, so a slow startup (loading a model) is not mistaken for a hang. **Obligation on later units:** any wait inside the main loop that can be long must keep beating the heartbeat.
- Limit: Python runs a signal handler on the main thread, between two Python instructions. If the main thread is inside a long native call (model inference) when the signal arrives, the handler does not run until the call returns. Docker's stop grace period is the backstop for that case, and the stale-heartbeat exit covers a call that never returns.
- The watchdog ends a process whose main thread is stuck (unconfirmed: to be proven by test).
- Ending the process from the watchdog skips normal teardown on purpose. Anything unacknowledged stays pending. On restart the worker picks its own pending entry up again. That read raises the entry's delivery count by one, so a chunk that hangs the handler every time is dead-lettered after the maximum number of deliveries. The same rule has a cost: a worker that restarts for a reason that has nothing to do with the chunk also spends one delivery of the entry it held. See [queue.md](queue.md#delivery-count).

## Errors

One shared hierarchy, in `mobygrep.shared.errors`:

| Class | Meaning | Subclasses |
|---|---|---|
| `MobygrepError` | Base class | |
| `PermanentError` | **This chunk can never succeed.** Retrying is pointless | `InvalidChunkId`, `InvalidMessage`, `StagedAudioNotFound` |
| `TransientError` | **The environment is failing, not the chunk.** Retry later | `ClipStoreUnavailable`, `ClipStoreMisconfigured`, `LedgerUnavailable`, `QueueUnavailable`, `QueueBackpressure` |
| `ClipNotFound` | A clip key has no object. Neither class above: the caller decides what it means | |
| `StartupCheckFailed` | A startup check failed | |

The rule for later units:

- Raise `PermanentError` only when the fault is in the chunk itself: unreadable audio, an invalid message.
- Anything about a dependency is a `TransientError`. That includes a clip store that is misconfigured at run time: the credentials are wrong for every chunk, so no chunk should be marked failed for it.
- Missing staged audio is a `PermanentError`. The marker check at startup rules out the environment as the cause, so a missing file means the audio was purged or lost.

What the queue consumer does with each class is in [queue.md](queue.md#consuming).

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
| 2026-10-02 | The watchdog paragraph states that a restart spends one delivery of the entry the worker held, whatever the reason for the restart | The spike saw every read of a consumer's own history add one to the delivery count. The design counted on this for a chunk that hangs the handler; the cost for unrelated restarts was not written down |
| 2026-10-02 | The startup checks say why the clip store check is a GET and never a put | The spike saw the local object store create a bucket on the first put, which would hide a wrong bucket name |
| 2026-10-02 | The startup checks state that a misconfiguration result is retried for the whole time | The spike saw the local object store answer "no such bucket" for about 0.7 s after it reported healthy |
| 2026-10-02 | The startup checks record that the Queue Redis check may fail while the queue instance is at its memory limit | The spike saw the creation of a stream and its group refused at the memory limit |
