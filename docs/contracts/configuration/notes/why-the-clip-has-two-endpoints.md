# Why the clip store has two endpoints

One address cannot serve both the programs and the browser.

```
┌──────────── Compose network ────────────┐
│                                         │
│  worker / API ──────────► objectstore   │
│               objectstore:8333    ▲     │
└───────────────────────────────────┼─────┘
                                    │ localhost:8333
                                    │ (published port)
                           browser on the host
```

| Address | A program in a container | A browser on the host |
|---|---|---|
| `objectstore:8333` | Works | Fails: the name exists only inside the Compose network |
| `localhost:8333` | Fails: `localhost` is the container itself | Works |

So each side gets its own setting:

| Setting | What uses it | Local value |
|---|---|---|
| Endpoint | The programs, to save and read clips | `objectstore:8333` in Compose, `localhost:8333` on the host |
| Public endpoint | `url_for`, as the host inside each presigned URL | `localhost:8333` in both columns |

A presigned URL is a link that lets a browser get one clip for a limited time. The host name is part of its signature:

```
signed for objectstore:8333, then edited to localhost:8333   →  rejected (signature mismatch)
signed for localhost:8333 from the start                     →  accepted
signed for localhost:8333, opened as 127.0.0.1:8333          →  rejected (the clip store compares the text)
```

Signing is a calculation, so the program does not connect to the public endpoint. In production, the programs and the browsers use the same R2 URL. Leave the public endpoint unset, and set the region to `auto`. See [storage.md](../../storage.md#clip-urls).
