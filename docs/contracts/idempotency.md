# Idempotency

This document fixes the rule that makes at-least-once delivery safe: what the queue library does before it calls a handler, what a handler must do and in which order, and what each store holds if a process dies at each point. The detection unit writes its handler to these steps. The ingestion unit depends on repeat enqueues being safe. The scorecard unit uses the crash-window table for its failure drills.

## The rule

Processing a chunk any number of times leaves the same rows as processing it once, and no clip that a row points at ever has different content. The chunk ID (see [chunk-id.md](chunk-id.md)) is the key in every store.

## What the library does

Before it calls the handler:

1. It reads the ledger row of the chunk. If the chunk is already `processed`, it acknowledges the message, counts it as a duplicate and does not call the handler. This makes duplicate messages nearly free.

## What the handler does

The detection unit writes the handler. It continues from step 1:

2. It writes clips to the clip store under keys derived from the chunk ID and the position of the cut. A clip must be a deterministic cut of the staged audio, so that the same key always means the same content (see [storage.md](storage.md#a-clip-is-a-deterministic-cut)).
3. It opens one database transaction. **The first statement of the transaction is the conditional "mark processed"** (see [schema.md](schema.md#ledger-operations)). If that changed no row, another attempt has already committed: roll back and return. Otherwise insert the detections and the embeddings, and commit.
4. It returns. Only then does the library acknowledge the message.

## Why this order holds

- A crash after step 2 leaves objects and no rows. The retry writes the same keys again, which the store treats as "already there".
- A crash after step 3 leaves an unacknowledged message. The retry stops at step 1.
- Two attempts at the same time (a slow handler whose entry another worker claimed) both reach step 3. The first statement locks the ledger row, so the second attempt waits, then finds the row already `processed` and rolls back (unconfirmed: to be proven by test). The detections of a chunk therefore always come from exactly one attempt.
- If the two attempts cut differently (one used the fallback detector), the clips of the loser sit under keys that no row points at. They are unreferenced and harmless, and are not cleaned up.

## Crash windows

For each point after which a process can die: the state of each store, and why the retry is safe.

| The process dies after | Ledger row | Stream | Staging and clip store | Why the retry is safe |
|---|---|---|---|---|
| Ingestor: staging the audio, before the ledger insert | None | No entry | Staged file exists | The ingestor has not saved its bookmark, so it repeats the chunk: the file is overwritten with the same bytes and the row is inserted. If it never repeats, the file is an orphan and is deleted after the orphan retention |
| Producer: ledger insert committed, before the add | `queued`, `enqueued_at` null | No entry | Staged file exists | `enqueue` again, or the ingestion unit's sweep of stale `queued` rows, adds the entry. The row keeps its trace ID |
| Producer: the add, before `enqueued_at` is set | `queued`, `enqueued_at` null | One entry | Staged file exists | A later `enqueue` adds a second entry. The first to finish marks the row `processed`; the other is acknowledged as a duplicate at step 1 |
| Producer: `enqueued_at` set, before the ingestor saves its bookmark | `queued`, `enqueued_at` set | One entry | Staged file exists | `enqueue` again finds `enqueued_at` and adds nothing |
| Worker: entry delivered, before any work | `queued` | Entry pending under that worker | No clips | The worker's own restart, or another worker after the min-idle time, takes the entry again. The delivery count rises by one on either path. A worker that restarts three times in a row therefore dead-letters the entry without finishing it; the two dead-lettering rows below then apply |
| Worker: clips written, before the transaction commits | `queued` | Entry pending | Clips exist with no rows | The retry writes the same keys, and the store treats an existing key as success; then it commits. The local object store refuses the second put with "precondition failed", which `put` treats as success. R2 does the same (unconfirmed: to be proven by test) |
| Worker: transaction committed, before the acknowledge | `processed`, detections and embeddings present | Entry pending | Clips exist, rows point at them | The redelivery stops at step 1: it is acknowledged as a duplicate and the handler is not called. This check comes before the delivery-count check, so a chunk that succeeded is never dead-lettered |
| Worker: two attempts overlap | `processed` by the first to commit | Both entries acknowledged in the end | The clips of the loser may sit under unreferenced keys | The conditional update locks the row; the second attempt changes no row and rolls back (unconfirmed: to be proven by test) |
| Consumer, dead-lettering: row marked `failed`, before the dead entry is added and the original acknowledged | `failed` | Original still pending | Staged file kept seven days | The entry is taken again and dead-lettered again; marking a `failed` row failed changes nothing and is a success. If the handler runs and now succeeds, `failed` to `processed` is an allowed transition |
| Consumer, dead-lettering: after the Redis transaction | `failed` | Dead entry added, original acknowledged | Staged file kept seven days | Nothing to retry. Recovery is requeue from the ledger |
| Admin requeue: row reset to `queued`, before the add | `queued`, `enqueued_at` null | No entry | Staged file exists (requeue refuses a purged row) | The sweep of stale `queued` rows picks it up |
| Admin requeue: the add, before `enqueued_at` is set | `queued`, `enqueued_at` null | One entry | Staged file exists | The same as the producer's duplicate case |
| Redis loses its data | `queued`, `enqueued_at` set | No entry | Staged file exists; audio of `queued` chunks is never deleted by age | `requeue --status queued` rebuilds the stream from the ledger. Duplicates are harmless |

Two cases are outside the table because no process dies in them:

- **The queue instance is at its memory limit.** The consumer cannot add to the dead stream, so an entry that must be dead-lettered stays pending. If the ledger row was already marked `failed`, this is the same state as the first dead-lettering row of the table: nothing is lost. See [queue.md](queue.md#dead-lettering).
- **A pending entry loses its payload.** This cannot happen under the acknowledged-only trimming policy. If it does, the consumer acknowledges the entry and counts it as lost; the ledger row stays `queued` with `enqueued_at` set, and `requeue --status queued` recovers the chunk. See [queue.md](queue.md#trimming).

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
| 2026-10-02 | The row "entry delivered, before any work" states that a restart alone spends a delivery, and that three restarts dead-letter the entry | The spike saw every read of a consumer's own history add one to the delivery count |
| 2026-10-02 | Added the memory-limit case below the table | The spike saw the add to the dead stream refused at the memory limit |
