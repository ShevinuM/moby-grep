# Chunk ID

This document fixes the format of a chunk ID, how each part is derived, and where the ID is used. The ingestion unit creates chunk IDs. The detection unit reads them and derives clip keys from them. The search unit displays them and queries by prefix. Every store uses the chunk ID as its idempotency key.

## Status

- **Draft, 2026-10-02.** The code that implements this contract is not written yet. This document is what it will be built to.
- **Unconfirmed behaviour:** none. Nothing in this document depends on how an external dependency behaves.
- **Provisional:** the normaliser's hash algorithm, hash length and default maximum length under [Normalising external names](#normalising-external-names), and the zero-padding width in the clip key under [Where the chunk ID is used](#where-the-chunk-id-is-used). They are the intended values and become final when the code exists.
- **Completed by:** the chunk ID type in `mobygrep.shared.chunk_id` (confirms the normaliser values; every line under [Examples](#examples) is then parsed by a test), and the final consistency review.

## Format

- The form is `<source>/<group>/<item>`: exactly three segments separated by `/`.
- Each segment is non-empty, starts with a lowercase letter or a digit, and contains only lowercase letters, digits, `_` and `-`. As a pattern for one segment: `[a-z0-9][a-z0-9_-]*`, matched against the whole segment. Only ASCII letters and digits are allowed.
- The whole ID is at most 200 characters.
- Parsing never repairs its input: it does not strip spaces and does not change case.
- An invalid ID raises `InvalidChunkId`, which is a permanent error (see [observability.md](observability.md#errors)).

The type, for reference:

```python
@dataclass(frozen=True, slots=True)
class ChunkId:
    source: str
    group: str
    item: str

    @classmethod
    def parse(cls, value: str) -> "ChunkId":
        """Parse and validate `<source>/<group>/<item>`. Raises InvalidChunkId."""

    def __str__(self) -> str:
        """Canonical text form."""


class SourceKind(StrEnum):
    LIVE = "live"
    ARCHIVE = "archive"
    REFERENCE = "reference"
```

`ChunkId` works as a pydantic field type: it validates from a string and serialises to a string. An invalid `ChunkId` object cannot exist: constructing one directly applies the same rules as `parse`.

## Where the parts come from

- `source` is the slug of a row in the `sources` table (see [schema.md](schema.md)).
- `group` and `item` come from the source data and are never invented.
  - **Live audio:** the stream folder and the segment file name without its extension, giving `orcasound_lab/1541061134/live042`.
  - **Archive and reference audio:** a collection or file identifier, and a chunk index zero-padded to six digits, giving `watkins/best_of/6102500a_000000`.
- The same audio always yields the same chunk ID.
- Source kinds are exactly `live`, `archive` and `reference`.

## Examples

```
orcasound_lab/1541061134/live042
watkins/best_of/6102500a_000000
```

Every line in the block is a valid chunk ID, and nothing else may be put in it. The first is live audio. The second is reference audio. The ingestion unit fixes the real group and item for each archive and reference source.

## Invalid examples

| ID | Why it is rejected |
|---|---|
| `orcasound_lab/live042` | Two segments |
| `orcasound_lab/1541061134/live042/extra` | Four segments |
| `orcasound_lab//live042` | An empty segment |
| `Orcasound_Lab/1541061134/live042` | Upper case |
| `orcasound lab/1541061134/live042` | A space |
| `orcasound_lab/1541061134/live042.ts` | A dot |
| `_orcasound/1541061134/live042` | A segment that starts with `_` |
| `orcasound_lab/-1541061134/live042` | A segment that starts with `-` |
| `a/b/` followed by 197 `c` characters | 201 characters; the limit is 200 |

## A chunk ID identifies; it does not order

Live segment names are not zero-padded: `live999` sorts after `live1000` as text. Anything that needs chunks in time order uses `started_at` (see [schema.md](schema.md)). `ChunkId` defines no ordering.

## Normalising external names

A helper, `normalise_segment`, turns an arbitrary external name (an archive file name, for example) into a valid segment.

- A name that is already a valid segment, and is not longer than the maximum length, is returned unchanged.
- Any other name is rewritten into a base: it is made lowercase, each run of characters outside `a-z`, `0-9`, `_` and `-` is replaced by one `_`, and leading and trailing `_` and `-` are removed.
- Normalising loses information. Two different names could become the same segment, and the second file would then be skipped as a duplicate. To prevent that, when the helper had to change its input it appends a short hash of the **original** name. The base is shortened first, so that the result is not longer than the maximum length. When nothing is left of the base, the result is the hash alone.
- The result is always a valid segment.
- The output is the same for the same input every time, in every process.

Provisional until the code exists: the hash is the first 8 hexadecimal characters of the SHA-256 of the original name encoded as UTF-8, appended as `_<hash>`; the default maximum length of a segment is 64 characters. For example `6102500a` stays `6102500a`, and `6102500A.wav` becomes `6102500a_wav_<hash>`. `A.wav` and `a.wav` give the same base `a_wav` and different hashes. `.wav` gives the base `wav`.

## Where the chunk ID is used

The chunk ID is the idempotency key across every store:

| Where | Form |
|---|---|
| The ledger | Primary key `chunks.chunk_id`; foreign key in `detections` ([schema.md](schema.md)) |
| The queue message | The `chunk_id` field ([queue.md](queue.md#message-version-1)) |
| The staged audio key | `<chunk_id>.<extension>`, for example `orcasound_lab/1541061134/live042.ts` ([storage.md](storage.md#staging-store)) |
| The clip key | `clips/v1/<chunk_id>/<offset>-<duration>.<extension>`, with offset and duration in milliseconds and zero-padded, for example `clips/v1/orcasound_lab/1541061134/live042/00002500-00001500.flac` ([storage.md](storage.md#clip-keys)) |
| Logs | The `chunk_id` field ([observability.md](observability.md#logs)) |

A chunk ID is never a metric label.

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
