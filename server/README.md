guai/server/
├── app/
│   ├── __init__.py
│   ├── main.py              # app factory, router registration, lifespan
│   ├── config.py            # pydantic-settings: everything from .env
│   ├── db.py                # engine/session factory, init, PRAGMAs
│   ├── models.py            # SQLAlchemy models (weather, sleep, activity, headache, ...)
│   ├── schemas.py           # pydantic request/response models
│   ├── auth.py              # bearer-token dependency
│   ├── pack.py              # binary decoders (health batch, log entry)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── sync.py          # GET /api/sync          (watchface bundle)
│   │   ├── health.py        # POST /api/health       (binary batch ingest)
│   │   ├── log.py           # POST /api/log          (headache/mood/med entries)
│   │   ├── pins.py          # GET /api/pins          (prompt pins for JS)
│   │   ├── weather.py       # GET /api/weather/*     (latest/history/status)
│   │   └── status.py        # GET /api/status        (debug/uptime)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── weather.py       # Open-Meteo fetch, WMO mapping, store
│   │   ├── calendar.py      # CalDAV/ICS fetch, recurrence, current+next
│   │   ├── prompts.py       # schedule → prompt_state rows → pin JSON
│   │   └── retention.py     # nightly rollup + prune
│   └── tasks.py             # background job wiring (scheduler config)
├── static/index.html        # dashboard
├── data/                    # SQLite file (docker volume)
├── .env
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
