# Queue

This document fixes the queue between the ingestor and the detection workers: the streams, the consumer group, the message, how a chunk is produced and consumed, what happens on failure, the defaults and the metrics. The ingestion unit calls `enqueue` and depends on its failure behaviour. The detection unit supplies a handler and depends on the delivery rules. The scorecard unit depends on the metrics and on requeue as the recovery step.

## Where

The `redis-queue` instance: Redis 8.10 with the append-only file on, fsync on every write, `maxmemory 256mb` and the `noeviction` policy. Nothing but these streams lives there. The API's cache is a second instance, `redis-cache`.

## Streams

With the default key prefix:

| Key | Purpose |
|---|---|
| `mobygrep:chunks:live` | Chunks from live sources |
| `mobygrep:chunks:archive` | Chunks from archive and reference sources |
| `mobygrep:chunks:dead` | Messages that could not be processed |

Live and archive are separate streams so that replaying a large archive cannot starve live audio; workers look at live first. Redis has no priority for a single message, and a second stream with the same group is the usual substitute.

## Consumer group

- One group, `detectors`, on the live and the archive stream.
- It is created from ID `0`, together with the stream: `XGROUP CREATE <key> detectors 0 MKSTREAM`. "Group already exists" is success.
- Creating from `0` and not from "now" matters. If the ingestor adds chunks before any worker has started, a group created from "now" would skip them.
- Every program that touches the queue ensures the groups at startup. The group must exist before the first add: on a stream with no group, acknowledged-only trimming removes entries down to the cap, because nothing references them.

## Message, version 1

Stream entries are flat maps from string to string.

| Field | Content |
|---|---|
| `v` | Schema version, `1` |
| `chunk_id` | The chunk ID (see [chunk-id.md](chunk-id.md)) |
| `kind` | `live`, `archive` or `reference` |
| `staged_audio_key` | Key of the staged audio (see [storage.md](storage.md#staging-store)) |
| `started_at` | ISO-8601 UTC; empty when unknown |
| `duration_ms` | Empty when unknown |
| `enqueued_at` | ISO-8601 UTC |
| `traceparent` | W3C trace context; always present (see [observability.md](observability.md#trace-id)) |
| `known_species_id` | Optional ID of the known species, for reference audio (see [schema.md](schema.md#species)) |

- Audio bytes are never put on the stream. The message points at the staged file.
- Every field can be rebuilt from the ledger row of the chunk. That is what lets the admin command requeue a chunk.
- Timestamps are written as UTC with a `Z` suffix and microseconds (`2026-10-02T07:06:05.123456Z`). The reader accepts any ISO-8601 time that carries an offset and rejects a naive one.
- `None` is written as the empty string.
- Unknown extra fields are ignored on read, so fields can be added without a version bump.
- A message with an unknown `v`, or with a required field missing, is a permanent error (`InvalidMessage`).
- A change that needs a new `v` is deployed to the workers first and to the ingestor second, so no worker ever meets a version it does not know.

## Producing

Only through the library's `enqueue`. It is async, for the ingestor.

```python
class ChunkProducer:
    async def enqueue(self, chunk: NewChunk) -> EnqueueResult:
        """Record the chunk in the ledger, then add it to its stream."""


@dataclass(frozen=True)
class EnqueueResult:
    created: bool  # a new ledger row was written by this call
    entry_id: str | None  # the stream entry ID, when this call added one
    status: str  # the ledger row's status after the call
    trace_id: str  # always the ledger row's trace ID
```

`NewChunk` is defined in [schema.md](schema.md#ledger-operations).

The order inside `enqueue`:

1. Propose a new trace ID for the chunk. An ambient span is never inherited.
2. Insert the ledger row if it is absent, and commit. The trace ID of the row is the proposed one for a new row and the original one for an existing row. The stream is chosen from the kind of the source row.
3. If the row is `processed`, or it already has `enqueued_at`, stop. Nothing is added.
4. For the archive and reference kinds, check the back-pressure cap.
5. Add the message to the live stream (kind `live`) or the archive stream (the other kinds), trimming to the stream cap with the acknowledged-only policy.
6. Set `enqueued_at` on the row.

**The ledger row is written before the chunk goes on the stream.** Otherwise a chunk could be on the queue with no record. The cost: the ingestor cannot enqueue while Postgres is down. That stretch becomes a recorded feed gap.

Failure behaviour, which the ingestion unit depends on:

| Situation | Result |
|---|---|
| Postgres unavailable | `LedgerUnavailable`. Nothing was enqueued |
| Redis unavailable, or at its memory limit, after the ledger insert | `QueueUnavailable`. The row is `queued` with no `enqueued_at`. Calling `enqueue` again for the same chunk is safe and adds the entry |
| Called again after a successful add | The row has `enqueued_at`; nothing is added; `created` is false and `entry_id` is none |
| Crash between the add and setting `enqueued_at` | A later call adds a second entry. This is the one way the producer creates a duplicate. It is harmless (see [idempotency.md](idempotency.md)) |
| Archive backlog above the cap | `QueueBackpressure`. The row is `queued` with no `enqueued_at`; the same retry path applies |
| Redis itself loses its data | Rows stay `queued` with `enqueued_at` set, and no entry exists. `enqueue` will not add them again; `requeue --status queued` does. The age of the oldest `queued` row is the signal. That alert belongs to the scorecard unit |

At the memory limit Redis refuses the add with an out-of-memory error; redis-py raises `redis.exceptions.OutOfMemoryError`.

## Back-pressure

Both streams share one Redis instance with a fixed memory limit, and acknowledged-only trimming never removes unprocessed entries. An unbounded archive backlog could therefore fill the instance and block live chunks too.

- The producer refuses archive and reference chunks while the archive backlog is above a cap (default 5,000). Live chunks are never refused for that reason.
- The backlog is the lag of the consumer group as Redis reports it (the `lag` field of `XINFO GROUPS`): the number of entries not yet delivered to the group.
- The length of the stream is not the measure, because acknowledged-only trimming leaves processed entries in the stream up to the cap. Length overstates the backlog: 20 against a lag of 10 in one observation.
- The lag stays a correct number through acknowledged-only trimming, including when trimming leaves holes in the stream, and reclaims and history reads do not move it.
- When Redis reports no lag, the length is the fallback. Redis reported no lag in two cases, neither of which this design produces: after `XDEL` of an entry not yet delivered, and for a group created at `$` on a stream that already had entries. With the group created from `0` and no `XDEL`, the fallback should not be reached.

## Trimming

The live and the archive stream are trimmed on every add, to the stream cap (default 100,000), with the acknowledged-only policy. This needs Redis 8.2 or newer; the pinned image is `redis:8.10`.

The command forms, as sent on the wire. The position of the policy differs between the two commands:

```
XADD <key> ACKED MAXLEN ~ 100000 * <field> <value> ...
XTRIM <key> MAXLEN <n> ACKED
```

What the policy does:

- **Unfinished work is never trimmed.** Only entries that the group has acknowledged are removed. Entries that are pending, and entries not yet delivered, stay. In the observation: ten entries, six read, four of those acknowledged, a cap of 2. The trim removed the four acknowledged entries and left the two pending and the four undelivered ones, a length of 6 against a cap of 2.
- **Trimming skips past an unacknowledged entry; it does not stop at it**. A stuck entry at the head of the stream does not hold back the trimming of acknowledged entries behind it. Of 350 entries with only the first unacknowledged, a trim left only the first and the last.
- **The stream can be longer than its cap** while workers are behind. After a trim, the length is the larger of the cap and the number of unacknowledged entries: 6 unacknowledged entries against a cap of 2 left a length of 6.
- **The cap is exact for acknowledged entries, even in the approximate form**. With the acknowledged-only policy, `MAXLEN ~ n` removes acknowledged entries one by one down to `n`. It does not remove whole internal blocks only; that behaviour belongs to the default policy. Ten acknowledged entries with a cap of `~ 2` left exactly 2.
- **One call removes at most 10,000 entries** at the default block size: 100 × `stream-node-max-entries`, which is 100 by default. Entries that are skipped do not count against the limit. A stream far above its cap is brought down over several adds.
- A test that wants trimming needs no special recipe: a small cap really trims. A test that wants to see nothing trimmed must use unacknowledged entries, not a small count.

At the memory limit:

- With `noeviction`, a full instance refuses **every** add, including the producer's add that would trim, because the memory check comes before the command runs.
- Reading, acknowledging, claiming and `XTRIM` still work at the limit. Reading and acknowledging every entry freed no memory; used memory rose slightly.
- **Creating a stream and its group is refused at the limit too**: `XGROUP CREATE … MKSTREAM` for a new stream was refused. Every program ensures the groups at startup, so a program that starts while the instance is full may fail its startup check. Whether ensuring a group that already exists is also refused at the limit is (unconfirmed: to be proven by test).
- **A full instance therefore does not recover by draining.** Workers can read and acknowledge every entry and adds are still refused. The remedy is an explicit `XTRIM <key> MAXLEN <n> ACKED`, which is accepted at the limit and frees the memory of the acknowledged entries; an add then succeeds. The foundation records this and builds nothing for it. An alert on the memory of the instance belongs to the scorecard unit.

The payload-lost case:

- Under the acknowledged-only policy a pending entry cannot lose its payload. The consumer still handles the case, as a control.
- Its two shapes are known from a trim with the default policy:
  - A read of the consumer's own history returns the entry ID with an **empty field map**. The entry stays pending until it is acknowledged.
  - A reclaim (`XAUTOCLAIM`) returns the ID in the **deleted-IDs element** of its reply, not among the entries, and removes it from the pending list. A claim of one ID (`XCLAIM`) returns nothing and removes it from the pending list.

The dead stream is capped with ordinary trimming (the default policy, approximate form). There the approximate form does remove only whole blocks, so the dead stream can sit somewhat above its cap.

## Consuming

Synchronous, for the worker. The detection unit supplies only a handler; the library owns everything else.

```python
@dataclass(frozen=True)
class Delivery:
    stream: str
    entry_id: str
    message: ChunkMessage
    deliveries: int  # 1 on first delivery
    reclaimed: bool


ChunkHandler = Callable[[Delivery], None]
```

- Delivery is **at least once**. A message is acknowledged only after its handler returns.
- A worker handles one message at a time. It first drains its own unfinished entries from a previous run, then reads new ones, live before archive.
- Periodically (the reclaim interval) a worker claims entries that have been idle longer than the min-idle time from dead or stuck consumers, one at a time.
- For each entry, in this order:
  1. No fields: the payload was trimmed while the entry was pending. Acknowledge, log, count as lost. An ID that a reclaim reports as deleted is the same case: it is logged and counted as lost, and there is nothing to acknowledge.
  2. Parse. An invalid message is dead-lettered (reason `invalid_message`).
  3. Read the ledger row of the chunk. No row: dead-letter (reason `unknown_chunk`). Already `processed`: acknowledge, count as a duplicate, do not call the handler. This comes before the delivery-count check, so a chunk that succeeded is never reported as failed.
  4. If the delivery count exceeds the maximum, dead-letter without calling the handler (reason `max_deliveries`).
  5. Bind `chunk_id`, `source` and the trace context, then call the handler.
  6. The handler returns: acknowledge. It raises `PermanentError`: dead-letter (reason `permanent_error`). It raises `TransientError`: retry in place (see [Transient failures](#transient-failures)). It raises anything else: log, count, and **leave the entry pending**. The entry is claimed again after the min-idle time, by this worker or another. The min-idle time is thus the retry backoff.
- **The min-idle time** must exceed the worst-case time to process one chunk, or work in progress gets claimed by another worker. The default of 120 s is deliberately generous against an expected processing time well under a second.
- If Redis is unreachable, the loop backs off (1 s, doubling to 30 s), turns readiness false and tries again. It does not exit, and it keeps beating the heartbeat.
- On shutdown the loop stops between entries. A handler in flight finishes and is acknowledged. The block time bounds how long that takes.

The read and the client:

- The read for new entries covers both streams in one blocking call. **The count applies to each stream**: with one entry on each stream, one call with a count of 1 returned two entries, live first. Both are then pending under this worker. The worker handles the live entry first.
- A read that finds nothing returns an empty list, for a blocking and a non-blocking read alike. An empty read of the consumer's own history returns one element for each stream, each with an empty list.
- The socket timeout of the client (10 s) is longer than the block time (2 s). A 2 s blocking read on a client with a 10 s socket timeout returns empty after about 2 s and raises nothing; with a socket timeout shorter than the block time it raises a timeout error.
- **The queue clients set their retry policy explicitly.** redis-py 8.1.0 retries a command ten times with backoff by default, on connection and timeout errors. With that default a timed-out blocking read is sent again silently, and an error reached the caller only after 14 s with a 1 s socket timeout and after 59 s with a 5 s one. By the same arithmetic (eleven attempts of one socket timeout each), a 10 s socket timeout would hide a failure for up to about 110 s, close to the liveness timeout. That figure was not observed. During that time neither the consumer's own backoff nor the producer's failure handling sees the failure. The policy the library sets is fixed when it is built.

## Delivery count

Redis keeps a delivery count for every pending entry. With a maximum of 3, an entry is handed out at most three times and is dead-lettered on what would be the fourth delivery. The handler runs at most three times, and can run fewer.

What each path does to the count:

| Path | Count |
|---|---|
| First read as a new entry | 1 |
| A read of the consumer's own history | Plus 1, on every such read |
| A reclaim by another consumer (`XAUTOCLAIM`) | Plus 1 |
| A reclaim in the IDs-only form (`XAUTOCLAIM … JUSTID`) | Unchanged |
| A self-claim in the IDs-only form (`XCLAIM … JUSTID`) | Unchanged |
| A claim in the full form (`XCLAIM` without `JUSTID`) | Plus 1 |

- **The count is "times handed out", not "times the handler ran".** A delivery counts even if the worker died before its handler ran. The count read after a history read or a reclaim already includes that read.
- A worker that restarts and picks up its own pending entry raises the count of that entry. So a chunk that hangs or kills the worker every time is dead-lettered after the maximum number of deliveries.
- The cost: **a restart spends a delivery whatever its cause.** A worker that restarts three times in quick succession, for a reason that has nothing to do with the chunk, dead-letters the entry it held without its handler completing. The chunk is then `failed` and is recovered with requeue.
- The IDs-only reclaim does not spend a delivery. It is recorded here as an option. It is not adopted.

## Transient failures

A `TransientError` says the environment is failing, not the chunk. Counting it as a failed delivery would be wrong: with the defaults a chunk reaches its fourth delivery about seven minutes after its first, so a clip store outage longer than that would mark every chunk in flight as `failed`. The consumer therefore:

- Keeps the entry and calls the handler again after a backoff (1 s, doubling to 30 s). It reads no new entries while it waits.
- Before each attempt, resets the idle time of the entry without raising its delivery count, so that another worker does not claim it. The form is the self-claim `XCLAIM <key> detectors <consumer> 0 <id> JUSTID`: it reset the idle time and left the count unchanged, and another consumer's reclaim then passed over the entry. The call must use the IDs-only form; the full form spends a delivery.
- Turns readiness false while it retries, and true again when an attempt succeeds. It keeps beating the heartbeat.
- On shutdown, leaves the entry pending and stops.
- Gives up after the transient retry limit (default 10 minutes) on the same delivery. The delivery then counts as failed and the entry is left pending, like an unknown error.

The limit exists because a "transient" failure can turn out to be specific to one chunk. With it, a chunk has to fail for at least 30 minutes across three deliveries before it is dead-lettered, and one worker can dead-letter at most one chunk in that time. **A long outage therefore costs delay, not data.**

- A database connection error raised out of a handler (SQLAlchemy's operational and interface errors) is treated as transient even if the handler did not wrap it.
- A failure that raises unknown errors on every chunk (a bad deploy) is not caught by this. Those chunks become `failed` and are recovered with requeue.

## Dead-lettering

In this order:

1. Mark the ledger row `failed` with the error type and text. Changing no row is a success. An entry with no usable chunk ID skips this step.
2. In one Redis transaction: add an entry to the dead stream, and acknowledge the original (unconfirmed: to be proven by test).

The ledger is updated first. A crash between the two steps then leaves a pending entry that will be dead-lettered again, which is harmless. The other order could leave a dead-lettered entry whose row still says `queued`.

- The dead entry holds all the original fields plus `source_stream`, `source_id`, `deliveries`, `reason`, `error_type`, `error` (truncated to 500 characters), `failed_at` and `failed_by`. `source_stream` and `source_id` are the key of the stream the entry came from and the ID of the entry in that stream. Neither refers to the `sources` table.
- The reasons are `invalid_message`, `unknown_chunk`, `max_deliveries` and `permanent_error`.
- The dead stream has no consumer group. It is a view of recent failures for a person. **The durable record of a failure is the ledger row.**
- **At the memory limit the add to the dead stream is refused** like every other add. The entry stays pending and nothing is lost. What the consumer does then (how long it waits, what it reports) is decided when the consumer is built.

## Consumer names

- Consumer names are stable and come from configuration (for example `detector-1`), not from the container hostname.
- **Each running worker must have a name that no other running worker has.** Two workers with one name would each treat the other's work in progress as their own unfinished history and take it over.
- Workers are therefore separate named Compose services. `docker compose up --scale worker=N` is not supported.

## Recovery

`mobygrep-admin chunks requeue` puts chunks back on their stream **from the ledger**. Chunks are selected by status (`failed` or `queued`), and optionally by source, a "since" time, one chunk ID and a limit. It has a dry-run mode.

- A `failed` row is reset to `queued`, its message is added, then `enqueued_at` is set. A row whose audio has been purged is reported and skipped.
- A `queued` row has its message added again. This rebuilds the queue after Redis has lost data. It may add duplicates, which are harmless.
- The message is rebuilt from the ledger row and keeps the trace ID of the row.
- There is no "replay from the dead stream" command: the dead stream is capped and trimmed, and the ledger is not.
- Staged audio of `failed` chunks is kept seven days so that requeue works (see [storage.md](storage.md#retention-and-purge)).

## Defaults

All are settings. The variables are in [configuration.md](configuration.md#settings-variables).

| Setting | Default | Environment variable |
|---|---|---|
| Key prefix | `mobygrep` | `MOBYGREP_QUEUE__KEY_PREFIX` |
| Stream cap (live and archive) | 100,000 | `MOBYGREP_QUEUE__STREAM_MAX_LEN` |
| Block time | 2,000 ms | `MOBYGREP_QUEUE__BLOCK_MS` |
| Min-idle time | 120,000 ms | `MOBYGREP_QUEUE__MIN_IDLE_MS` |
| Reclaim interval | 30 s | `MOBYGREP_QUEUE__RECLAIM_INTERVAL_S` |
| Maximum deliveries | 3 | `MOBYGREP_QUEUE__MAX_DELIVERIES` |
| Transient retry limit | 600 s | `MOBYGREP_QUEUE__TRANSIENT_RETRY_LIMIT_S` |
| Archive backlog cap | 5,000 | `MOBYGREP_QUEUE__ARCHIVE_BACKLOG_CAP` |
| Dead stream cap | 10,000 | `MOBYGREP_QUEUE__DEAD_STREAM_MAX_LEN` |
| Socket timeout of the client | 10 s | `MOBYGREP_QUEUE_REDIS__SOCKET_TIMEOUT_S` |

## Metrics

The library registers twelve metrics. Their types, labels and meanings are in [observability.md](observability.md#metrics-the-foundation-registers).

- `mobygrep_queue_messages_enqueued_total`
- `mobygrep_queue_enqueue_duration_seconds`
- `mobygrep_queue_enqueue_refused_total`
- `mobygrep_queue_messages_processed_total`
- `mobygrep_queue_messages_failed_total`
- `mobygrep_queue_messages_duplicate_total`
- `mobygrep_queue_messages_dead_lettered_total`
- `mobygrep_queue_handler_retries_total`
- `mobygrep_queue_messages_reclaimed_total`
- `mobygrep_queue_messages_lost_total`
- `mobygrep_queue_handler_duration_seconds`
- `mobygrep_queue_oldest_pending_age_seconds`

Stream length, group lag and pending counts come from the Redis exporter.

## What this contract requires of other units

- The ingestion unit runs a periodic sweep that calls `enqueue` again for stale `queued` rows with no `enqueued_at`.
- The ingestion unit slows its archive replay when `enqueue` raises `QueueBackpressure`.
- The detection unit's handler follows the handler steps of the idempotency rule (see [idempotency.md](idempotency.md#what-the-handler-does)), raises `PermanentError` only for faults in the chunk itself, and raises `TransientError` for anything about a dependency.
- The detection unit loads its model before the consumer's first read, so that a cold start does not count against the min-idle time.
- Each worker service has its own consumer name.
- Tests in any unit put chunks on the queue through the shared test factories (create a committed source, stage a file and enqueue a chunk, build a message), not by writing stream entries by hand.

## Not used

Redis's producer-side deduplication (available from Redis 8.6) is not used: `enqueued_at` and the ledger check already make duplicates rare and cheap. It is a possible later addition.

## Known limits

Each limit is recorded here; none is solved yet.

1. **A full queue instance does not recover by draining.** At the memory limit every add is refused, including the producer's add that would trim, and so is the creation of a stream and its group. Acknowledging entries frees nothing. Only an explicit acknowledged-only trim frees memory, and nothing runs one automatically. See [Trimming](#trimming).
2. **A consumer cannot dead-letter at the memory limit.** The add to the dead stream is refused like any other add. See [Dead-lettering](#dead-lettering).
3. **A restart spends a delivery.** A worker that restarts for a reason unrelated to the chunk still uses up one delivery of the entry it held. Three quick restarts dead-letter the entry. See [Delivery count](#delivery-count).
4. **One read can return an entry from each stream.** The read that covers the live and the archive stream returns up to one entry for each stream, not one in total. How the consumer shapes its reads so that an archive backlog cannot delay live audio is fixed when the consumer is built. See [Consuming](#consuming).
5. **The retry policy of the Redis client.** The client library retries a failed command ten times by default, which hides an outage from the caller for a long time. The queue clients must set the policy explicitly; the value is fixed when the queue library is built. See [Consuming](#consuming).

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
| 2026-10-02 | Trimming: the cap is exact for acknowledged entries in the approximate form, with at most 10,000 removals in one call. The warning that a small cap trims nothing is dropped | The design assumed approximate trimming removes only whole blocks. The spike saw that this holds for the default policy only; with the acknowledged-only policy `MAXLEN ~ 2` left exactly 2 of 10 acknowledged entries |
| 2026-10-02 | Trimming: stated as skip-past, with the consequence for the length of the stream | The design left open whether trimming stops at the first unacknowledged entry. The spike saw it skip past |
| 2026-10-02 | Trimming and Known limits: "a full instance refuses new entries but workers can still drain it" is replaced. A full instance does not recover by draining; an explicit acknowledged-only trim is the remedy. Listed under Known limits | The spike saw every add refused at the memory limit, including the add that would trim, and saw acknowledging free no memory |
| 2026-10-02 | Dead-lettering: at the memory limit the add to the dead stream is refused. Listed under Known limits | Seen in the spike; the design did not cover it |
| 2026-10-02 | Delivery count: every read of a consumer's own history adds one, so a restart spends a delivery whatever its cause. Listed under Known limits | The design noted this as possible. The spike saw it on every history read |
| 2026-10-02 | Consuming: "a worker reads one message at a time" becomes "handles one message at a time"; one read can return an entry from each stream. Listed under Known limits | The spike saw that the count of a read applies to each stream |
| 2026-10-02 | Consuming: the queue clients must set their retry policy explicitly. Listed under Known limits | The spike saw the client library's default retry hide a timeout for 14 s to 59 s |
| 2026-10-02 | Consumer group: the group must exist before the first add | The spike saw acknowledged-only trimming remove entries from a stream that has no group |
| 2026-10-02 | Trimming and Known limits: creating a stream and its group is refused at the memory limit, so a program that starts while the instance is full may fail its startup check | Seen in the spike; the design did not cover it |
| 2026-10-02 | Trimming and Consuming: the two shapes of an entry whose payload was trimmed (an empty field map on a history read; a deleted ID on a reclaim), both counted as lost | The spike recorded the reply shapes. The design described one case with no shape |
| 2026-10-02 | Trimming: the dead stream can sit above its cap | The spike saw the approximate form remove only whole blocks under the default policy, which the dead stream uses |
| 2026-10-02 | Producing: "Redis unavailable or out of memory" names the error the client raises at the memory limit | The spike saw `redis.exceptions.OutOfMemoryError` for a refused add |
| 2026-10-10 | Message fields renamed: `audio_key` to `staged_audio_key`, `label` to `known_species_id` | They follow the ledger columns, from which every field is rebuilt |
