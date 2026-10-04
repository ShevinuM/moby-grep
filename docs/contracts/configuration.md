# Configuration

This document fixes how the programs are configured: the naming rule for environment variables, which program reads which variables, the default of each, and the value each has on the host and inside Compose. Every unit adds its own settings by the same rules. The production deployment unit supplies the production values and the secrets.

## Conventions

- The environment prefix is `MOBYGREP_` and the nested delimiter is `__`: for example `MOBYGREP_POSTGRES__HOST`, `MOBYGREP_CLIPS__BUCKET`.
- Precedence: an environment variable, then the `.env` file, then the default.
- Unknown variables are ignored, because one `.env` file serves all programs.
- Constructing the settings object is the first thing each program does. A validation error prints which variable is wrong, never its value, and the program exits non-zero before any connection is attempted.
- Clip store settings deliberately do not use `AWS_*` names, so boto3 never picks up ambient credentials.
- The Postgres user and password are the same variables for every program, with different values per service: the three programs, and the queue and chunk commands of the admin command, use the application role `mobygrep_app`; the `migrate` service uses the database owner.
- The consumer name is the worker's own setting, not part of the queue settings, because the ingestor and the admin command load the queue settings too and have no consumer. It is required and has no default.
- The stream cap, the block time and the other queue values are settings so that tests can shrink them. The key prefix is a setting so that each test gets its own streams.
- Durations carry their unit in the name (`_MS`, `_S`, `_HOURS`).
- Two sets of values exist. `.env.example` holds working values for programs run **on the host** against the local Compose stack: hosts are `localhost`, with the ports the local override file publishes on `127.0.0.1`. Inside Compose, the `environment:` block of each program service overrides the host names with service names.
- An optional variable is left out of an env file when it is not wanted. `NAME=` sets the empty string; it does not unset the variable.
- No secret is ever committed. `.env` is git-ignored. Every value in `.env.example` is for local development only.

## Which program reads what

| Program | Groups of settings |
|---|---|
| Ingestor | postgres, queue redis, queue, staging, ops, log, tracing |
| Worker | postgres, queue redis, queue, staging, clips, ops, log, tracing, plus its own consumer name |
| API | postgres, cache redis, clips, ops, log, tracing |
| Admin command | postgres, queue redis, queue, log |

## Settings variables

These are the variables the settings classes read. "On the host" is the value in `.env.example`, except for the two secrets, whose values are not repeated here; a dash means the variable is not in that file and its default applies. "In Compose" is what a program service sees inside the Compose network; "same" means the host value is correct there too.

<!-- settings-variables:start -->

| Variable | Meaning | Default | On the host | In Compose |
|---|---|---|---|---|
| `MOBYGREP_POSTGRES__HOST` | Postgres host | none; required | `localhost` | `postgres` |
| `MOBYGREP_POSTGRES__PORT` | Postgres port | 5432 | `5432` | `5432` |
| `MOBYGREP_POSTGRES__DATABASE` | Database name | none; required | `mobygrep` | same |
| `MOBYGREP_POSTGRES__USER` | Role | none; required | `mobygrep_app` | same; the `migrate` service uses the owner |
| `MOBYGREP_POSTGRES__PASSWORD` | Password (secret) | none; required | a local-only value | same; the `migrate` service uses the owner's password |
| `MOBYGREP_QUEUE_REDIS__URL` | URL of the `redis-queue` instance | none; required | `redis://localhost:6379/0` | `redis://redis-queue:6379/0` |
| `MOBYGREP_QUEUE_REDIS__SOCKET_TIMEOUT_S` | Socket timeout of the queue clients | 10 s | — | default |
| `MOBYGREP_CACHE_REDIS__URL` | URL of the `redis-cache` instance | none; required | `redis://localhost:6380/0` | `redis://redis-cache:6379/0` |
| `MOBYGREP_QUEUE__KEY_PREFIX` | Prefix of the stream keys | `mobygrep` | — | default |
| `MOBYGREP_QUEUE__STREAM_MAX_LEN` | Cap of the live and the archive stream | 100,000 | — | default |
| `MOBYGREP_QUEUE__BLOCK_MS` | Blocking read time | 2,000 ms | — | default |
| `MOBYGREP_QUEUE__MIN_IDLE_MS` | Idle time before an entry can be reclaimed | 120,000 ms | — | default |
| `MOBYGREP_QUEUE__RECLAIM_INTERVAL_S` | How often a worker looks for idle entries | 30 s | — | default |
| `MOBYGREP_QUEUE__MAX_DELIVERIES` | Deliveries to a handler before dead-lettering | 3 | — | default |
| `MOBYGREP_QUEUE__TRANSIENT_RETRY_LIMIT_S` | How long one delivery is retried in place | 600 s | — | default |
| `MOBYGREP_QUEUE__ARCHIVE_BACKLOG_CAP` | Archive backlog above which archive enqueues are refused | 5,000 | — | default |
| `MOBYGREP_QUEUE__DEAD_STREAM_MAX_LEN` | Cap of the dead stream | 10,000 | — | default |
| `MOBYGREP_STAGING__ROOT` | Root directory of staged audio | none; required | `/tmp/mobygrep-staging` | `/var/lib/mobygrep/staging` |
| `MOBYGREP_STAGING__RETENTION_HOURS` | Keep staged audio of `processed` chunks | 48 h | — | default |
| `MOBYGREP_STAGING__FAILED_RETENTION_HOURS` | Keep staged audio of `failed` chunks | 168 h | — | default |
| `MOBYGREP_STAGING__ORPHAN_RETENTION_HOURS` | Keep files that have no ledger row | 168 h | — | default |
| `MOBYGREP_STAGING__MIN_FREE_BYTES` | Free disk space below which the ingestor stops staging | 10 GB (10 × 1024³ bytes) | — | default |
| `MOBYGREP_CLIPS__ENDPOINT_URL` | S3 endpoint the programs use | none; required | `http://localhost:8333` | `http://objectstore:8333` |
| `MOBYGREP_CLIPS__PUBLIC_ENDPOINT_URL` | Endpoint a browser can reach; optional | the endpoint URL | `http://localhost:8333` | same |
| `MOBYGREP_CLIPS__REGION` | `auto` for R2, `us-east-1` locally | `auto` | `us-east-1` | same |
| `MOBYGREP_CLIPS__ACCESS_KEY_ID` | Access key ID | none; required | `mobygrep-local` | same |
| `MOBYGREP_CLIPS__SECRET_ACCESS_KEY` | Secret key (secret) | none; required | a local-only value | same |
| `MOBYGREP_CLIPS__BUCKET` | Bucket | none; required | `mobygrep-clips` | same |
| `MOBYGREP_CLIPS__URL_MODE` | `presigned` or `public` | `presigned` | — | default |
| `MOBYGREP_CLIPS__PUBLIC_BASE_URL` | Base URL in `public` mode; required in that mode | none | — | unset |
| `MOBYGREP_CLIPS__URL_LIFETIME_S` | Lifetime of a presigned URL; at most 604,800 s | 3,600 s | — | default |
| `MOBYGREP_OPS__HOST` | Bind host of the ops endpoints | `0.0.0.0` | — | default |
| `MOBYGREP_OPS__PORT` | Port of the ops endpoints | API 8000, ingestor 9101, worker 9102 | — | default |
| `MOBYGREP_OPS__SHUTDOWN_GRACE_S` | Shutdown grace period | 30 s | — | default |
| `MOBYGREP_OPS__LIVENESS_TIMEOUT_S` | Heartbeat age at which a program is not live | 120 s | — | default |
| `MOBYGREP_LOG__LEVEL` | Log level | `INFO` | — | default |
| `MOBYGREP_LOG__FORMAT` | `auto`, `json` or `console` | `auto` | — | default |
| `MOBYGREP_TRACING__ENABLED` | Export traces | `false` | `false` | `false` until the tracing backend exists |
| `MOBYGREP_TRACING__OTLP_ENDPOINT` | OTLP HTTP endpoint | `http://localhost:4318` | `http://localhost:4318` | `http://tempo:4318` |
| `MOBYGREP_TRACING__EXPORT_TIMEOUT_S` | Export timeout | 3 s | — | default |
| `MOBYGREP_CONSUMER_NAME` | The worker's consumer name; required | none | `detector-1` | `detector-1` for the one worker service |

<!-- settings-variables:end -->

Notes on the table:

- **Six variables differ between the host and Compose**, so the `environment:` block of a program service must override them: the Postgres host, the two Redis URLs, the staging root, the clip endpoint and the tracing endpoint. The `migrate` service also overrides the Postgres user and password.
- **Why the clip store has two endpoints.** A presigned URL is signed for a host name. A URL signed for the in-network name `objectstore:8333` cannot be opened from a browser on the host. The public endpoint therefore stays `http://localhost:8333` in both columns. It must be character for character the host the browser uses: `localhost` and `127.0.0.1` are not interchangeable. In production both endpoints are the same R2 URL, the public one is left unset, and the region is `auto`. See [storage.md](storage.md#clip-urls).
- **The ops port** has a default per program and is not in `.env.example`, because one file serves every program run on the host and they would collide. For the API it is the port the API itself serves on.
- **The staging root** is a path on a shared named volume in Compose. On the host it is a local directory outside the repository. An ingestor on the host with a worker in a container (or the reverse) do not share a staging directory. That combination is not supported.
- **The socket timeout** (10 s) is deliberately longer than the block time (2 s). A blocking read of 2 s on a client with a 10 s socket timeout returns empty and raises nothing.
- **The consumer name** must be unique among running workers. See [queue.md](queue.md#consumer-names).
- **Secrets.** The two values marked "a local-only value" are set in `.env.example`, are for the local Compose stack only, and are not written here.

## Variables not read by the settings classes

These variables are read by the image build, the Compose files, an init script or the test harness, not by a settings class. All names are provisional until the files that use them exist.

| Variable | Read by | Meaning |
|---|---|---|
| `MOBYGREP_BUILD_REVISION` | Every program, directly | The git revision, set when the image is built. It goes into the build-info metric; `unknown` when absent. See [observability.md](observability.md#metrics) |
| `POSTGRES_USER` | The Postgres image; the `migrate` service | The database owner. Used only to run migrations |
| `POSTGRES_PASSWORD` | The Postgres image; the `migrate` service | The owner's password (secret) |
| `POSTGRES_DB` | The Postgres image; Grafana | The database name. Must equal `MOBYGREP_POSTGRES__DATABASE` |
| `MOBYGREP_APP_PASSWORD` | The Postgres init script, inside the `postgres` container only | The password the script gives the `mobygrep_app` role. Compose fills it from `MOBYGREP_POSTGRES__PASSWORD` |
| `GRAFANA_READER_PASSWORD` | The Postgres init script; Grafana | The password of the `grafana_reader` role (secret) |
| `GRAFANA_ADMIN_USER` | Grafana | Grafana's admin login |
| `GRAFANA_ADMIN_PASSWORD` | Grafana | Grafana's admin password (secret) |
| `MOBYGREP_TEST_POSTGRES_URL` | The test harness | When set, the tests use that running instance and start no container. It is the owner's URL of an instance whose roles already exist |
| `MOBYGREP_TEST_REDIS_URL` | The test harness | When set, the tests use that Redis as the queue instance |
| `MOBYGREP_TEST_OBJECTSTORE_URL` | The test harness | When set, the tests use that S3 endpoint |

## Change log

| Date | Change | Why |
|---|---|---|
| 2026-10-02 | First draft | Written after the spike so the other units can plan against it |
| 2026-10-02 | The public clip endpoint is stated as an exact host match | The spike saw a URL signed for `localhost:8333` refused when it was fetched through `127.0.0.1:8333` |
