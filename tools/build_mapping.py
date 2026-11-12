"""One-off: build config.json's column mapping by matching the Airtable columns (which came from the
cleaned CSV exports) to Gravity Forms entry keys, using entries present in both.

Usage: python3 tools/build_mapping.py CSV_DIR   (reads config.json for forms, writes mappings back)
Env: GF_KEY, GF_SECRET, AIRTABLE_TOKEN"""
import csv, json, os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gfat import GF, Airtable, entry_value

def norm(s): return re.sub(r'\r\n?', '\n', s or '').strip()

def main(csv_dir):
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')
    cfg = json.load(open(cfg_path))
    for site in cfg['sites']:
        gf = GF(site['url'], os.environ[site['gf_key_env']], os.environ[site['gf_secret_env']])
        for f in site['forms']:
            form = gf.form(f['form_id'])
            rows = list(csv.DictReader(open(os.path.join(csv_dir, f['csv']), encoding='utf-8')))
            ents = {e['id']: e for e in gf.get(f"/forms/{f['form_id']}/entries", {'paging[page_size]': 500})['entries']}
            pairs = [(r, ents[r['Entry Id']]) for r in rows if r['Entry Id'] in ents]
            keys = sorted({k for _, e in pairs for k in e})
            geo = [x for x in form['fields'] if x['type'] == 'gfgeo_geocoder']
            geo_ids = [str(x['id']) for x in geo]
            geo_by_label = {(x.get('label') or '').strip(): str(x['id']) for x in geo}
            # labels the export would use, for columns that are blank in every sampled row
            label_keys = collections.defaultdict(list)
            for x in form['fields']:
                lab = (x.get('adminLabel') or x.get('label') or '').strip()
                for i in (x.get('inputs') or []):
                    if not i.get('isHidden'): label_keys[(i.get('label') or '').strip()].append(str(i['id']))
                    label_keys[f"{lab} ({i.get('label')})"].append(str(i['id']))
                if not x.get('inputs'): label_keys[lab].append(str(x['id']))
            used, mapping, unresolved = set(), {}, []
            for h in rows[0].keys():
                base = re.sub(r' \d+$', '', h)
                if h in geo_by_label and h != 'Geocoder':
                    mapping[h] = {'geocoder': geo_by_label[h]}; continue
                if base == 'Geocoder':
                    n = int(h.split()[-1]) - 1 if h != base else 0
                    if n < len(geo_ids): mapping[h] = {'geocoder': geo_ids[n]}; continue
                if h == 'Entry Id': mapping[h] = 'id'; continue
                if h == 'Entry Date': mapping[h] = 'date_created'; continue
                scored = []
                for k in keys:
                    if k in used: continue
                    both = [(norm(r[h]), norm(str(e.get(k) or ''))) for r, e in pairs]
                    hits = sum(1 for a, b in both if a and a == b)
                    miss = sum(1 for a, b in both if a and a != b)
                    extra = sum(1 for a, b in both if b and not a)
                    if hits: scored.append((hits - 2 * miss - extra, k))
                scored.sort(reverse=True)
                cands = [k for s, k in scored if s == scored[0][0]] if scored else []
                if len(cands) > 1:  # tie: prefer the one whose label matches the header
                    lk = [k for k in cands if k in label_keys.get(base, [])]
                    cands = lk[:1] or cands[:1]
                if not cands:
                    cands = [k for k in label_keys.get(base, []) if k not in used][:1]
                if cands:
                    k = cands[0]
                    if k[0].isdigit(): used.add(k)
                    mapping[h] = k
                else:
                    unresolved.append(h)
            f['mapping'] = mapping
            print(f"{f['name']}: {len(mapping)} mapped, unresolved {unresolved}")
    json.dump(cfg, open(cfg_path, 'w'), indent=2)

if __name__ == '__main__':
    main(sys.argv[1])
