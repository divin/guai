set dotenv-load := false

# uv tool binaries (e.g. `pebble`) are installed into ~/.local/bin, which non-login
# shells may not have on PATH. Prepend it so every recipe can call `pebble`.
export PATH := home_directory() + "/.local/bin:" + env_var("PATH")

# Default: verify backend + frontend, then run their tests.
default: check test frontend-test

# Format Python backend code.
fmt:
    cd server && uv run ruff format .

# Lint and auto-fix safe issues.
lint:
    cd server && uv run ruff check --fix .

# Sort Python imports with Ruff.
isort:
    cd server && uv run ruff check --select I --fix .

# Non-mutating checks for the backend and Zola site.
check: frontend-check
    cd server && uv run ruff format --check .
    cd server && uv run ruff check .

# Run backend tests, building the Zola site required by the app test.
test: frontend-build
    cd server && uv run pytest tests -q

# Validate Zola templates and links.
frontend-check:
    zola --root frontend check --skip-external-links

# Build the static site (output: frontend/public).
frontend-build:
    zola --root frontend build

# Run dependency-free frontend tests.
frontend-test: frontend-build
    cd frontend && node --test

# Install/update the locked Python environment.
sync:
    cd server && uv sync

# Run a local instance bound to loopback, storing test data in .local-data/.
run: frontend-build
    cd server && GUAI_DB_PATH=.local-data/guai.sqlite3 \
        uv run uvicorn --factory guai_hub.main:create_app --host 0.0.0.0 --port 8000 --workers 1

# Install the Pebble/Rebble toolchain (CLI via uv + latest SDK) once.
# Requires ~/.local/bin on PATH (uv tool does this on install).
pebble-setup:
    uv tool install pebble-tool
    # `pebble sdk install` exits 1 when the SDK is already installed, so tolerate that
    # line. A missing SDK surfaces clearly at the next `pebble build`.
    - pebble sdk install latest

# Compile the Pebble watchface (emery / Pebble Time 2 only).
watch-build: pebble-setup
    cd watchface && pebble build

# Compile the Pebble check-in app (emery / Pebble Time 2 only).
app-build: pebble-setup
    cd app && pebble build

# Run the watchface in the Pebble Time 2 (emery) emulator (build + launch + install).
# After this, open the config page (watch-config) to set server URL + token,
# then push synthetic health data (watch-simulate) and watch it reach the server.
watch-emulate: watch-build
    cd watchface && pebble install --emulator emery build/watchface.pbw

# Configure the watchface for the emulator WITHOUT the flaky config page: seed
# GUAI_SERVER/GUAI_TOKEN into pypkjs localStorage from server/.env.
# Requires the emulator to be stopped first (watch-kill), then watch-emulate.
watch-config:
    python3 scripts/watch-seed.py

# Fallback: interactive config page in the emulator window (enter URL + token, Save
# before the phonesim ack times out).
watch-config-html:
    pebble emu-app-config --emulator emery --file watchface/docs/config.html

# Emulator-free sync smoke test: POST a representative watchface payload (activity,
# heart rate, sleep) to the running server using the token from server/.env.
watch-smoke:
    bash scripts/watch-smoke.sh

# Push synthetic health data into the emery emulator (steps/HR/sleep).
watch-simulate:
    pebble emu-steps --emulator emery 8500
    pebble emu-heart-rate --emulator emery 68
    pebble emu-sleep --emulator emery 420 --restful 300

# Stream watchface logs from the emery emulator.
watch-logs:
    pebble logs --emulator emery

# Run the check-in app in the emery emulator (build + install; the app auto-launches).
# Config is seeded with the same `just watch-config` (both GUAI apps are seeded).
app-emulate: app-build
    cd app && pebble install --emulator emery build/app.pbw --logs

# Close the emery emulator.
watch-kill:
    pebble kill
