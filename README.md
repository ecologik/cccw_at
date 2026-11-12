# CCCW pledge sync

Every night, GitHub Actions copies new Gravity Forms pledge entries from
indiana.clearchoicescleanwater.org into the matching "… Original" tables in the
White River Alliance Airtable base.

## How it works

For each enabled form in `config.json`, `sync.py`:

1. reads the highest **Entry Id** already in the form's Airtable table,
2. pulls Gravity Forms entries with a higher id (active entries only),
3. skips any Entry Id already in Airtable, so re-running is safe,
4. creates the rest. Select options are added automatically (`typecast`).

Column mapping (Airtable column → GF entry key) lives in `config.json`. It was built by
`tools/build_mapping.py`, which matched the columns of the first CSV import against the
same entries from the API. Geocoder columns are rendered the same way the GF CSV export
renders them. Entry Date is written as true UTC.

## All Pledges summary

After each run, the **All Pledges** table gets one row per pledge with its record count and
last pledge date (Indiana local date). Rows are matched by Pledge Name, so you can add your own
columns to that table freely. Settings are under `summary` in `config.json`.

## Secrets (repo Settings → Secrets and variables → Actions)

| Name | What |
|---|---|
| `AIRTABLE_TOKEN` | Airtable personal access token: `data.records:read`, `data.records:write`, `schema.bases:read` on the base |
| `GF_KEY_INDIANA` | Gravity Forms REST API consumer key (Indiana site, Read permission) |
| `GF_SECRET_INDIANA` | Gravity Forms REST API consumer secret |

The GF API is called with OAuth 1.0a signatures because the site's Sucuri firewall strips
Basic auth headers.

## Running by hand

- In GitHub: Actions → Nightly pledge sync → Run workflow (tick "Dry run" to preview).
- Locally: `AIRTABLE_TOKEN=… GF_KEY_INDIANA=… GF_SECRET_INDIANA=… python3 sync.py --dry-run`
  (`--form Tree` limits it to one form).

## Common changes

- **Stop syncing a form:** set `"enabled": false` in `config.json` (Service is already off).
- **Field added to a GF form:** add the column in Airtable, then add `"Column name": "<GF field id>"`
  to that form's `mapping` (e.g. `"12"`, or `"34.1"` for a checkbox choice).
- **Another state site:** add a new entry to `sites` with its URL, its own key/secret env names
  and its forms, and add those secrets plus the env lines in the workflow.
