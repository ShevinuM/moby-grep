# The staging root

The staging root is where the ingestor leaves audio for the workers. The two must use the same directory.

| Where the programs run | Staging root | What it is |
|---|---|---|
| On the host | `/tmp/mobygrep-staging` | A local directory, outside the repository |
| In Compose | `/var/lib/mobygrep/staging` | A path on a named volume that the containers share |

```
Supported                                          Not supported

Both on the host                                   Ingestor on the host, worker in Compose
 ingestor ──► /tmp/mobygrep-staging ──► worker      ingestor ──► /tmp/mobygrep-staging
                                                                      ✗ worker cannot see it
Both in Compose                                     worker   ──► named volume (empty)
 ingestor ──► named volume ──► worker
                                                   (the reverse fails the same way)
```

Run the ingestor and the workers in the same place. The worker's startup check requires the marker file `.mobygrep-staging`, which the ingestor creates. A worker that cannot see the ingestor's directory usually fails that check (see [observability.md](../../observability.md)).
