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
| `fake` *(default)* | Pure Python, in-memory. No Azure credentials, no network. Used by local dev and the whole test suite. |
| `azure` | Talks to Microsoft Graph and Azure Postgres using `DefaultAzureCredential` (the managed identity). |

Anything other than `azure` is treated as `fake`, so a misconfigured
environment fails *offline* rather than blindly reaching for Azure.

## Modules

### `appkit.sharepoint`

```python
sharepoint.list_rows("Requests")                       # all items as field dicts
sharepoint.list_rows("Requests", select=["Title"])     # project fields
sharepoint.list_rows("Requests", site="contoso.sharepoint.com,<siteId>,<webId>")
```

Reads list items from Graph (`/sites/{site}/lists/{list}/items?expand=fields`),
following pagination. The site defaults to `APPKIT_SHAREPOINT_SITE`.

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
managed identity; in `fake` mode it is a process-local in-memory SQLite database
with the same surface.

### `appkit.auth`

```python
u = auth.user(request)      # request: anything with a `.headers` mapping
if u and u.has_role("approver"):
    ...
```

Reads the Azure Container Apps Easy Auth headers
(`X-MS-CLIENT-PRINCIPAL[-NAME|-ID|-IDP]`) and returns a `User`
(`id`, `name`, `email`, `provider`, `roles`). With no headers present and the
`fake` backend active, it returns a configurable **dev user** so local pages
render without a login.

## Configuration

Only needed in `azure` mode:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `APPKIT_BACKEND` | all | `fake` (default) or `azure`. |
| `APPKIT_SHAREPOINT_SITE` | sharepoint | Graph site id. |
| `APPKIT_MAIL_SENDER` | mail | Mailbox to send as. |
| `APPKIT_DB_DSN` | db | Postgres connection string (no password — the token is injected). |
| `APPKIT_DB_POOL_MAX` | db | Max pool size (default 10). |
| `APPKIT_DEV_USER` / `APPKIT_DEV_EMAIL` / `APPKIT_DEV_ROLES` | auth | The local dev user (fake backend only). |

The managed identity needs, at minimum: Graph `Sites.Read.All` (SharePoint),
`Mail.Send` (mail), and an AAD role on the Postgres server (db).

## Development

```sh
uv sync --extra dev      # or: uv pip install -e '.[dev]'
uv run pytest            # runs entirely on the fake backend, no network
uv run ruff check .
```

Tests reset all fake state between cases via the `reset_fakes()` helper (wired
up as an autouse fixture in `tests/conftest.py`).
