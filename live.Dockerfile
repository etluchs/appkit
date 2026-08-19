# The live suite, packaged to run as an Azure Container Apps Job.
#
# It runs inside the same environment as the app, with the same managed
# identity, so what it proves is what the app would experience:
#
#   az acr build -r <registry> -t appkit-live:latest -f live.Dockerfile .
#
#   az containerapp job create -n appkit-live -g <rg> \
#     --environment <container-apps-env> \
#     --trigger-type Manual --replica-timeout 1800 \
#     --image <registry>.azurecr.io/appkit-live:latest \
#     --mi-system-assigned \
#     --env-vars APPKIT_BACKEND=azure APPKIT_AUTH=easyauth \
#                APPKIT_SHAREPOINT_SITE=... APPKIT_LIVE_LIST=... APPKIT_DB_DSN=...
#
#   az containerapp job start -n appkit-live -g <rg>
#
# The soak test needs a longer replica timeout (--replica-timeout 6000) and its
# own schedule; see the README.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS build
WORKDIR /src

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev --extra dev

FROM python:3.11-slim AS runtime
WORKDIR /src

RUN useradd --create-home --uid 10001 appuser
COPY --from=build /src/.venv /src/.venv
COPY src ./src
COPY tests ./tests
COPY pyproject.toml ./

ENV PATH="/src/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    APPKIT_BACKEND=azure

USER appuser

# `appkit-doctor` first: if the environment is wrong, its report explains why in
# a way a test failure would not. Then the live suite.
CMD ["sh", "-c", "appkit-doctor && pytest -m live -v"]
