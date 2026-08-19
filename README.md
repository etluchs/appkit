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

**Real data in the fake backend.** Rather than hand-writing seed rows, open the
list in SharePoint and use **Export → Export to Excel** or **Export to CSV**.
Save the file as `<ListName>.xlsx` / `<ListName>.csv` in a folder of your choice:

```sh
export APPKIT_SHAREPOINT_FAKE_DIR=./sharepoint-exports
# ./sharepoint-exports/Requests.csv  ->  sharepoint.list_rows("Requests")
```

Every export in that folder becomes the list named after its file, so
application code is unchanged. Rows are normalised to look like what Graph
returns: the export's `ID` column becomes the string `id` key (rows are numbered
`"1"`, `"2"`, … if there is no such column) and empty cells are dropped rather
than returned as `None`. Lists without a matching file keep their built-in seed,
and exports are re-read on every `reset_fakes()`, so they stay in place across
tests.

The **CSV export carries the list's structure**, and that is what makes it the
more faithful of the two. It opens with a `ListSchema=` preamble holding the
field definitions, followed by a header row of the list's *internal* field names
— the same keys Graph puts in an item's `fields` facet. The schema is used for
field types, so a multi-choice column comes back as a real list:

```python
sharepoint.list_rows("Services")[0]
# {"id": "10", "Title": "Confluence", "user": ["Mitarbeitende"],
#  "kosten": "kostenlos", ...}
```

Multi-line `Note` fields keep their newlines and HTML entities are unescaped.
Everything other than multi-choice comes back as text: an export carries no
type information beyond the schema, so values are not guessed at. (The Excel
export has no schema at all — its header is display names, and only dates get
special treatment, becoming ISO strings.)

A single file can also be loaded directly, which is handy in a fixture:

```python
from appkit import _fake
_fake.load_sharepoint_export("tests/data/Requests.csv", list_name="Requests")
```

Misconfiguration is loud rather than silent: a missing directory, an unreadable
export, or two files claiming the same list all raise.

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

Only `APPKIT_SHAREPOINT_FAKE_DIR` applies to the fake backend; the rest are
needed in `azure` mode:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `APPKIT_BACKEND` | all | `fake` (default) or `azure`. |
| `APPKIT_SHAREPOINT_SITE` | sharepoint | Graph site id. |
| `APPKIT_SHAREPOINT_FAKE_DIR` | sharepoint | Folder of `<ListName>.xlsx` / `<ListName>.csv` list exports (fake backend only). |
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
