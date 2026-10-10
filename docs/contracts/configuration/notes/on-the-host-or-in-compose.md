# On the host or in Compose

A program reaches the services by a different address in each place:

```
On the host                              In Compose

your Mac                                 ┌──────── Compose network ────────┐
 worker ──► localhost:5432 ─┐            │ worker ──► postgres:5432         │
            (published      │            │        ──► redis-queue:6379      │
             ports)         ▼            │        ──► objectstore:8333      │
         ┌── Docker ──────────────┐      │                                  │
         │ postgres, redis, ...   │      │ postgres, redis, ...             │
         └────────────────────────┘      └──────────────────────────────────┘
```

`.env` holds the host values. The `environment:` block of each program's Compose service overrides the six variables that differ:

| Variable | On the host (`.env`) | In Compose (`environment:`) |
|---|---|---|
| `MOBYGREP_POSTGRES__HOST` | `localhost` | `postgres` |
| `MOBYGREP_QUEUE_REDIS__URL` | `redis://localhost:6379/0` | `redis://redis-queue:6379/0` |
| `MOBYGREP_CACHE_REDIS__URL` | `redis://localhost:6380/0` | `redis://redis-cache:6379/0` |
| `MOBYGREP_STAGING__ROOT` | `/tmp/mobygrep-staging` | `/var/lib/mobygrep/staging` |
| `MOBYGREP_CLIPS__ENDPOINT_URL` | `http://localhost:8333` | `http://objectstore:8333` |
| `MOBYGREP_TRACING__OTLP_ENDPOINT` | `http://localhost:4318` | `http://tempo:4318` |

The `migrate` service overrides two more, because migrations change tables and `mobygrep_writer` cannot:

| Variable | Programs | `migrate` |
|---|---|---|
| `MOBYGREP_POSTGRES__USER` | `mobygrep_writer` | `mobygrep_owner` |
| `MOBYGREP_POSTGRES__PASSWORD` | the writer's password | the owner's password |

A missed override makes the program in the container connect to `localhost`, which is the container itself. Its startup check then fails.
