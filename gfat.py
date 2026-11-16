"""Shared helpers: Gravity Forms (OAuth1-signed) + Airtable clients and value formatting."""
import base64, hashlib, hmac, json, random, re, time, urllib.error, urllib.parse, urllib.request

def _q(s): return urllib.parse.quote(str(s), safe='~')

class GF:
    """Gravity Forms REST v2. Uses OAuth 1.0a signing because the site's Sucuri firewall strips Basic auth."""
    def __init__(self, site, key, secret):
        self.base, self.key, self.secret = site.rstrip('/') + '/wp-json/gf/v2', key, secret

    def get(self, path, params=None):
        url = self.base + path
        params = dict(params or {})
        oauth = {'oauth_consumer_key': self.key, 'oauth_nonce': str(random.getrandbits(64)),
                 'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
                 'oauth_version': '1.0'}
        allp = {**params, **oauth}
        base = 'GET&' + _q(url) + '&' + _q('&'.join(f'{_q(k)}={_q(v)}' for k, v in sorted(allp.items())))
        oauth['oauth_signature'] = base64.b64encode(
            hmac.new((self.secret + '&').encode(), base.encode(), hashlib.sha1).digest()).decode()
        # send params in the same sorted order we signed them: the server rebuilds nested keys
        # like paging[...] in request order, so any other order breaks the signature
        full = url + '?' + urllib.parse.urlencode(sorted({**params, **oauth}.items()), quote_via=urllib.parse.quote)
        return _request(urllib.request.Request(full, headers={'User-Agent': 'cccw-pledge-sync'}))

    def form(self, form_id):
        return self.get(f'/forms/{form_id}')

    def entries_newer_than(self, form_id, min_id, page_size=100):
        """Active entries with id > min_id, oldest first."""
        out, page = [], 1
        while True:
            d = self.get(f'/forms/{form_id}/entries', {
                'paging[page_size]': page_size, 'paging[current_page]': page,
                'sorting[key]': 'id', 'sorting[direction]': 'DESC', 'sorting[is_numeric]': 'true'})
            batch = d.get('entries', [])
            newer = [e for e in batch if int(e['id']) > min_id]
            out.extend(newer)
            if len(newer) < len(batch) or len(batch) < page_size:
                break
            page += 1
        return sorted(out, key=lambda e: int(e['id']))

class Airtable:
    def __init__(self, token, base_id):
        self.token, self.base = token, base_id
        self.root = f'https://api.airtable.com/v0/{base_id}'

    def _req(self, method, url, body=None):
        time.sleep(0.22)  # stay under 5 req/s per base
        data = json.dumps(body).encode() if body is not None else None
        return _request(urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}))

    def schema(self):
        return self._req('GET', f'https://api.airtable.com/v0/meta/bases/{self.base}/tables')['tables']

    def max_number(self, table, field):
        q = urllib.parse.urlencode({'maxRecords': 1, 'fields[]': field,
                                    'sort[0][field]': field, 'sort[0][direction]': 'desc'})
        recs = self._req('GET', f'{self.root}/{table}?{q}')['records']
        return int(recs[0]['fields'].get(field, 0)) if recs else 0

    def existing_values(self, table, field, values):
        found = set()
        for i in range(0, len(values), 50):
            chunk = values[i:i + 50]
            formula = 'OR(' + ','.join(f'{{{field}}}={int(v)}' for v in chunk) + ')'
            off = None
            while True:
                q = {'filterByFormula': formula, 'fields[]': field, 'pageSize': 100}
                if off: q['offset'] = off
                d = self._req('GET', f'{self.root}/{table}?' + urllib.parse.urlencode(q))
                found |= {int(r['fields'][field]) for r in d['records'] if field in r['fields']}
                off = d.get('offset')
                if not off: break
        return found

    def all_records(self, table, fields):
        recs, off = [], None
        while True:
            q = [('pageSize', 100)] + [('fields[]', f) for f in fields] + ([('offset', off)] if off else [])
            d = self._req('GET', f'{self.root}/{table}?' + urllib.parse.urlencode(q))
            recs += d['records']; off = d.get('offset')
            if not off: return recs

    def update(self, table, updates):
        for i in range(0, len(updates), 10):
            self._req('PATCH', f'{self.root}/{table}',
                      {'records': [{'id': rid, 'fields': f} for rid, f in updates[i:i + 10]], 'typecast': True})

    def delete(self, table, ids):
        for i in range(0, len(ids), 10):
            q = urllib.parse.urlencode([('records[]', r) for r in ids[i:i + 10]])
            self._req('DELETE', f'{self.root}/{table}?{q}')

    def create(self, table, rows):
        made = 0
        for i in range(0, len(rows), 10):
            d = self._req('POST', f'{self.root}/{table}',
                          {'records': [{'fields': r} for r in rows[i:i + 10]], 'typecast': True})
            made += len(d['records'])
        return made

def _request(req, tries=5):
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            body = e.read()[:500].decode('utf-8', 'replace')
            if e.code in (429, 500, 502, 503, 504) and n < tries - 1:
                time.sleep(2 ** n * 2); continue
            raise RuntimeError(f'HTTP {e.code} for {req.get_method()} {req.full_url.split("?")[0]}: {body}') from None
        except urllib.error.URLError:
            if n < tries - 1: time.sleep(2 ** n * 2); continue
            raise

# ---- PHP serialize (geocoder fields) ----
def php_unserialize(s):
    def parse(i):
        t = s[i]
        if t == 'N': return None, i + 2
        if t in 'ibd':
            j = s.index(';', i); v = s[i + 2:j]
            return (int(v) if t == 'i' else bool(int(v)) if t == 'b' else float(v)), j + 1
        if t == 's':
            j = s.index(':', i + 2); n = int(s[i + 2:j])
            b = s.encode('utf-8'); start = len(s[:j + 2].encode('utf-8'))
            v = b[start:start + n].decode('utf-8')
            return v, j + 2 + len(v) + 2
        if t == 'a':
            j = s.index(':', i + 2); n = int(s[i + 2:j]); i = j + 2; d = {}
            for _ in range(n):
                k, i = parse(i); v, i = parse(i); d[k] = v
            return d, i + 1
        raise ValueError('bad php serialize at %d' % i)
    return parse(0)[0]

GEO_LABELS = [('street_number', 'Street Number'), ('street_name', 'Street Name'),
              ('street', 'Street ( number + name )'), ('street_bw', 'Street ( name + number )'),
              ('premise', 'Premise'), ('subpremise', 'Subpremise'), ('neighborhood', 'Neighborhood'),
              ('city', 'City'), ('county', 'County'), ('region_code', 'Region Code ( state code )'),
              ('region_name', 'Region Name ( state name )'), ('postcode', 'Postcode / Zipcode'),
              ('country_code', 'Country Code'), ('country_name', 'Country Name'), ('address', 'Address'),
              ('formatted_address', 'Formatted Address'), ('latitude', 'Latitude'),
              ('longitude', 'Longitude'), ('place_name', 'Place Name'), ('place_id', 'Place ID')]

def format_geocoder(raw):
    """Render a geocoder value the way the Gravity Forms CSV export does."""
    if not raw: return ''
    try: d = php_unserialize(raw)
    except Exception: d = None
    if not isinstance(d, dict): return raw  # the export shows unparseable values as-is
    return ' | '.join(f'{label}: {str(d.get(k) or "") or "n/a"}' for k, label in GEO_LABELS if k in d)

def entry_value(entry, spec):
    """spec: a GF entry key (e.g. '1.3', 'id', 'date_created') or {'geocoder': '9'}."""
    if isinstance(spec, dict):
        return format_geocoder(entry.get(spec['geocoder']))
    v = entry.get(spec)
    return '' if v is None else str(v)

def to_airtable(value, field_type):
    v = re.sub(r'\r\n?', '\n', value).strip()
    if v == '': return None
    if field_type in ('number', 'currency', 'percent'):
        try: return float(v.replace(',', '').replace('$', ''))
        except ValueError: return None
    if field_type == 'multipleSelects':
        return [x.strip() for x in v.split(',') if x.strip()]
    if field_type in ('dateTime', 'date'):
        return None if v.startswith(('0000', '-0001')) else v.replace(' ', 'T') + 'Z'
    return v
