"""Read the Zotero library (local API, read-only) and write change files for zotero/apply.js.

Needs Zotero running with Settings → Advanced → "Allow other applications on this computer to communicate
with Zotero" ticked, and the Better BibTeX plugin (for citation keys).
"""
import json, os, re, time, urllib.request

from .net import ROOT, norm, family

API = 'http://127.0.0.1:23119/api/users/0'
BBT = 'http://127.0.0.1:23119/better-bibtex/json-rpc'
ROOT_COLLECTION = 'references'   # where references live
READING_LIST = 'Reading list'    # references › Reading list › <headings of the Google Doc>

def _get(path):
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.load(r), int(r.headers.get('Total-Results', 0))

def _all(path):
    out, start = [], 0
    while True:
        sep = '&' if '?' in path else '?'
        page, total = _get(f'{path}{sep}limit=100&start={start}')
        out += page; start += 100
        if start >= total: return out

def check_running():
    try:
        _get('/items?limit=1')
    except Exception as ex:
        raise SystemExit('Cannot read Zotero: start it and tick Settings → Advanced → "Allow other applications '
                         f'on this computer to communicate with Zotero" ({ex})')

class Library:
    def __init__(self):
        check_running()
        self.items = [i['data'] for i in _all('/items/top?format=json') if i['data']['itemType'] not in ('note', 'attachment')]
        self.items = [i for i in self.items if not i.get('deleted')]
        self.by_key = {i['key']: i for i in self.items}
        self.collections = {c['key']: c['data'] for c in _all('/collections?format=json')}
        self.children = {}
        for c in self.collections.values(): self.children.setdefault(c.get('parentCollection') or None, []).append(c)
        self._index()

    def _index(self):
        self.by_doi, self.by_arxiv, self.by_title = {}, {}, {}
        for i in self.items:
            if i.get('DOI'): self.by_doi[i['DOI'].lower()] = i['key']
            for a in arxiv_ids(i): self.by_arxiv[a] = i['key']
            self.by_title.setdefault(norm(i.get('title')), []).append(i['key'])

    def collection_path(self, key):
        path, c = [], self.collections.get(key)
        while c:
            path.insert(0, c['name']); c = self.collections.get(c.get('parentCollection'))
        return path

    def find(self, item):
        """Key of the library item describing the same work (DOI, arXiv id, or title + first author), else None."""
        from difflib import SequenceMatcher
        if item.get('DOI') and item['DOI'].lower() in self.by_doi: return self.by_doi[item['DOI'].lower()]
        for a in arxiv_ids(item):
            if a in self.by_arxiv: return self.by_arxiv[a]
        t, fam = norm(item.get('title')), family((item.get('creators') or [None])[0])
        if t in self.by_title and len(self.by_title[t]) == 1: return self.by_title[t][0]
        best = None
        for other in self.items:
            ot = norm(other.get('title'))
            if abs(len(ot) - len(t)) > 40: continue
            s = SequenceMatcher(None, t, ot).ratio()
            if s >= 0.9 and (not fam or fam == family((other.get('creators') or [None])[0])) and (not best or s > best[0]):
                best = (s, other['key'])
        return best[1] if best else None

def arxiv_ids(item):
    blob = ' '.join(str(item.get(k, '')) for k in ('archiveID', 'url', 'DOI', 'extra'))
    return set(re.findall(r'(\d{4}\.\d{4,5})', blob)) if re.search(r'arxiv|10\.48550', blob, re.I) else set()

def is_preprint(item):
    return item['itemType'] == 'preprint' or bool(re.search(r'arxiv|biorxiv|medrxiv|chemrxiv', item.get('publicationTitle', ''), re.I))

def pinned_key(item):
    m = re.search(r'^Citation Key:\s*(\S+)', item.get('extra', ''), re.M)
    return m.group(1) if m else None

def bbt_citekeys(item_keys):
    """Better BibTeX citation keys of these Zotero items."""
    out = {}
    for k in range(0, len(item_keys), 200):
        req = urllib.request.Request(BBT, data=json.dumps({'jsonrpc': '2.0', 'method': 'item.citationkey',
                                     'params': [item_keys[k:k + 200]], 'id': 1}).encode(), headers={'Content-Type': 'application/json'})
        out.update(json.load(urllib.request.urlopen(req, timeout=60)).get('result') or {})
    return out

def write_changes(name, ops, report_lines):
    """out/<name>_<date>.json for zotero/apply.js, and a matching .md report."""
    os.makedirs(os.path.join(ROOT, 'out'), exist_ok=True)
    stamp = time.strftime('%Y-%m-%d_%H%M')
    path = os.path.join(ROOT, 'out', f'{name}_{stamp}.json')
    json.dump(ops, open(path, 'w'), indent=1, ensure_ascii=False)
    open(path[:-5] + '.md', 'w').write('\n'.join(report_lines) + '\n')
    return path
