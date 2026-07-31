# appkit

The UZH internal-app toolkit. Four small modules, one job each — so that a
business app (and the AI assistant writing it) never has to touch Microsoft
Graph, `httpx`, or `psycopg` by hand.

Every module authenticates with the app's **managed identity** in production
and ships with an **in-memory fake** for local development and tests. No
secrets, no connection strings in code, no network in the test suite.

```python
from appkit import sharepoint, mail, auth, db

rows = sharepoint.list_rows("Requests")            # Graph list items -> dicts
mail.send_mail(to="team@uzh.ch", subject="Summary", body="...")   # Graph sendMail
who = auth.user(request)                            # Easy Auth headers -> User
db.execute("insert into note (body) values (%s)", ["hi"])         # psycopg pool
```

## The two backends

appkit runs in one of two backends, chosen by the `APPKIT_BACKEND` environment
variable:

| `APPKIT_BACKEND` | Behaviour |
| --- | --- |
| `fake` *(default locally)* | Pure Python, in-memory. No Azure credentials, no network. Used by local dev and the whole test suite. |
| `azure` | Talks to Microsoft Graph and Azure Postgres using `DefaultAzureCredential` (the managed identity). |

The backend is chosen **strictly**. An unrecognised value is an error, and an
*unset* value is an error whenever the app is running on Azure Container Apps or
App Service (detected via `CONTAINER_APP_NAME` / `WEBSITE_SITE_NAME`). Off
platform, unset still means `fake`.

That strictness exists because every fake failure looks like a success:
`send_mail()` returns normally without sending anything, `db.execute()` writes to
an in-memory database that disappears on the next restart, `list_rows()` renders
seed data as if it were real, and `auth.user()` hands back an *authenticated*
dev user carrying whatever roles `APPKIT_DEV_ROLES` names. A production app with
a dropped or misspelled variable must fail loudly, not quietly serve fiction.

## Modules

### `appkit.sharepoint`

```python
sharepoint.list_rows("Requests")                       # all items as field dicts
sharepoint.list_rows("Requests", select=["Title"])     # project fields
sharepoint.list_rows("Requests", site="contoso.sharepoint.com,<siteId>,<webId>")
```

Reads list items from Graph (`/sites/{site}/lists/{list}/items?expand=fields`),
following pagination. The site defaults to `APPKIT_SHAREPOINT_SITE`.

> **Naming a list.** The fake backend looks lists up by display name; Graph
> resolves `/lists/{key}` by the list's **id** or **URL name**. A display name
> with a space in it therefore works locally and returns 404 in Azure. Use the
> list id, or a URL name without spaces.

### `appkit.mail`

```python
mail.send_mail(to="team@uzh.ch", subject="Hi", body="Text")
mail.send_mail(to=["a@uzh.ch", "b@uzh.ch"], cc="boss@uzh.ch",
               subject="Report", body="<p>HTML</p>", html=True)
mail.outbox()   # fake backend: inspect what was "sent"
```

Sends via `POST /users/{sender}/sendMail`. The sender defaults to
`APPKIT_MAIL_SENDER`.

### `appkit.db`

```python
db.execute("create table note (id serial primary key, body text)")
db.execute("insert into note (body) values (%s)", ["hello"])
rows = db.query("select * from note")     # -> list[dict]
with db.transaction() as tx:
    tx.execute("update note set body = %s where id = %s", ["hi", 1])
```

A `psycopg` connection pool that returns **dict rows**. Use `%s` placeholders in
both backends. In `azure` mode the password is an AAD access token from the
managed identity, fetched **per connection** (those tokens expire after about an
hour, so a pool that captured one would stop being able to open connections); in
`fake` mode it is a process-local in-memory SQLite database with the same
surface.

> **The fake is SQLite, not Postgres.** `serial`, `returning`, `now()`,
> `boolean`, `timestamptz` and a literal `%` in SQL all behave differently.
> `tests/test_db_conformance.py` runs one suite against both and marks each known
> divergence — read it before relying on anything beyond plain
> `select`/`insert`/`update`.

### `appkit.auth`

```python
u = auth.user(request)      # request: anything with a `.headers` mapping
if u and u.has_role("approver"):
    ...
```

Reads the Azure Container Apps Easy Auth headers
(`X-MS-CLIENT-PRINCIPAL[-NAME|-ID|-IDP]`) and returns a `User`
(`id`, `name`, `email`, `provider`, `roles`). Locally it returns a configurable
**dev user** so pages render without a login.

#### Those headers are only as trustworthy as what is in front of you

Easy Auth strips client-supplied `X-MS-CLIENT-PRINCIPAL*` headers and injects
its own, so **behind it** they are trustworthy. Any request path that skips it —
internal ingress, another container in the same environment, a directly exposed
port, or Easy Auth left in "allow unauthenticated" mode — lets the caller set
those headers by hand, and with them `has_role()`.

appkit cannot detect that from inside the container, so `APPKIT_AUTH` says how
the user is established:

| `APPKIT_AUTH` | Behaviour |
| --- | --- |
| `easyauth` | Trust the platform-injected headers. **Only safe if Easy Auth is enabled and set to reject unauthenticated requests.** |
| `verify` | Ignore those headers; cryptographically verify the `X-MS-TOKEN-AAD-ID-TOKEN` JWT against the tenant's signing keys. Forged headers cannot survive this. |
| `dev` | The local dev user, plus header simulation. **Refused** on an Azure app platform. |

Unset, it follows the backend (`fake` → `dev`, `azure` → `easyauth`) — but on
Container Apps or App Service it must be set explicitly, so that trusting a
proxy is always a decision somebody made on purpose.

`verify` is the only one of the three that does not depend on trusting the
network in front of the app. It needs the `appkit[verify]` extra, Easy Auth's
**token store enabled** (so the id token is forwarded), and:

| Variable | Meaning |
| --- | --- |
| `APPKIT_AUTH_TENANT_ID` | Entra tenant the token must come from. |
| `APPKIT_AUTH_CLIENT_ID` | The Easy Auth app registration's client id (the token's `aud`). |
| `APPKIT_AUTH_AUTHORITY` | Login endpoint; defaults to the public cloud. |

## Configuration

Only needed in `azure` mode:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `APPKIT_BACKEND` | all | `fake` (default) or `azure`. |
| `APPKIT_SHAREPOINT_SITE` | sharepoint | Graph site id. |
| `APPKIT_MAIL_SENDER` | mail | Mailbox to send as. |
| `APPKIT_DB_DSN` | db | Postgres connection string (no password — the token is injected). |
| `APPKIT_DB_POOL_MAX` | db | Max pool size (default 10). |
| `APPKIT_AUTH` | auth | `easyauth`, `verify` or `dev`. Required on an Azure app platform. |
| `APPKIT_AUTH_TENANT_ID` / `APPKIT_AUTH_CLIENT_ID` / `APPKIT_AUTH_AUTHORITY` | auth | Only for `APPKIT_AUTH=verify`. |
| `APPKIT_DEV_USER` / `APPKIT_DEV_EMAIL` / `APPKIT_DEV_ROLES` | auth | The local dev user (`APPKIT_AUTH=dev` only). |

The managed identity needs, at minimum: Graph `Sites.Read.All` (SharePoint),
`Mail.Send` (mail), and an AAD role on the Postgres server (db).

## Errors

Anything appkit raises on purpose derives from `AppkitError`:

```python
from appkit import GraphError, sharepoint

try:
    rows = sharepoint.list_rows("Requests")
except GraphError as exc:
    exc.status, exc.code, exc.message, exc.request_id
```

`GraphError` keeps the Graph error body and the `request-id` header — the two
things you need to explain a 403 or a 404, and the value Microsoft support asks
for. Graph requests are retried with backoff on throttling (`429`, honouring
`Retry-After`) and transient server errors; `sendMail` is only retried when Graph
tells us it did *not* process the request, since sending is not idempotent.
Configuration problems raise `ConfigError`.

## Development

```sh
uv sync --extra dev      # or: uv pip install -e '.[dev]'
uv run pytest            # fake backend + Graph contract tests, no network
uv run ruff check .
```

Tests reset all fake state between cases via the `reset_fakes()` helper (wired
up as an autouse fixture in `tests/conftest.py`).

### Testing the real backends

The suite is layered, because the fake proving something says nothing about
Azure:

| Layer | What it proves | Needs |
| --- | --- | --- |
| `test_*.py` (fake) | The in-memory behaviour apps develop against. | nothing |
| `test_graph_contract.py`, `test_*_azure.py` | The real Graph code path: URLs, `$expand`/`$top`, pagination, retry policy, error mapping. Runs against a mocked transport with only the managed-identity token stubbed. | nothing |
| `test_db_conformance.py` | One database suite run against **both** the fake and a real Postgres, through the real `psycopg` pool. Divergences are marked `xfail`, not hidden. | a Postgres |
| `test_auth_verify.py` | `APPKIT_AUTH=verify` end to end: a real RSA key signs real JWTs, a real local HTTP server serves the JWKS, and forged, tampered, expired, wrong-audience and wrong-tenant tokens are all rejected. | nothing |

```sh
docker run -d --name appkit-pg -p 5432:5432 \
    -e POSTGRES_USER=appkit -e POSTGRES_PASSWORD=appkit -e POSTGRES_DB=appkit \
    postgres:16
APPKIT_TEST_PG_DSN=postgresql://appkit@localhost:5432/appkit uv run pytest
```

Without `APPKIT_TEST_PG_DSN` the Postgres half skips, so the default `uv run
pytest` still needs nothing but Python. CI runs both.

Not yet covered: a live Microsoft 365 tenant (real Graph, real SharePoint list,
real mailbox) and the Container Apps Easy Auth headers. The Easy Auth parser
should be pinned with a golden `X-MS-CLIENT-PRINCIPAL` blob captured once from a
real deployment — see `tests/test_auth.py`, which currently builds its own.
