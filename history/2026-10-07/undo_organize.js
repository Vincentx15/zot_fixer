// Undo organize.js: set UNDO_FILE to the undo_organize_*.json written by the real run.
// Zotero → Tools → Developer → Run JavaScript, tick "Run as async function", paste, Run.
const UNDO_FILE = '/home/vincent/Documents/demande_financements/2026 - JCJC/zotero_audit/apply/undo_organize_REPLACE_ME.json';

const undo = await IOUtils.readJSON(UNDO_FILE);
const libraryID = Zotero.Libraries.userLibraryID;
const byKey = key => Zotero.Items.getByLibraryAndKeyAsync(libraryID, key);
let n = 0;
for (const u of undo.reverse()) {
  if (u.trashedAttachment) { const a = await byKey(u.trashedAttachment); if (a) { a.deleted = false; await a.saveTx(); n++; } continue; }
  if (u.createdCollection) {
    const c = Zotero.Collections.getByLibraryAndKey(libraryID, u.createdCollection);
    if (c) { await c.eraseTx(); n++; }  // the items stay in the library, only the collection goes
    continue;
  }
  const item = await byKey(u.itemKey);
  if (!item) continue;
  for (const ck of u.collections) { const c = Zotero.Collections.getByLibraryAndKey(libraryID, ck); if (c) item.removeFromCollection(c.id); }
  if (u.tag) item.removeTag('ownwork');
  if (u.extraBefore !== null) item.setField('extra', u.extraBefore);
  await item.saveTx(); n++;
}
return `undone ${n} changes`;
