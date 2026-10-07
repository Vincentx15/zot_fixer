// Apply a change file written by check.py or readinglist.py (out/*.json) to the Zotero library.
//
// Zotero → Tools → Developer → Run JavaScript, tick "Run as async function", paste this file, set CHANGES, Run.
// DRY_RUN = true writes a log of what would change and changes nothing; then set DRY_RUN = false.
// A real run writes <changes>.undo.json; undo.js reverts it.
//
// Operations (items are addressed by their Zotero key):
//   {op: 'create',    item: {Zotero JSON}, collections: [[path], …]}
//   {op: 'update',    key, fields: {field: value, itemType?, creators?}}
//   {op: 'collect',   key, path: ['references', 'Reading list', …]}   (missing collections are created)
//   {op: 'uncollect', key, path}
//   {op: 'trash',     key}

const DRY_RUN = true;
const CHANGES = '/home/vincent/projects/non_research/zot_fixer/out/REPLACE_ME.json';

const ops = await IOUtils.readJSON(CHANGES);
const libraryID = Zotero.Libraries.userLibraryID;
const log = [], undo = [], stats = {};
const count = k => { stats[k] = (stats[k] || 0) + 1; };
const byKey = key => Zotero.Items.getByLibraryAndKeyAsync(libraryID, key);

const planned = new Set();  // dry run: collections that would be created
async function collection(path) {  // find or create references › … › leaf
  let parent = null, c = null;
  for (let i = 0; i < path.length; i++) {
    c = Zotero.Collections.getByLibrary(libraryID, true).find(x => x.name === path[i] && (x.parentID || null) === (parent ? parent.id : null));
    if (!c) {
      if (DRY_RUN) {  // this level and the ones below would be created
        for (let j = i; j < path.length; j++) { const id = path.slice(0, j + 1).join(' › '); if (!planned.has(id)) { planned.add(id); count('collections created'); } }
        return null;
      }
      count('collections created');
      c = new Zotero.Collection(); c.libraryID = libraryID; c.name = path[i]; if (parent) c.parentID = parent.id;
      await c.saveTx(); undo.push({ op: 'createdCollection', key: c.key });
    }
    parent = c;
  }
  return c;
}

function setFields(item, fields) {
  const changed = [];
  if (fields.itemType && fields.itemType !== item.itemType) { changed.push(`itemType ${item.itemType} -> ${fields.itemType}`); item.setType(Zotero.ItemTypes.getID(fields.itemType)); }
  for (const [f, v] of Object.entries(fields)) {
    if (f === 'itemType') continue;
    if (f === 'creators') { changed.push(`creators -> ${v.length}`); item.setCreators(v); continue; }
    const id = Zotero.ItemFields.getID(f);
    if (!id || !Zotero.ItemFields.isValidForType(id, item.itemTypeID)) { changed.push(`(${f} not valid for ${item.itemType})`); continue; }
    const cur = item.getField(f) || '';
    if (cur !== v) { changed.push(`${f}: "${String(cur).slice(0, 60)}" -> "${String(v).slice(0, 60)}"`); item.setField(f, v); }
  }
  return changed;
}

for (const op of ops) {
  try {
    if (op.op === 'create') {
      count('create');
      log.push(`CREATE ${op.item.itemType} "${(op.item.title || '').slice(0, 80)}" in ${op.collections.map(p => p.join(' › ')).join(' | ')}`);
      if (DRY_RUN) { for (const p of op.collections) await collection(p); continue; }
      const item = new Zotero.Item(op.item.itemType); item.libraryID = libraryID;
      const { itemType, ...fields } = op.item;
      setFields(item, fields);
      for (const p of op.collections) item.addToCollection((await collection(p)).id);
      await item.saveTx(); undo.push({ op: 'created', key: item.key });
      continue;
    }
    const item = await byKey(op.key);
    if (!item) { count('not found'); log.push(`NOT FOUND ${op.key}`); continue; }
    const label = `${op.key} "${item.getField('title').slice(0, 70)}"`;
    const before = item.toJSON();
    if (op.op === 'update') {
      if (DRY_RUN) { const clone = item.clone(); const ch = setFields(clone, op.fields); count('update'); log.push(`UPDATE ${label} (${op.why || ''})\n    ` + ch.join('\n    ')); continue; }
      const ch = setFields(item, op.fields);
      if (!ch.length) { count('unchanged'); continue; }
      count('update'); log.push(`UPDATED ${label}\n    ` + ch.join('\n    '));
      undo.push({ op: 'updated', key: op.key, before }); await item.saveTx();
    } else if (op.op === 'collect' || op.op === 'uncollect') {
      const c = await collection(op.path);
      count(op.op); log.push(`${op.op.toUpperCase()} ${label} ${op.path.join(' › ')}`);
      if (DRY_RUN || !c) continue;
      if (op.op === 'collect') item.addToCollection(c.id); else item.removeFromCollection(c.id);
      undo.push({ op: op.op, key: op.key, collection: c.key }); await item.saveTx();
    } else if (op.op === 'trash') {
      count('trash'); log.push(`TRASH ${label}`);
      if (!DRY_RUN) { item.deleted = true; await item.saveTx(); undo.push({ op: 'trashed', key: op.key }); }
    }
  } catch (e) { count('errors'); log.push(`ERROR ${JSON.stringify(op).slice(0, 120)}: ${e}`); }
}

const base = CHANGES.replace(/\.json$/, '');
await IOUtils.writeUTF8(`${base}.${DRY_RUN ? 'dryrun' : 'applied'}.log`, `${JSON.stringify(stats)}\n\n${log.join('\n')}\n`);
if (!DRY_RUN) await IOUtils.writeJSON(`${base}.undo.json`, undo);
return `${DRY_RUN ? 'DRY RUN, nothing changed' : 'APPLIED'}: ${JSON.stringify(stats)} — log: ${base}.${DRY_RUN ? 'dryrun' : 'applied'}.log`;
