#!/usr/bin/env python3
"""Clean Zotero items and upgrade preprints to their published version.

    python3 check.py --since 2026-11-01     items added since a date (e.g. added by hand)
    python3 check.py --preprints            every preprint: has it been published?
    python3 check.py --all                  everything

Writes out/check_<date>.json for zotero/apply.js and a .md report. Per item:
  - preprint (or workshop paper) -> published version, with the workshop/arXiv rule;
  - missing DOI filled from Crossref (exact title + first author + year);
  - arXiv traces removed from published items; HTML / ALL CAPS titles flagged;
  - citation key pinned ("Citation Key: <key>" in Extra) so \\cite{} keys never change.
Uncertain cases are only listed in the report.
"""
import argparse, re
from concurrent.futures import ThreadPoolExecutor

from zbib import net, rules
from zbib.library import Library, bbt_citekeys, is_preprint, pinned_key, write_changes

def check(item):
    out, notes = {}, []
    if is_preprint(item) or re.search(r'workshop', item.get('proceedingsTitle') or '', re.I):
        pub = rules.find_published(item)
        already_workshop = item['itemType'] == 'conferencePaper'
        if pub['update'] and not (already_workshop and pub['status'] == 'workshop'):  # a workshop paper only moves up
            out.update(rules.update_fields(item, pub['update'])); notes.append(f"{pub['status']}: {pub['evidence']}")
        elif pub['status'] == 'unsure': notes.append(f"check by hand: {pub['evidence']}")
    else:
        if not item.get('DOI') and item['itemType'] in ('journalArticle', 'conferencePaper', 'bookSection'):
            cr = rules.crossref_match(item, published_only=False)
            venue = item.get('publicationTitle') or item.get('proceedingsTitle') or item.get('bookTitle') or ''
            container = (cr.get('container-title') or [''])[0] if cr else ''
            same_venue = not venue or net.sim(venue, container) > 0.5 or (rules.conf_of(venue)[0] and rules.conf_of(venue)[0] == rules.conf_of(container)[0])
            if cr and net.sim((cr.get('title') or [''])[0], item.get('title')) > 0.95 and same_venue:  # same title at another venue = another paper
                out['DOI'] = cr['DOI']; notes.append('DOI from Crossref')
        out.update(rules.arxiv_trace_cleanup(item))
    t = item.get('title') or ''
    if re.search(r'<[^>]+>', t): out['title'] = rules.tidy(t)
    letters = [c for c in t if c.isalpha()]
    if len(letters) > 15 and sum(c.isupper() for c in letters) / len(letters) > 0.9: notes.append('title in ALL CAPS: fix by hand')
    return out, notes

def duplicates(lib):
    """Pairs of items with the same DOI, or the same title and first author."""
    seen, out = {}, []
    for i in lib.items:
        for sig, why in (((i.get('DOI') or '').lower(), 'same DOI'), (net.norm(i.get('title')) + '|' + net.family((i.get('creators') or [None])[0]), 'same title and author')):
            if not sig or sig == '|': continue
            if sig in seen and seen[sig]['key'] != i['key']: out.append((seen[sig], i, why))
            else: seen[sig] = i
    unique = {}
    for a, b, why in out: unique.setdefault(tuple(sorted((a['key'], b['key']))), (a, b, why))
    return list(unique.values())

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--since', help='YYYY-MM-DD: items added since this date')
    g.add_argument('--preprints', action='store_true')
    g.add_argument('--all', action='store_true')
    a = ap.parse_args()
    lib = Library()
    items = [i for i in lib.items if (a.all or (a.preprints and is_preprint(i)) or (a.since and i['dateAdded'][:10] >= a.since))]
    print(f'checking {len(items)} items…', flush=True)
    keys = bbt_citekeys([i['key'] for i in items if not pinned_key(i)])
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(check, items))
    net.save_cache()
    ops, L = [], ['# Check', '']
    for item, (fields, notes) in zip(items, results):
        if not pinned_key(item) and keys.get(item['key']):
            fields['extra'] = ((fields.get('extra', item.get('extra')) or '') + f"\nCitation Key: {keys[item['key']]}").strip()
        if fields:
            ops.append({'op': 'update', 'key': item['key'], 'fields': fields, 'why': '; '.join(notes) or 'pin citation key'})
        if notes: L.append(f"- {item.get('title', '')[:80]} — {'; '.join(notes)}")
    dups = duplicates(lib)
    L.insert(1, f'{len(items)} items checked, {len(ops)} to update, {len(dups)} duplicate pairs in the library.')
    if dups:
        L += ['', '## Duplicates (merge them in Zotero → Duplicate Items, or select both → Merge Items)', '']
        L += [f"- {a.get('title', '')[:70]} ({a['key']}) / {b.get('title', '')[:70]} ({b['key']}) — {why}" for a, b, why in dups]
    path = write_changes('check', ops, L)
    print(f'wrote {path} (+ .md report). Apply it with zotero/apply.js.')

if __name__ == '__main__':
    main()
