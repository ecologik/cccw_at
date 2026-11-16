"""Lisa's pledge impact totals, computed from all Gravity Forms entries (see metrics.json)."""
import json, os

def _num(v):
    try: return float(str(v).replace(',', '').replace('$', ''))
    except (TypeError, ValueError): return 0.0

def metric_value(e, kind, spec):
    f = lambda k: _num(e.get(k))
    if kind == 'sum': return f(spec)
    if kind == 'count': return 1.0 if str(e.get(spec) or '').strip() else 0.0
    if kind == 'diff': return f(spec[0]) - f(spec[1])
    if kind == 'acres':
        acres, w, l, div = spec
        if f(acres): return f(acres)
        return f(w) * f(l) / f(div) if f(div) else 0.0
    if kind == 'formula': return float(eval(spec, {'__builtins__': {}}, {'f': f}))
    raise ValueError(kind)

def all_entries(gf, form_id, fields=None):
    out, page = [], 1
    while True:
        q = {'paging[page_size]': 200, 'paging[current_page]': page}
        if fields: q['_field_ids'] = ','.join(sorted(set(fields) | {'id', 'date_created'}))
        d = gf.get(f'/forms/{form_id}/entries', q)
        out += d['entries']
        if len(d['entries']) < 200: return out
        page += 1

def compute(gf, path=None, cache=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'metrics.json')
    cfg = {k: v for k, v in json.load(open(path)).items() if not k.startswith('_')}
    cache = {} if cache is None else cache
    out = {}
    for pledge, p in cfg.items():
        es = cache.setdefault(p['form'], all_entries(gf, p['form']))
        rows = []
        for name, kind, spec in p['metrics']:
            vals = [metric_value(e, kind, spec) for e in es]
            nz = [v for v in vals if v]
            rows.append({'metric': name, 'kind': kind, 'spec': spec, 'total': sum(vals),
                         'entries': len(nz), 'max': max(nz) if nz else 0})
        out[pledge] = rows
    return out


def find_outliers(gf, site_url, path=None, cache=None):
    """Entries with a field above its limit (outliers.json). One dict per entry + field."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outliers.json')
    cache = {} if cache is None else cache
    out, rules = [], json.load(open(path))['rules']
    for form_id, field, label, limit in rules:
        # full entries: Sucuri blocks requests with a long _field_ids list
        if form_id not in cache: cache[form_id] = all_entries(gf, form_id)
        for e in cache[form_id]:
            v = _num(e.get(field))
            if v > limit:
                name = ' '.join(x for x in (e.get('1.3'), e.get('1.6')) if x)
                out.append({'Pledge': label.split(':')[0], 'Field': label, 'Value': v, 'Limit': limit,
                            'Entry Id': int(e['id']), 'Entry Date': e['date_created'].replace(' ', 'T') + 'Z',
                            'Name': name, 'Email': e.get('2') or '',
                            'GF Link': f"{site_url.rstrip('/')}/wp-admin/admin.php?page=gf_entries&view=entry&id={form_id}&lid={e['id']}"})
    return out
