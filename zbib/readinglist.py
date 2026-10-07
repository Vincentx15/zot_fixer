"""The Google Doc reading list: parse its Word export, and resolve each entry to a Zotero item.

Document format: headings (Heading 1..6) organise the papers; an entry is a title line, optional comment
lines ("ls"/"toto" are placeholders), then its link(s); entries are separated by blank lines or headings.
"""
import re, urllib.parse, zipfile

from . import net, rules
from .net import norm, sim

PLACEHOLDERS = {'ls', 'toto', '?', ''}
URL_RE = re.compile(r'^(?:https?://|www\.|arxiv\.org/|doi\.org/)\S+$', re.I)
NOT_PAPERS = r'github\.com|huggingface\.co|notion\.site|youtube|twitter\.com|x\.com/|medium\.com|substack|blog|readthedocs|valencelabs|/wiki'

# ----------------------------------------------------------------------------- parsing
def paragraphs(docx_path):
    """[(heading level or None, text, [link targets])] in document order, table of contents excluded."""
    z = zipfile.ZipFile(docx_path)
    doc, styles = z.read('word/document.xml').decode(), z.read('word/styles.xml').decode()
    rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', z.read('word/_rels/document.xml.rels').decode()))
    levels = {}
    for m in re.finditer(r'<w:style [^>]*w:styleId="([^"]+)".*?</w:style>', styles, re.S):
        lvl = re.search(r'<w:outlineLvl w:val="(\d)"', m.group(0))
        name = re.search(r'<w:name w:val="([^"]+)"', m.group(0))
        if lvl: levels[m.group(1)] = int(lvl.group(1))
        elif name and re.match(r'(?i)(heading|titre) ?(\d)$', name.group(1)): levels[m.group(1)] = int(name.group(1)[-1]) - 1
    out = []
    for p in re.findall(r'<w:p[ >].*?</w:p>', doc, re.S):
        sid = re.search(r'<w:pStyle w:val="([^"]+)"', p)
        if sid and re.match(r'(?i)toc|contents|sommaire', sid.group(1)): continue
        olvl = re.search(r'<w:outlineLvl w:val="(\d)"', p)
        lvl = int(olvl.group(1)) if olvl else levels.get(sid.group(1)) if sid else None
        text = re.sub(r'\s+', ' ', ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p)).replace('​', '')).strip()
        links = [rels[r] for r in re.findall(r'<w:hyperlink [^>]*r:id="(rId\d+)"', p) if r in rels]
        out.append((lvl if lvl is not None and lvl <= 5 else None, text, links))
    return out

def parse(docx_path):
    """Entries: {'path': [headings], 'title', 'comments': [...], 'urls': [...]} in document order."""
    entries, stack, cur = [], [], None

    def flush():
        nonlocal cur
        if cur and (cur['title'] or cur['urls']):
            cur['comments'] = [c for c in cur['comments'] if c.lower() not in PLACEHOLDERS]
            cur.pop('pending', None)
            entries.append(cur)
        cur = None

    for lvl, text, links in paragraphs(docx_path):
        if lvl is not None:
            flush()
            if text and re.sub(r'\W', '', text.lower()) != 'index':
                stack = [(l, t) for l, t in stack if l < lvl] + [(lvl, re.sub(r'[\s:]+$', '', text))]
            continue
        is_link = bool(URL_RE.match(text)) or (links and text in links)
        if not text and not links:
            if cur and cur['urls']: flush()
            elif cur: cur['pending'] = True
            continue
        if is_link:
            if cur is None: cur = {'path': [t for _, t in stack], 'title': '', 'comments': [], 'urls': []}
            cur['urls'] += links or [text if text.lower().startswith('http') else 'https://' + text]
            continue
        if cur and cur['urls']: flush()
        elif cur and cur.get('pending') and len(text) <= 200 and (text.endswith(':') or not re.search(r'[.!?]$', text)):
            flush()  # a new title after a link-less entry
        if cur is None:
            cur = {'path': [t for _, t in stack], 'title': text, 'comments': [], 'urls': []}
        else:
            cur['comments'].append(text)
    flush()
    for e in entries:
        t = re.sub(r'\s*:\s*$', '', e['title'])
        m = re.search(r'\(([^()]*?(19|20)\d\d[^()]*)\)\s*$', t)
        e['clean_title'] = t[:m.start()].strip() if m else t
        e['id'] = norm(e['clean_title'])[:80] + '|' + (e['urls'][0] if e['urls'] else '')
    return entries

def diff(old, new):
    """Entries added, removed, moved (heading changed) or with edited comments, matched on title + first link."""
    o, n = {e['id']: e for e in old}, {e['id']: e for e in new}
    return {'added': [n[k] for k in n if k not in o], 'removed': [o[k] for k in o if k not in n],
            'moved': [(o[k], n[k]) for k in n if k in o and o[k]['path'] != n[k]['path']],
            'comments': [n[k] for k in n if k in o and o[k]['comments'] != n[k]['comments']]}

# ----------------------------------------------------------------------------- resolution
def identify(url):
    u = urllib.parse.unquote(url.strip())
    for pat, kind, fmt in (
            (r'arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})', 'arxiv', '{}'),
            (r'openreview\.net/(?:forum|pdf)\?id=([\w\-]+)', 'openreview', '{}'),
            (r'(PMC\d+)', 'pmcid', '{}'),
            (r'(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d+)', 'pmid', '{}'),
            (r'/pii/(S?[\dX]{15,17})', 'pii', '{}'),
            (r'biorxiv\.org/content/early/\d{4}/\d\d/\d\d/(\d+)', 'doi', '10.1101/{}'),
            (r'elifesciences\.org/articles/(\d+)', 'doi', '10.7554/eLife.{}'),
            (r'nature\.com/articles/([\w\-.]+?)(?:\.pdf)?(?:[?#].*)?$', 'doi', '10.1038/{}')):
        m = re.search(pat, u, re.I)
        if m: return kind, fmt.format(re.sub(r'v\d+$|\.pdf$', '', m.group(1)))
    m = re.search(r'(10\.\d{4,9}/[^\s?#&]+)', u)
    if m:
        doi = re.sub(r'(v\d+)?(\.(full|abstract|supplementary-material|full-text)(\.pdf)?|\.pdf)?/?$', '', m.group(1)) \
            if re.match(r'10\.(1101|64898)/', m.group(1)) else m.group(1)
        return 'doi', re.sub(r'(/(abstract|full|pdf|epdf|meta|fulltext|html))+$|\.pdf$', '', doi, flags=re.I).rstrip('/.')
    return 'web', url

LANDING = [(r'(proceedings\.mlr\.press/v\d+/[\w\-]+?)(?:/[\w\-]+)?\.pdf$', r'https://\1.html'),
           (r'((?:papers\.nips\.cc|proceedings\.neurips\.cc)/paper(?:_files/paper)?/\d{4})/file/(\w+)-Paper[\w\-]*\.pdf$', r'https://\1/hash/\2-Abstract.html'),
           (r'(openaccess\.thecvf\.com/.*?)/papers/([\w\-]+)_paper\.pdf$', r'https://\1/html/\2_paper.html')]

def item_from_doi(doi):
    cr = net.crossref_doi(doi)
    if not cr: return None
    if cr.get('type') == 'posted-content':
        item = rules.item_from_crossref(cr)
        repo = 'bioRxiv' if re.match(r'10\.(1101|64898)/', doi) else 'ChemRxiv' if doi.startswith('10.26434') else ''
        item = {k: v for k, v in item.items() if k not in ('publicationTitle', 'volume', 'issue', 'pages')}
        return {**item, 'itemType': 'preprint', 'repository': repo}
    return rules.item_from_crossref(cr)

def resolve_url(url, doc_title):
    for pat, rep in LANDING: url = re.sub(r'^https?://' + pat, rep, url)
    kind, ident = identify(url)
    if kind == 'pmcid' or kind == 'pmid':
        r = net.cached('ncbi', ident, lambda: net.http(f'https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={ident}&format=json&tool=zotero-maintenance'))
        doi = ((r or {}).get('records') or [{}])[0].get('doi') if isinstance(r, dict) else None
        kind, ident = ('doi', doi) if doi else (kind, ident)
    if kind == 'pii':
        doi = net.crossref_by_pii(ident)
        kind, ident = ('doi', doi) if doi else ('web', url)
    if kind == 'arxiv':
        ar = net.datacite_arxiv(ident)
        if ar: return ar['item'], f'arXiv {ident}'
    if kind == 'doi':
        item = item_from_doi(ident)
        if item: return item, f'DOI {ident}'
    if kind == 'openreview':
        for h in net.openreview_search(doc_title):
            if h['forum'] == ident or sim(h['title'], doc_title) > 0.93:
                v = h.get('venue') or ''
                return {'itemType': 'preprint', 'title': h['title'], 'repository': 'OpenReview', 'url': f'https://openreview.net/forum?id={h["forum"]}',
                        'date': rules.year_in(v) and str(rules.year_in(v)) or '',
                        'creators': [{'creatorType': 'author', 'firstName': ' '.join(a.split()[:-1]), 'lastName': a.split()[-1]} for a in h['authors']]}, f'OpenReview {h["forum"]}'
    if re.search(NOT_PAPERS, url, re.I): return None, 'not a paper'
    if kind in ('pmid',):
        items = net.translation_server('search', f'PMID: {ident}')
        if items: return items[0], f'PubMed {ident}'
    items = net.translation_server('web', url)
    if items: return items[0], 'web page (translation-server)'
    return None, None

def title_search(title):
    probe = {'title': title, 'creators': []}
    cr = rules.crossref_match(probe, published_only=False)
    if cr and sim((cr.get('title') or [''])[0], title) > 0.92: return rules.item_from_crossref(cr), f"title → Crossref {cr['DOI']}"
    ar = net.datacite_arxiv(title=title)
    if ar: return ar['item'], f"title → arXiv {ar['id']}"
    return None, None

def looks_like_title(text):
    """Paper titles vs notes: short, no sentence punctuation, not a 'Label:' or a TODO."""
    return 15 < len(text) < 200 and not re.search(r'[.!?:]$', text) and not re.match(r'(?i)todo|careful|other |see |voir ', text) \
        and not text[:1].islower()

def resolve(entry):
    """-> (Zotero item with the published version when there is one, how it was found) or (None, reason)."""
    item = how = None
    for url in entry['urls']:
        item, how = resolve_url(url, entry['clean_title'])
        if item or how == 'not a paper': break
    if not item and how != 'not a paper' and looks_like_title(entry['clean_title']):
        item, how = title_search(entry['clean_title'])
    if not item: return None, how or 'not found'
    for k in ('key', 'version', 'accessDate', 'attachments', 'notes', 'tags', 'seeAlso', 'collections', 'relations'):
        item.pop(k, None)
    item['title'] = rules.tidy(item.get('title'))
    if item['itemType'] == 'preprint' or 'workshop' in (item.get('proceedingsTitle') or '').lower():
        pub = rules.find_published(item)
        if pub['update']:
            item = {**{k: v for k, v in item.items() if k not in ('repository', 'archiveID')}, **pub['update']}
            item.update(rules.arxiv_trace_cleanup(item))
            how = f"{how or ''}; {pub['status']}: {pub['evidence']}"
    return item, how
