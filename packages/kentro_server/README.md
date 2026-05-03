# kentro-server

FastAPI engine for Kentro. Holds all engine state and business logic. Same binary runs locally for dev, as a subprocess in Colab, on a host like Fly.io / Railway, or self-hosted in a customer VPC.

CLI: `kentro-server start | seed-demo | reset-tenant <id> | smoke-test`

See `implementation-handoff.md` at the repo root for the full architecture.
