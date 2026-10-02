# Storage

This document fixes the two stores that hold audio: the staging store, where the audio of a chunk waits between the ingestor and a worker, and the clip store, where the audio of each detection is kept. The ingestion unit writes staged audio and runs the purge. The detection unit reads staged audio, decodes it and writes clips. The search unit turns clip keys into URLs. The model unit decides where decoding lives. The production deployment unit supplies the R2 bucket and its credentials.

## Status

- **Draft, 2026-10-02.** The code that implements this contract is not written yet. This document is what it will be built to.
- **Unconfirmed behaviour:**
  - The clip store behaves on real R2 as stated here: the create-if-absent put and presigned URLs ([Clip store](#clip-store), [Clip URLs](#clip-urls)). Both were observed on the local object store only.
  - Staging writes are atomic, so a reader never sees a partial file ([Staging store](#staging-store)).
  - The two differences between the backends that the key and bucket rules rest on: R2 does not create a bucket on a put, and the local object store cannot hold a key that is a prefix of another ([Clip store](#clip-store), [Clip keys](#clip-keys)).
- **Provisional:** the zero-padding width of the clip key (eight digits), until the storage code exists.
- **Completed by:** the storage code (both stores, the purge, and contract tests that run against every implementation; the run against real R2), and the final consistency review.

## Two stores

Staging and clips are two interfaces. They differ in backend, key scheme, retention and operations.

- Chunk audio waits on a shared Docker volume, not in object storage. That is free and fast, and it keeps a network dependency out of the hot path.
- Clips go to S3-compatible storage: Cloudflare R2 in production, and SeaweedFS locally (Compose service `objectstore`, S3 port 8333).

## Staging store

Holds chunk audio between the ingestor and a worker.

```python
class StagingStore(Protocol):
    def write(self, key: str, data: bytes) -> None:
        """Write atomically. Overwriting an existing key is allowed."""

    def read(self, key: str) -> bytes:
        """Raises StagedAudioNotFound."""

    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> bool:
        """Delete if present. Returns whether a file was removed."""

    def usage(self) -> StagingUsage:
        """Total and free bytes of the disk that holds the root."""

    def iter_older_than(self, cutoff: datetime) -> Iterator[StagedFile]:
        """Files whose modification time is before the cutoff."""


def staging_key(chunk_id: ChunkId, extension: str) -> str:
    """`<chunk_id>.<extension>`."""
```

- Writes are atomic: a reader never sees a partial file (unconfirmed: to be proven by test).
- Keys are relative, contain no `..`, and resolve inside the root.
- `usage()` asks the operating system for the free space of the disk. It does not add up file sizes. The question that matters is how full the disk is, and staging shares the disk with Postgres.
- The interface is synchronous. Async callers wrap calls in a thread.
- Implementations: a filesystem store on the shared volume (it also exposes the local path of a key, which is not part of the protocol), and an in-memory fake for unit tests in every unit, whose usage figures a test can set.
- Missing staged audio is a permanent error for a handler (`StagedAudioNotFound`; see [observability.md](observability.md#errors)).
- The marker file `.mobygrep-staging` at the root is created by the ingestor's startup check and required by the worker's (see [observability.md](observability.md#startup-checks)). An ingestor on the host with a worker in a container do not share a directory: run both on the host or both in containers. The marker is never an orphan: the listing of the staging store skips it and the purge never deletes it.

## What a staged file is

- The staged file is the audio **as the ingestor fetched it, not transcoded**. For live audio that is the stream segment exactly as downloaded: AAC audio in an MPEG-TS container, 48 kHz, mono or stereo depending on the hydrophone.
- The extension of the key names the container (`ts` for live segments). The reader identifies the format from the content of the file; the extension is for people.
- For archive and reference audio, the ingestion unit cuts long recordings into chunks. It stages them in the source's own format or another compact one, never as larger decoded audio without accounting for the disk.
- The reason is disk space. Live audio arrives at about 10 GB a day compressed, which is about 20 GB at the 48-hour retention. Decoded to the model's input format (mono, 32 kHz, 16-bit) it is 640 KB for each chunk: about 39 GB a day and 77 GB at 48 hours, on an 80 GB disk.

## Who decodes

Decoding, mixing down to mono, resampling to the model's rate and any normalisation happen **on the worker side**.

- The recommended home for that code is `mobygrep.inference`, because the search API has to prepare uploaded audio in exactly the same way. The model unit and the detection unit settle its final home.
- The decoder library belongs to the `inference` dependency set, and so ships only in the worker image.

## Retention and purge

- Staged audio of a `processed` chunk is deleted once 48 hours have passed since `completed_at`.
- Staged audio of a `failed` chunk is kept 7 days since `completed_at`. A failed chunk can be requeued only while its audio exists, and the people who would requeue it may not notice a failure within two days.
- **Audio of a chunk that is still `queued` is never deleted by age.** A backlog must not eat its own input.
- A file with no ledger row at all is an orphan. It is deleted once it is older than the orphan retention (7 days).
- The three retention periods and the disk floor below are settings (see [configuration.md](configuration.md#settings-variables)).
- The foundation provides one function that does this in batches, records `audio_purged_at` in the ledger, treats a file that is already missing as success, and sets the `mobygrep_storage_staging_free_bytes` gauge. **The foundation does not schedule it.**

```python
async def purge_staged_audio(
    sessions: async_sessionmaker[AsyncSession],
    staging: StagingStore,
    settings: StagingSettings,
    *,
    now: datetime,
    batch_size: int = 500,
) -> PurgeResult:
    """Delete staged audio that is past retention and record it in the ledger."""
```

## Obligations on the ingestion unit

1. **Run the purge periodically.**
2. **Stop staging when the disk is nearly full.** If workers stop, unprocessed audio accumulates and is never purged, and the disk is shared with Postgres. Before it stages a chunk, the ingestor checks `usage()`. When the free space is below the minimum (default 10 GB, a setting) it does not stage, and it records a feed gap.

## Clip store

Holds the saved audio of detections.

```python
class ClipStore(Protocol):
    def put(
        self, key: str, data: bytes, *, content_type: str, overwrite: bool = False
    ) -> None:
        """Store a clip. By default create-if-absent; an existing key is success."""

    def get(self, key: str) -> bytes:
        """Raises ClipNotFound."""

    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None:
        """Idempotent."""

    def url_for(self, key: str, *, expires: timedelta | None = None) -> str:
        """A URL a browser can play without credentials."""


def clip_key(
    chunk_id: ChunkId, start_offset_ms: int, duration_ms: int, extension: str
) -> str:
    """`clips/v1/<chunk_id>/<offset, zero-padded>-<duration, zero-padded>.<extension>`."""
```

- `put` is create-if-absent by default and treats "already there" as success. It does not report whether it created the object: a retry after a lost response would see "already exists" for an object the caller itself just wrote.
- Correctness rests on the deterministic key. The condition only guards against accidental overwrite. `overwrite=True` is for deliberate backfills.
- On the local object store the condition is enforced: a second put with `If-None-Match: *` was refused with "precondition failed" (HTTP 412) and the first object, with its content type, was untouched; the same two puts without the condition replaced the object (confirmed by spike, 2026-10-02). `put` treats that refusal as success. The same behaviour on real R2 is (unconfirmed: to be proven by test).
- `put` sets the content type and a long immutable cache lifetime.
- The S3 implementation uses only put, get, head and delete, plus presigning. It uses no listing, tagging, ACLs, versioning or batch delete: according to R2's documentation it does not implement several of these.
- A missing key answers differently to the two reads. On the local object store a GET returns the named error "no such key", and a HEAD returns a bare 404 with no named error, because a HEAD reply has no body (confirmed by spike, 2026-10-02). `exists` must test for the 404. R2 answers the same way (unconfirmed: to be proven by test).
- **The local object store creates a bucket on the first put to it** (confirmed by spike, 2026-10-02). R2 does not (unconfirmed: to be proven by test). A wrong bucket name therefore passes locally on a write and would fail on R2. A GET or a HEAD does not create a bucket, which is why the startup check reads (see [observability.md](observability.md#startup-checks)).
- Implementations: the S3 store (one configuration for both backends; region `auto` for R2 and `us-east-1` locally), an in-memory fake whose `url_for` returns a predictable fake URL, and a small async adapter for the API.

## Clip keys

- The form is `clips/v1/<chunk_id>/<offset>-<duration>.<extension>`, with the offset and the duration in milliseconds and zero-padded. For example: `clips/v1/orcasound_lab/1541061134/live042/00002500-00001500.flac`. The padding width (eight digits) is provisional until the storage code exists.
- A key is derived only from the chunk ID and the offset and duration of the detection, so a retry that makes the same cut produces the same key.
- The `v1` segment allows a future change in how clips are cut to produce new keys.
- A key always ends in a file extension, and no key is a prefix of another. The local store maps keys onto a directory tree and cannot hold both `a/b` and `a/b/c` (unconfirmed: to be proven by test); R2 can. The rule keeps the two in step.
- **Only keys are stored in the database, never URLs.**

## A clip is a deterministic cut

A clip is a deterministic cut of the staged audio. The same key therefore always holds the same content, whichever attempt wrote it.

- This is what makes "create only if absent" safe: an attempt that finds the key taken can use the object that is there.
- An attempt that cuts differently (another detector, for example) gets a different key.
- Objects written by an attempt that later lost stay in the store with no row pointing at them. They are not cleaned up: the store interface has no listing operation, and the waste is a few clips for each rare event.

See [idempotency.md](idempotency.md) for how the handler uses this.

## Clip URLs

- In `presigned` mode, `url_for` signs with a second client built on the **public** endpoint. The host is part of the signature, so a URL signed for the in-network name `objectstore:8333` cannot be rewritten to `localhost:8333` afterwards.
- This works on the local object store: a URL signed by a client on the public endpoint was fetched from the host with no credentials, and the same URL signed for the internal host and then rewritten was refused as a signature mismatch (confirmed by spike, 2026-10-02). The local store validates signatures, so a wrong presign cannot pass locally.
- The public endpoint setting must be character for character the host the browser uses. A URL signed for `localhost:8333` and fetched through `127.0.0.1:8333` was refused (confirmed by spike, 2026-10-02).
- In production both endpoints are the same R2 URL. Presigned URLs on real R2 are (unconfirmed: to be proven by test).
- In `public` mode, `url_for` returns the public base URL joined with the key, with no network call.
- The default lifetime is 3,600 s.
- According to R2's documentation, its presigned URLs work only on R2's own S3 domain, not on a custom domain, and expire after at most seven days. A Grafana panel therefore cannot hold a permanent presigned link. **The search unit is expected to add an API route that redirects to a fresh URL.** The foundation does not build it.

## Errors

Three kinds (see [observability.md](observability.md#errors)):

| Error | When |
|---|---|
| `ClipNotFound` | The key has no object |
| `ClipStoreUnavailable` | Timeouts, 5xx responses, throttling |
| `ClipStoreMisconfigured` | Credentials, bucket |

The last two are both `TransientError` for a handler: neither is the fault of the chunk. They differ only at startup, where a misconfiguration stops the program and unavailability does not.

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
| 2026-10-02 | Clip store: the local object store creates a bucket on the first put, so a wrong bucket name is not caught locally on a write | Seen in the spike. R2 does not create buckets this way, so the difference is stated |
| 2026-10-02 | Clip store: `exists` must test for a bare 404, not for the named "no such key" error | The spike saw a HEAD of a missing key return 404 with no named error |
| 2026-10-02 | Clip URLs: the public endpoint must match the browser's host exactly | The spike saw a URL signed for `localhost:8333` refused through `127.0.0.1:8333` |
