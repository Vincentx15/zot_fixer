# zotero-maintenance

Keeps my Zotero library clean and in sync with my reading-list Google Doc.
Python scripts compute the changes; one Zotero script applies them (with dry run and undo).

## Setup (once)

- Zotero 7 with Better BibTeX, running.
- Zotero → Settings → Advanced → tick **Allow other applications on this computer to communicate with Zotero**
  (read-only local API used by the scripts).
- Python ≥ 3.9, standard library only.
- Optional, only for reading-list links to publisher pages without DOI: a Zotero translation-server
  (`git clone --recurse-submodules https://github.com/zotero/translation-server && docker build -t zotero-ts translation-server && docker run -d -p 127.0.0.1:1969:1969 zotero-ts`).

## Use cases

| situation | command |
|---|---|
| I updated the reading-list Google Doc | export it (File → Download → .docx), then `python3 readinglist.py update ~/Downloads/Bibliographie.docx` |
| I added references by hand in Zotero | `python3 check.py --since 2026-11-01` (date of the additions) |
| Preprints may have been published since | `python3 check.py --preprints` |

Each command writes `out/<name>_<date>.json` (the changes) and `out/<name>_<date>.md` (what will change, and what
needs a human decision). Then, in Zotero → Tools → Developer → Run JavaScript (tick *Run as async function*):

1. paste `zotero/apply.js`, set `CHANGES` to the `.json` file, run with `DRY_RUN = true`, read the `.dryrun.log`;
2. set `DRY_RUN = false`, run again. A `.undo.json` is written; `zotero/undo.js` reverts the run.

Cases the scripts cannot settle (venue without year, retitled papers, dead links) are listed in the `.md` report:
give it to Claude to research, as done on 2026-10-07.

## Rules applied

- A preprint is replaced by its journal / main-track conference version. A workshop version replaces it only if
  the arXiv version is not more recent. Published items keep no arXiv trace (DOI, publisher, URL, Extra).
- Titles are only changed when the words differ; years are print years.
- Every item has its citation key pinned (`Citation Key:` in Extra), so `\cite{}` keys never change.
- The reading list lives in `references › Reading list › <doc headings>`; each paper is filed under its
  lowest heading. Nothing is deleted when an entry disappears from the doc: it is reported.

## Layout

```
readinglist.py, check.py   entry points
zbib/net.py                cached lookups: Crossref, DataCite (arXiv), arXiv pages, bioRxiv, OpenReview, Semantic Scholar
zbib/rules.py              published-version detection and cleaning rules
zbib/readinglist.py        Word export parsing, diff, link resolution
zbib/library.py            Zotero local API, Better BibTeX keys, change files
zotero/apply.js, undo.js   run inside Zotero
state/                     lookup cache (not versioned), last reading-list export, entry → item mapping
out/                       change files, reports, logs, undo files (not versioned)
history/                   undo files of the initial clean-up (2026-10-07)
```
