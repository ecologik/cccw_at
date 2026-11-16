"""Nightly sync: copy new Gravity Forms pledge entries into Airtable.

For each enabled form in config.json: find the highest Entry Id already in its Airtable table, fetch
GF entries newer than that, skip any Entry Id that is somehow already there, and create the rest.

Env: AIRTABLE_TOKEN, plus the GF key/secret env vars named per site in config.json.
Usage: python3 sync.py [--dry-run] [--form NAME_OR_ID ...]"""
import argparse, json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from gfat import GF, Airtable, entry_value, to_airtable
import impact

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='show what would be added, write nothing')
    ap.add_argument('--form', action='append', help='only these forms (name or id); repeatable')
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(here, 'config.json')))
    at = Airtable(os.environ['AIRTABLE_TOKEN'], cfg['airtable_base'])
    tables = {t['id']: t for t in at.schema()}
    total, failures = 0, []

    for site in cfg['sites']:
        gf = GF(site['url'], os.environ[site['gf_key_env']], os.environ[site['gf_secret_env']])
        for f in site['forms']:
            if args.form:
                if f['name'] not in args.form and str(f['form_id']) not in args.form: continue
            elif not f.get('enabled', True):
                continue
            label = f"{site['name']} / {f['name']} (form {f['form_id']})"
            try:
                t = tables[f['airtable_table']]
                types = {x['name']: x['type'] for x in t['fields']}
                writable = {h: s for h, s in f['mapping'].items()
                            if h in types and types[h] not in ('formula', 'rollup', 'lookup', 'autoNumber',
                                                               'createdTime', 'lastModifiedTime', 'count')}
                missing = sorted(set(f['mapping']) - set(types))
                if missing: print(f'  note: {label}: columns not in Airtable, skipped: {missing}')

                since = at.max_number(t['id'], 'Entry Id')
                entries = gf.entries_newer_than(f['form_id'], since)
                if entries:
                    already = at.existing_values(t['id'], 'Entry Id', [e['id'] for e in entries])
                    entries = [e for e in entries if int(e['id']) not in already]
                rows = []
                for e in entries:
                    row = {}
                    for h, spec in writable.items():
                        v = to_airtable(entry_value(e, spec), types[h])
                        if v is not None: row[h] = v
                    rows.append(row)
                if args.dry_run:
                    print(f'{label}: last Entry Id {since}, would add {len(rows)}'
                          + (f" (ids {entries[0]['id']}..{entries[-1]['id']})" if rows else ''))
                else:
                    made = at.create(t['id'], rows) if rows else 0
                    total += made
                    print(f'{label}: last Entry Id {since}, added {made}')
            except Exception as ex:
                failures.append(label)
                print(f'ERROR {label}: {ex}', file=sys.stderr)

    if cfg.get('summary') and not args.form:
        try:
            refresh_summary(at, cfg, args.dry_run)
        except Exception as ex:
            failures.append('All Pledges summary')
            print(f'ERROR All Pledges summary: {ex}', file=sys.stderr)

    if cfg.get('impact') and not args.form:
        try:
            site = cfg['sites'][0]
            refresh_impact(at, GF(site['url'], os.environ[site['gf_key_env']], os.environ[site['gf_secret_env']]),
                           cfg['impact'], args.dry_run)
        except Exception as ex:
            failures.append('Pledge Impact')
            print(f'ERROR Pledge Impact: {ex}', file=sys.stderr)

    if cfg.get('outliers') and not args.form:
        try:
            site = cfg['sites'][0]
            refresh_outliers(at, GF(site['url'], os.environ[site['gf_key_env']], os.environ[site['gf_secret_env']]),
                             site['url'], cfg['outliers'], args.dry_run)
        except Exception as ex:
            failures.append('Outliers')
            print(f'ERROR Outliers: {ex}', file=sys.stderr)

    print(f'Done. Added {total} record(s).' + (f' Failed: {failures}' if failures else ''))
    sys.exit(1 if failures else 0)

def person_key(rec):
    """Who pledged: email if given, else first + last name + address (or city). Same rule as the
    Oct 2026 participation analysis."""
    f = rec['fields']; norm = lambda k: ' '.join(str(f.get(k) or '').lower().split())
    if norm('Email'): return 'e:' + norm('Email')
    who = (norm('Name (First  Name)'), norm('Name (Last Name)'), norm('Address') or norm('City'))
    return 'n:' + '|'.join(who) if any(who) else 'r:' + rec['id']

def refresh_summary(at, cfg, dry_run):
    """One row per pledge in the summary table: record count and latest Entry Date (local date)."""
    sm = cfg['summary']; tz = ZoneInfo(sm.get('timezone', 'UTC'))
    rows = {}
    for site in cfg['sites']:
        for f in site['forms']:
            recs = at.all_records(f['airtable_table'], ['Entry Date', 'Email', 'Name (First  Name)',
                                                        'Name (Last Name)', 'Address', 'City'])
            dates = [r['fields']['Entry Date'] for r in recs if r['fields'].get('Entry Date')]
            last = max(dates) if dates else None
            if last:
                last = datetime.fromisoformat(last.replace('Z', '+00:00')).astimezone(tz).date().isoformat()
            vals = {sm['count_field']: len(recs), sm['last_date_field']: last}
            if sm.get('unique_field'):
                vals[sm['unique_field']] = len({person_key(r) for r in recs})
            rows[f.get('summary_name', f['name'])] = vals
    have = {x['name'] for t in at.schema() if t['id'] == sm['table'] for x in t['fields']}
    if sm.get('unique_field') and sm['unique_field'] not in have:
        print(f"  note: add a number field '{sm['unique_field']}' to the summary table to store unique counts")
        for v in rows.values(): v.pop(sm['unique_field'], None)
    existing = at.all_records(sm['table'], [sm['name_field']])
    by_name = {r['fields'].get(sm['name_field']): r['id'] for r in existing if r['fields'].get(sm['name_field'])}
    blanks = [r['id'] for r in existing if not r['fields'].get(sm['name_field'])]
    updates, creates = [], []
    for name, vals in rows.items():
        fields = {sm['name_field']: name, **vals}
        if name in by_name: updates.append((by_name[name], fields))
        elif blanks: updates.append((blanks.pop(0), fields))
        else: creates.append(fields)
    for name, vals in rows.items():
        print(f"  summary: {name}: {vals}")
    if not dry_run:
        at.update(sm['table'], updates)
        if creates: at.create(sm['table'], creates)

def refresh_impact(at, gf, im, dry_run):
    """One row per pledge + metric (metrics.json) with its total over all GF entries."""
    t = next((t for t in at.schema() if t['name'] == im['table'] or t['id'] == im['table']), None)
    if not t:
        print(f"  note: no '{im['table']}' table in Airtable yet; impact totals skipped"); return
    res = impact.compute(gf)
    want = {}
    for pledge, rows in res.items():
        for r in rows:
            want[(pledge, r['metric'])] = {im['pledge_field']: pledge, im['metric_field']: r['metric'],
                                           im['total_field']: round(r['total'], 2), im['entries_field']: r['entries']}
    existing = at.all_records(t['id'], [im['pledge_field'], im['metric_field']])
    have = {(r['fields'].get(im['pledge_field']), r['fields'].get(im['metric_field'])): r['id'] for r in existing}
    blanks = [r['id'] for r in existing if not r['fields'].get(im['pledge_field'])]
    updates, creates = [], []
    for k, fields in want.items():
        if k in have: updates.append((have[k], fields))
        elif blanks: updates.append((blanks.pop(0), fields))
        else: creates.append(fields)
    print(f'  impact: {len(want)} metrics ({len(updates)} update, {len(creates)} new)')
    if not dry_run:
        at.update(t['id'], updates)
        if creates: at.create(t['id'], creates)

def refresh_outliers(at, gf, site_url, oc, dry_run):
    """Keep the Outliers table equal to the current list of flagged entries (outliers.json).
    Rows whose entry was fixed or deleted in Gravity Forms are removed."""
    t = next((t for t in at.schema() if t['name'] == oc['table'] or t['id'] == oc['table']), None)
    if not t:
        print(f"  note: no '{oc['table']}' table in Airtable yet; outliers skipped"); return
    cols = {f['name'] for f in t['fields']}
    found = impact.find_outliers(gf, site_url)
    want = {(o['Entry Id'], o['Field']): {k: v for k, v in o.items() if k in cols} for o in found}
    existing = at.all_records(t['id'], ['Entry Id', 'Field'])
    have = {(r['fields'].get('Entry Id'), r['fields'].get('Field')): r['id'] for r in existing}
    stale = [rid for k, rid in have.items() if k not in want]
    updates = [(have[k], f) for k, f in want.items() if k in have]
    creates = [f for k, f in want.items() if k not in have]
    print(f'  outliers: {len(want)} flagged ({len(creates)} new, {len(stale)} cleared)')
    if not dry_run:
        at.update(t['id'], updates)
        if creates: at.create(t['id'], creates)
        if stale: at.delete(t['id'], stale)

if __name__ == '__main__':
    main()
