// Undo apply_fixes.js: restores every item listed in an undo_*.json file to its previous state.
// Zotero → Tools → Developer → Run JavaScript, tick "Run as async function", set UNDO_FILE, Run.

const UNDO_FILE = '/home/vincent/Documents/demande_financements/2026 - JCJC/zotero_audit/apply/undo_REPLACE_ME.json';

const undo = await IOUtils.readJSON(UNDO_FILE);
const libraryID = Zotero.Libraries.userLibraryID;
let restored = 0, missing = [];
for (const u of undo) {
  const item = await Zotero.Items.getByLibraryAndKeyAsync(libraryID, u.itemKey);
  if (!item) { missing.push(u.citationKey); continue; }
  if (u.trashed) { item.deleted = false; await item.saveTx(); restored++; continue; }
  const before = Object.assign({}, u.before);
  // fromJSON would also rewrite collections/relations/tags; keep the current ones
  delete before.collections; delete before.relations; delete before.tags;
  delete before.key; delete before.version; delete before.dateAdded; delete before.dateModified;
  item.fromJSON(before);
  await item.saveTx();
  restored++;
}
return `restored ${restored} items` + (missing.length ? `; not found: ${missing.join(', ')}` : '');
