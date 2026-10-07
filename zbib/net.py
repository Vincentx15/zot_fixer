"""Cached, rate-limited lookups in public scholarly APIs. Standard library only.

Sources: Crossref (journals, IEEE/ACM proceedings, DOIs), DataCite (arXiv metadata; the arXiv API itself
rate-limits hard), arXiv abstract pages (Journal ref / Comments / Related DOI), bioRxiv API, OpenReview
search (ICLR/NeurIPS/ICML decisions and a DBLP mirror), Semantic Scholar (venues), and optionally a local
Zotero translation-server for publisher pages.
"""
import html, json, os, re, threading, time, unicodedata, urllib.error, urllib.parse, urllib.request
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(ROOT, 'state', 'cache.json')
UA = 'zot_fixer/1.0' + (f" (mailto:{os.environ['CROSSREF_MAILTO']})" if os.environ.get('CROSSREF_MAILTO') else '')
TRANSLATION_SERVER = os.environ.get('TRANSLATION_SERVER', 'http://127.0.0.1:1969')

# ----------------------------------------------------------------------------- text
def norm(s):
    """Lower-case alphanumerics; identical to norm() in zotero/apply.js."""
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(ch for ch in s if not ('̀' <= ch <= 'ͯ'))
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()

def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def family(creator):
    return norm((creator or {}).get('lastName') or (creator or {}).get('name') or '').split(' ')[-1]

# ----------------------------------------------------------------------------- cache + http
_lock, _locks = threading.Lock(), {}
CACHE = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}

def save_cache():
    with _lock:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        json.dump(dict(CACHE), open(CACHE_PATH + '.tmp', 'w'))
        os.replace(CACHE_PATH + '.tmp', CACHE_PATH)

def cached(ns, key, fn):
    k = f'{ns}::{key}'
    if CACHE.get(k, 'ERROR') == 'ERROR':
        CACHE[k] = fn()
    return CACHE[k]

def throttle(host, seconds):
    """At most one request per `seconds` to `host`, across threads."""
    lock = _locks.setdefault(host, threading.Lock())
    with lock:
        last = _locks.get(host + ':t', 0)
        wait = last + seconds - time.time()
        if wait > 0: time.sleep(wait)
        _locks[host + ':t'] = time.time()

def http(url, data=None, headers=None, tries=5, raw=False):
    host = urllib.parse.urlparse(url).netloc
    for t in range(tries):
        throttle(host, {'export.arxiv.org': 3, 'arxiv.org': 3, 'api2.openreview.net': 0.7}.get(host, 0.25))
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return body.decode('utf-8', 'replace') if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(min(60, 3 * 2 ** t)); continue
            return None  # 400/403/404…: not found
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(5)
    return 'ERROR'

# ----------------------------------------------------------------------------- Crossref
CR_FIELDS = 'DOI,title,type,container-title,issued,published-print,volume,issue,page,author,relation,event'

def crossref_search(title, author=''):
    q = urllib.parse.urlencode({'query.bibliographic': title, 'query.author': author, 'rows': 6, 'select': CR_FIELDS})
    r = cached('crossref', norm(title) + '|' + author, lambda: http('https://api.crossref.org/works?' + q))
    return r['message']['items'] if isinstance(r, dict) else []

def crossref_doi(doi):
    r = cached('crossref_doi', doi.lower(), lambda: http('https://api.crossref.org/works/' + urllib.parse.quote(doi)))
    return r['message'] if isinstance(r, dict) else None

def crossref_by_pii(pii):
    r = cached('pii', pii, lambda: http('https://api.crossref.org/works?' + urllib.parse.urlencode({'filter': f'alternative-id:{pii}', 'rows': 1})))
    items = r['message']['items'] if isinstance(r, dict) else []
    return items[0]['DOI'] if items else None

# ----------------------------------------------------------------------------- arXiv (DataCite + abstract page)
def datacite_arxiv(arxiv_id=None, title=None):
    """arXiv records registered at DataCite (10.48550/arXiv.<id>): by id, or best title match."""
    if arxiv_id:
        r = cached('datacite', arxiv_id, lambda: http(f'https://api.datacite.org/dois/10.48550/arXiv.{arxiv_id}'))
        recs = [r['data']] if isinstance(r, dict) and r.get('data') else []
    else:
        q = urllib.parse.urlencode({'query': 'titles.title:(' + ' '.join(norm(title).split()[:14]) + ')',
                                    'client-id': 'arxiv.content', 'page[size]': 8})
        r = cached('datacite_title', norm(title), lambda: http('https://api.datacite.org/dois?' + q))
        recs = [x for x in (r.get('data', []) if isinstance(r, dict) else []) if sim(x['attributes']['titles'][0]['title'], title) > 0.93]
    if not recs: return None
    a = recs[0]['attributes']
    sub = sorted(d['date'] for d in a.get('dates', []) if d.get('dateType') == 'Submitted')
    creators = []
    for c in a.get('creators', []):
        if c.get('familyName'): creators.append({'creatorType': 'author', 'lastName': c['familyName'], 'firstName': c.get('givenName', '')})
        elif c.get('name'): creators.append({'creatorType': 'author', 'name': c['name']})
    aid = re.sub(r'^10\.48550/arxiv\.', '', recs[0]['id'], flags=re.I)
    abstract = next((d.get('description') for d in a.get('descriptions', []) if d.get('descriptionType') == 'Abstract'), '') or ''
    return {'id': aid, 'first': (sub[0] if sub else str(a.get('publicationYear')))[:10],
            'latest': (sub[-1] if sub else str(a.get('publicationYear')))[:10],
            'item': {'itemType': 'preprint', 'title': re.sub(r'\s+', ' ', a['titles'][0]['title']).strip(), 'creators': creators,
                     'date': (sub[0] if sub else str(a.get('publicationYear')))[:10], 'repository': 'arXiv',
                     'archiveID': f'arXiv:{aid}', 'url': f'https://arxiv.org/abs/{aid}', 'abstractNote': abstract.strip()}}

def arxiv_page(aid):
    """Journal ref, Comments and Related DOI from the arXiv abstract page."""
    def go():
        page = http(f'https://arxiv.org/abs/{aid}', headers={'User-Agent': 'Mozilla/5.0 (zot_fixer)'}, raw=True)
        if not isinstance(page, str): return page
        def cell(cls):
            m = re.search(rf'class="tablecell {cls}[^"]*">(.*?)</td>', page, re.S)
            return html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip() if m else ''
        doi = re.search(r'Related DOI:.*?(10\.\d{4,9}/[^\s"<]+)', page, re.S)
        return {'jref': cell('jref'), 'comments': cell('comments'), 'related_doi': doi.group(1) if doi else None}
    return cached('arxivpage', aid, go)

# ----------------------------------------------------------------------------- others
def biorxiv_published(doi):
    def go():
        r = http(f'https://api.biorxiv.org/details/biorxiv/{doi}')
        pubs = [c.get('published') for c in (r or {}).get('collection', []) if c.get('published') not in (None, 'NA')] if isinstance(r, dict) else []
        return pubs[-1] if pubs else None
    return cached('biorxiv', doi.lower(), go)

def openreview_search(title):
    """OpenReview notes (incl. its DBLP mirror) with a title close to `title`."""
    def go():
        r = http('https://api2.openreview.net/notes/search?' + urllib.parse.urlencode({'term': title, 'type': 'terms', 'content': 'title', 'limit': 10}))
        if not isinstance(r, dict): return r
        out = []
        for n in r.get('notes', []):
            c = n.get('content', {}); g = lambda x: x.get('value') if isinstance(x, dict) else x
            out.append({'forum': n.get('forum'), 'title': g(c.get('title')), 'venue': g(c.get('venue')), 'venueid': g(c.get('venueid')),
                        'authors': [a['fullname'] if isinstance(a, dict) else a for a in (g(c.get('authors')) or [])],
                        'date': n.get('pdate') or n.get('cdate')})
        return out
    hits = cached('openreview', norm(title), go)
    return [h for h in hits if h.get('title') and sim(h['title'], title) > 0.93] if isinstance(hits, list) else []

def semantic_scholar(ids):
    """Batch lookup ('ARXIV:<id>' / 'DOI:<doi>') -> {id: record}. Fields: venue, year, journal, externalIds."""
    todo = [i for i in ids if CACHE.get(f's2::{i}', 'ERROR') == 'ERROR']
    hdr = {'Content-Type': 'application/json', **({'x-api-key': os.environ['S2_API_KEY']} if os.environ.get('S2_API_KEY') else {})}
    for k in range(0, len(todo), 400):
        chunk = todo[k:k + 400]
        r = http('https://api.semanticscholar.org/graph/v1/paper/batch?fields=title,venue,year,journal,externalIds',
                 data=json.dumps({'ids': chunk}).encode(), headers=hdr, tries=10)
        for i, x in zip(chunk, r if isinstance(r, list) else ['ERROR'] * len(chunk)): CACHE[f's2::{i}'] = x
    return {i: CACHE.get(f's2::{i}') for i in ids}

def translation_server(endpoint, body):
    """Zotero translation-server ('search' for identifiers, 'web' for pages). None if it is not running."""
    def go():
        try:
            req = urllib.request.Request(f'{TRANSLATION_SERVER}/{endpoint}', data=body.encode(), headers={'Content-Type': 'text/plain'})
            with urllib.request.urlopen(req, timeout=120) as r:
                items = json.load(r)
        except urllib.error.HTTPError:
            return None
        except (urllib.error.URLError, ConnectionError):
            return 'ERROR'
        bad = r'verifying your browser|just a moment|access denied|page not found|sign in|log in'
        return [i for i in items if isinstance(i, dict) and i.get('title') and i.get('itemType') != 'webpage'
                and not re.search(bad, i['title'], re.I)] if isinstance(items, list) else None
    r = cached('ts_' + endpoint, body, go)
    return r if isinstance(r, list) else []
