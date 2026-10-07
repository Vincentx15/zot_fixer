"""Published-version detection and metadata cleaning, with the library's rules:
  - a preprint is replaced by its journal / main-track conference version;
  - an accepted workshop paper replaces it only if the arXiv version is not more recent;
  - published items carry no arXiv trace (arXiv DOI, publisher "arXiv", arxiv.org URL, "arXiv:" line in Extra);
  - titles are kept unless the words differ (no case-only or punctuation-only rewrites);
  - years are the print/issue year, not the online-first date.
"""
import re

from . import net
from .net import norm, sim, family

CONFS = [  # pattern, short name, proceedings title
    (r'NeurIPS|NIPS|Neural Information Processing Systems', 'NeurIPS', 'Advances in Neural Information Processing Systems'),
    (r'\bICML\b|International Conference on Machine Learning', 'ICML', 'International Conference on Machine Learning'),
    (r'\bICLR\b|Learning Representations', 'ICLR', 'International Conference on Learning Representations'),
    (r'\bCVPR\b|Computer Vision and Pattern Recognition', 'CVPR', 'IEEE/CVF Conference on Computer Vision and Pattern Recognition'),
    (r'\bICCV\b|International Conference on Computer Vision', 'ICCV', 'IEEE/CVF International Conference on Computer Vision'),
    (r'\bECCV\b|European Conference on Computer Vision', 'ECCV', 'European Conference on Computer Vision'),
    (r'\bAAAI\b', 'AAAI', 'Proceedings of the AAAI Conference on Artificial Intelligence'),
    (r'\bIJCAI\b', 'IJCAI', 'International Joint Conference on Artificial Intelligence'),
    (r'\bAISTATS\b|Artificial Intelligence and Statistics', 'AISTATS', 'International Conference on Artificial Intelligence and Statistics'),
    (r'\bUAI\b|Uncertainty in Artificial Intelligence', 'UAI', 'Conference on Uncertainty in Artificial Intelligence'),
    (r'\bKDD\b|Knowledge Discovery', 'KDD', 'ACM SIGKDD Conference on Knowledge Discovery and Data Mining'),
    (r'\bRECOMB\b', 'RECOMB', 'Research in Computational Molecular Biology'),
    (r'\bMICCAI\b', 'MICCAI', 'Medical Image Computing and Computer Assisted Intervention'),
    (r'\bLoG\b|Learning on Graphs', 'LoG', 'Learning on Graphs Conference'),
]
CONF_MONTH = {'NeurIPS': 12, 'ICML': 7, 'ICLR': 5, 'CVPR': 6, 'ICCV': 10, 'AAAI': 2}
PMLR_YEAR = {'97': 2019, '119': 2020, '139': 2021, '162': 2022, '202': 2023, '235': 2024, '267': 2025, '296': 2026}
WORKSHOP = r'workshop|\bMLSB\b|\bGEM\b|\bLMRL\b|\bGRaM\b|AI4Science|non-archival'
NOT_ACCEPTED = r'submitted|submission|under review|withdrawn|rejected|desk|tutorial|talk|poster session only'
BAD_CONTAINERS = r'SSRN|ChemInform|Research Square|Authorea|Preprints|Qeios|Zenodo|figshare'
PREPRINT_DOI = r'^10\.(48550|1101/20\d\d|64898|26434|21203)/'

def conf_of(text):
    for pat, short, proc in CONFS:
        if re.search(pat, text or '', re.I): return short, proc
    return None, None

def tidy(s):
    s = re.sub(r'<[^>]+>', '', s or '').replace('&amp;', '&').replace('\\&', '&').replace('**', '')
    s = re.sub(r'[‐‑‒−─]', '-', s)
    return re.sub(r'\s+', ' ', re.sub(r'\s+:', ':', s)).strip()

def year_in(text):
    ys = re.findall(r'(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)', text or '')
    return int(ys[-1]) if ys else None

# ----------------------------------------------------------------------------- Crossref -> Zotero
def item_from_crossref(cr):
    full = net.crossref_doi(cr['DOI']) or cr
    item = {'title': tidy((cr.get('title') or [''])[0]), 'DOI': cr['DOI']}
    container = tidy((cr.get('container-title') or [''])[0])
    if cr.get('type') == 'proceedings-article':
        item.update(itemType='conferencePaper', proceedingsTitle=container)
        if (cr.get('event') or {}).get('name'): item['conferenceName'] = cr['event']['name']
    elif cr.get('type') == 'book-chapter':
        item.update(itemType='bookSection', bookTitle=container)
    else:
        item.update(itemType='journalArticle', publicationTitle=container)
    for fld in ('published-print', 'journal-issue', 'issued'):  # print year first
        src = (full.get(fld) or {}).get('published-print', {}) if fld == 'journal-issue' else (full.get(fld) or {})
        dp = (src.get('date-parts') or [[None]])[0]
        if dp and dp[0]:
            item['date'] = '-'.join(f'{x:02d}' if i else str(x) for i, x in enumerate(dp)); break
    for src, dst in (('volume', 'volume'), ('issue', 'issue'), ('page', 'pages')):
        if cr.get(src): item[dst] = cr[src]
    if cr.get('author'):
        item['creators'] = [{'creatorType': 'author', 'lastName': a['family'], 'firstName': a.get('given', '')} if a.get('family')
                            else {'creatorType': 'author', 'name': a.get('name', '')} for a in cr['author']]
    return item

def crossref_match(item, published_only=True):
    """Best Crossref record for this item's work: similar title, same first author, plausible year."""
    title, fam = item.get('title', ''), family((item.get('creators') or [None])[0])
    year = int(item['date'][:4]) if (item.get('date') or '')[:4].isdigit() else None
    best, preprint = None, None
    for cr in net.crossref_search(title, fam):
        s = sim((cr.get('title') or [''])[0], title)
        if s < 0.9: continue
        fams = [norm(a.get('family', '')).split(' ')[-1] for a in cr.get('author', [])[:3]]
        if fam and fams and fam not in fams: continue
        if cr.get('type') == 'posted-content': preprint = preprint or cr; continue
        if cr.get('type') not in ('journal-article', 'proceedings-article', 'book-chapter'): continue
        if re.search(BAD_CONTAINERS, (cr.get('container-title') or [''])[0], re.I): continue
        y = ((cr.get('issued') or {}).get('date-parts') or [[None]])[0][0]
        if year and y and (y < year - 1 if published_only else abs(y - year) > 2): continue
        if not best or s > best[0]: best = (s, cr)
    if best: return best[1]
    for rel in ((preprint or {}).get('relation') or {}).get('is-preprint-of', []):  # bioRxiv -> journal link
        if rel.get('id-type') == 'doi': return net.crossref_doi(rel['id'])
    return None

# ----------------------------------------------------------------------------- published version
def find_published(item):
    """-> {'status': journal|conference|workshop|preprint|unsure, 'update': {...} or None, 'evidence': str}.
    'update' holds the Zotero fields of the published version (itemType included)."""
    aid = next(iter(sorted(_arxiv(item))), None)
    page = net.arxiv_page(aid) if aid else None
    page = page if isinstance(page, dict) else {}
    doi = item.get('DOI') or ''
    # 1. explicit links to the published version: arXiv "Related DOI", bioRxiv "published", Crossref
    for d, why in ((page.get('related_doi'), 'arXiv related DOI'),
                   (net.biorxiv_published(doi) if re.match(r'10\.(1101|64898)/', doi) else None, 'bioRxiv: published as')):
        if d and not re.match(PREPRINT_DOI, d):
            cr = net.crossref_doi(d)
            if cr: return _published(item, item_from_crossref(cr), f'{why} {d}')
    cr = crossref_match(item)
    if cr: return _published(item, item_from_crossref(cr), f"Crossref {cr['DOI']}")
    # 2. venue statements: arXiv journal ref / comments, OpenReview decisions, Semantic Scholar
    for field in ('jref', 'comments'):
        text = page.get(field) or ''
        if field == 'comments' and not re.search(r'accepted|published|to appear|camera[- ]ready|proceedings', text, re.I): continue
        if re.search(NOT_ACCEPTED, text, re.I) and not re.search(r'accepted|published', text, re.I): continue
        short, proc = conf_of(text)
        if short and year_in(text):
            ws = bool(re.search(WORKSHOP, text, re.I))
            return _venue(item, aid, text if ws else proc, short, year_in(text), ws, f'arXiv {field}: "{text[:120]}"')
    accepted = [h for h in net.openreview_search(item.get('title', '')) if h.get('venue')
                and not re.search(NOT_ACCEPTED + r'|CoRR|arXiv|bioRxiv|^Crossref$', h['venue'], re.I)
                and 'dblp.org/journals/' not in (h.get('venueid') or '')]
    accepted.sort(key=lambda h: bool(re.search(WORKSHOP, (h['venue'] or '') + (h.get('venueid') or ''), re.I)))  # main track first
    if accepted:
        h = accepted[0]; short, proc = conf_of(h['venue'])
        ws = bool(re.search(WORKSHOP, h['venue'] + (h.get('venueid') or ''), re.I))
        venue = re.sub(r'\s*\b(poster|oral|spotlight|notable[- ]top[- ]\d+%?)\b', '', h['venue'], flags=re.I).strip()
        return _venue(item, aid, venue if ws or not proc else proc, short, year_in(h['venue']), ws, f"OpenReview: {h['venue']}")
    s2 = (net.semantic_scholar([f'ARXIV:{aid}']).get(f'ARXIV:{aid}') if aid else None) or {}
    short, proc = conf_of(s2.get('venue') or '')
    if short:
        j, dblp = s2.get('journal') or {}, (s2.get('externalIds') or {}).get('DBLP') or ''
        y = None
        if short == 'NeurIPS' and str(j.get('volume') or '').isdigit() and 'Neural' in (j.get('name') or ''): y = 1987 + int(j['volume'])
        if short == 'ICML' and str(j.get('volume') or '') in PMLR_YEAR: y = PMLR_YEAR[str(j['volume'])]
        m = re.match(r'conf/\w+/\w+?(\d\d)$', dblp)
        if m: y = 2000 + int(m.group(1))
        if y: return _venue(item, aid, proc, short, y, False, f"Semantic Scholar venue {s2['venue']} ({dblp or 'volume ' + str(j.get('volume'))})")
        return {'status': 'unsure', 'update': None, 'evidence': f"Semantic Scholar says {s2['venue']}, year unknown"}
    return {'status': 'preprint', 'update': None, 'evidence': 'no published version found'}

def _arxiv(item):
    from .library import arxiv_ids
    return arxiv_ids(item)

def _published(item, pub, evidence):
    return {'status': 'conference' if pub['itemType'] == 'conferencePaper' else 'journal', 'update': pub, 'evidence': evidence}

def _venue(item, aid, venue, short, year, workshop, evidence):
    if workshop and aid:
        ar = net.datacite_arxiv(aid)
        if ar and year and ar['latest'][:7] > f"{year}-{CONF_MONTH.get(short, 6):02d}":
            return {'status': 'preprint', 'update': None, 'evidence': f'{evidence} — but the arXiv version ({ar["latest"]}) is newer: kept arXiv'}
    pub = {'itemType': 'conferencePaper', 'proceedingsTitle': venue,
           'conferenceName': venue if workshop or not short else f'{short} {year}', 'date': str(year or '')}
    return {'status': 'workshop' if workshop else 'conference', 'update': pub, 'evidence': evidence}

# ----------------------------------------------------------------------------- building updates
ARXIV_TRACE_FIELDS = ('DOI', 'publisher', 'url')

def update_fields(item, pub):
    """Fields to write on `item` to turn it into `pub`: venue/volume/pages/date/DOI, title only if the words
    differ, full author list only if longer, and no arXiv traces left."""
    out = {'itemType': pub['itemType']} if pub['itemType'] != item['itemType'] else {}
    for f in ('publicationTitle', 'proceedingsTitle', 'bookTitle', 'conferenceName', 'volume', 'issue', 'pages', 'date', 'DOI'):
        new, cur = pub.get(f), item.get(f) or ''
        if not new or str(new).lower() == str(cur).lower(): continue          # DOI case, identical values
        if f == 'date' and len(cur) > 4 and year_in(cur) == year_in(new): continue  # same year, already precise
        out[f] = new
    if pub.get('title') and norm(tidy(pub['title'])) != norm(item.get('title')): out['title'] = tidy(pub['title'])
    if len(pub.get('creators') or []) > len(item.get('creators') or []): out['creators'] = pub['creators']
    out.update(arxiv_trace_cleanup({**item, **out}))
    return out

def arxiv_trace_cleanup(item):
    """Fields to clear on a published item that still carries arXiv identifiers."""
    if item.get('itemType') == 'preprint': return {}
    out = {}
    if re.match(PREPRINT_DOI, item.get('DOI') or '', re.I): out['DOI'] = ''
    if (item.get('publisher') or '').strip().lower() == 'arxiv': out['publisher'] = ''
    if 'arxiv.org' in (item.get('url') or ''): out['url'] = ''
    extra = item.get('extra') or ''
    if re.search(r'^arXiv:.*$', extra, re.M | re.I):
        out['extra'] = re.sub(r'^arXiv:.*$\n?', '', extra, flags=re.M | re.I).strip()
    return out
