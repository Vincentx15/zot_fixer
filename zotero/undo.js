// Revert a run of apply.js. Zotero → Tools → Developer → Run JavaScript, "Run as async function", set UNDO, Run.
const UNDO = '/home/vincent/Documents/zotero-maintenance/out/REPLACE_ME.undo.json';

const undo = (await IOUtils.readJSON(UNDO)).reverse();
const libraryID = Zotero.Libraries.userLibraryID;
const byKey = key => Zotero.Items.getByLibraryAndKeyAsync(libraryID, key);
const coll = key => Zotero.Collections.getByLibraryAndKey(libraryID, key);
let n = 0;
for (const u of undo) {
  if (u.op === 'createdCollection') { const c = coll(u.key); if (c) { await c.eraseTx(); n++; } continue; }  // items stay in the library
  const item = await byKey(u.key);
  if (!item) continue;
  if (u.op === 'created' || u.op === 'trashed') item.deleted = u.op === 'created';
  if (u.op === 'collect') item.removeFromCollection(coll(u.collection).id);
  if (u.op === 'uncollect') item.addToCollection(coll(u.collection).id);
  if (u.op === 'updated') {
    const before = { ...u.before };
    for (const k of ['key', 'version', 'collections', 'relations', 'tags', 'dateAdded', 'dateModified']) delete before[k];
    item.fromJSON(before);
  }
  await item.saveTx(); n++;
}
return `reverted ${n} changes`;
