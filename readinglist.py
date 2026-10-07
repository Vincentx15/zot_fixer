#!/usr/bin/env python3
"""Keep Zotero in sync with the Google Doc reading list.

    python3 readinglist.py update path/to/Bibliographie.docx

Compares the export with the previous one (state/readinglist/), resolves the papers that are not in Zotero yet
(published version preferred), and writes out/readinglist_<date>.json for zotero/apply.js:
  - new papers are created in references › Reading list › <their headings>;
  - papers already in Zotero are filed in their heading collections (moved when their heading changed);
  - nothing is ever deleted: entries removed from the doc are only listed in the report.
"""
import glob, json, os, shutil, sys, time
from concurrent.futures import ThreadPoolExecutor

from zbib import net, readinglist as rl
from zbib.library import Library, ROOT_COLLECTION, READING_LIST, write_changes
from zbib.net import ROOT, norm, sim, family

STATE = os.path.join(ROOT, 'state', 'readinglist')
MAPPING = os.path.join(STATE, 'mapping.json')   # entry id -> Zotero item key

def main(docx):
    lib = Library()
    entries = rl.parse(docx)
    previous = sorted(glob.glob(os.path.join(STATE, '*.docx')))
    changes = rl.diff(rl.parse(previous[-1]), entries) if previous else None
    mapping = json.load(open(MAPPING)) if os.path.exists(MAPPING) else {}

    # 1. which Zotero item is each entry? (saved mapping, exact title, then full resolution)
    todo = []
    for e in entries:
        key = mapping.get(e['id'])
        if key not in lib.by_key:
            keys = lib.by_title.get(norm(e['clean_title']), [])
            key = keys[0] if len(keys) == 1 else None
        if key: e['key'] = key
        else: todo.append(e)
    print(f'{len(entries)} entries, {len(entries) - len(todo)} already in Zotero, resolving {len(todo)}…', flush=True)
    path2key = {tuple(lib.collection_path(k)): k for k in lib.collections}
    def in_heading(e, item):
        """Same first author and a similar title in the entry's heading collection: the same work, retitled
        when it was published (e.g. "BindCraft: one-shot design…" -> "One-shot design … with BindCraft")."""
        ck = path2key.get(tuple([ROOT_COLLECTION, READING_LIST] + e['path']))
        fam = family((item.get('creators') or [None])[0])
        cands = [i for i in lib.items if ck in (i.get('collections') or []) and fam and family((i.get('creators') or [None])[0]) == fam]
        def score(i):
            t, others = i.get('title') or '', [item.get('title') or '', e['clean_title']]
            words = lambda x: {w for w in norm(x).split() if len(w) > 3}
            first = lambda x: (norm(x).split() or [''])[0]
            return max(max(sim(t, o) for o in others),
                       max(len(words(t) & words(o)) / max(1, len(words(t) | words(o))) + 0.25 for o in others),  # shared words
                       max(0.6 if len(first(t)) >= 4 and first(t) == first(o) else 0 for o in others))           # same distinctive first word
        best = max(cands, key=score, default=None)
        return best['key'] if best and score(best) >= 0.6 else None
    with ThreadPoolExecutor(4) as ex:
        for e, (item, how) in zip(todo, ex.map(rl.resolve, todo)):
            e['item'], e['how'] = item, how
            if item:
                key = lib.find(item) or in_heading(e, item)
                if key: e['key'] = key
    net.save_cache()

    # 2. operations
    ops, created, unresolved = [], {}, []
    wanted = {}  # item key (or new item) -> heading paths
    for e in entries:
        path = [ROOT_COLLECTION, READING_LIST] + e['path']
        if e.get('key'):
            mapping[e['id']] = e['key']
            wanted.setdefault(e['key'], set()).add(tuple(path))
        elif e.get('item'):
            sig = (e['item'].get('DOI') or '').lower() or norm(e['item'].get('title'))
            if sig in created: created[sig]['collections'].append(path); continue
            op = {'op': 'create', 'item': e['item'], 'collections': [[ROOT_COLLECTION], path],
                  'why': f"reading list: {e['clean_title'][:80]} ({e['how']})"}
            created[sig] = op; ops.append(op)
        elif e.get('how') != 'not a paper' and (e['urls'] or rl.looks_like_title(e['clean_title'])):
            unresolved.append(e)  # notes and non-paper links (code, blogs) are not reported
    rl_root = [ROOT_COLLECTION, READING_LIST]
    for key, paths in wanted.items():
        current = {tuple(lib.collection_path(c)) for c in lib.by_key[key].get('collections', [])}
        for p in sorted(paths - current):
            ops.append({'op': 'collect', 'key': key, 'path': list(p), 'why': 'reading-list heading'})
        for p in sorted(current - paths):  # heading collections the paper is no longer under
            if list(p[:2]) == rl_root and len(p) > 2:
                ops.append({'op': 'uncollect', 'key': key, 'path': list(p), 'why': 'heading changed in the doc'})

    # 3. report, change file, new baseline
    L = [f'# Reading list update — {os.path.basename(docx)}', '',
         f'{len(entries)} entries; {sum(o["op"] == "create" for o in ops)} papers to add, '
         f'{sum(o["op"] == "collect" for o in ops)} to file in heading collections, {sum(o["op"] == "uncollect" for o in ops)} to un-file; '
         f'{len(unresolved)} unresolved.', '']
    if changes:
        L += [f'## Changes since {os.path.basename(previous[-1])}', '',
              f"- added: {len(changes['added'])}, removed: {len(changes['removed'])}, moved: {len(changes['moved'])}, comments edited: {len(changes['comments'])}", '']
        L += [f"- removed from the doc (still in Zotero): {e['clean_title'][:90]}" for e in changes['removed']]
    L += ['', '## Papers to add', ''] + [f"- {o['item'].get('title', '')[:90]} — {o['why'].split('(', 1)[-1].rstrip(')')}" for o in ops if o['op'] == 'create']
    L += ['', '## Unresolved (check by hand, or ask Claude to search them)', ''] + \
         [f"- {' › '.join(e['path'][-2:])}: {e['title'][:100]} {e['urls'][:1]}" for e in unresolved]
    path = write_changes('readinglist', ops, L)
    os.makedirs(STATE, exist_ok=True)
    json.dump(mapping, open(MAPPING, 'w'), indent=1)
    shutil.copy(docx, os.path.join(STATE, time.strftime('%Y-%m-%d_') + os.path.basename(docx)))
    print(f'wrote {path} (+ .md report). Apply it with zotero/apply.js.')

if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'update': sys.exit(__doc__)
    main(sys.argv[2])
