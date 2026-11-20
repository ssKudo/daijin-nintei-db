const assert = require('node:assert/strict');
const decoder = require('../docs/decoder.js');
const manifest = require('../docs/data/manifest.json');

decoder.setCodeRules(require('../docs/data/code_rules.json'));
decoder.registerManifestPrefixes(manifest.categories);

let total = 0;
for (const category of manifest.categories) {
  const rows = require('../docs/data/' + category.json_file);
  assert.equal(rows.length, category.record_count, category.id);
  const extracted = decoder.extractCertNosFromText(rows.map((row) => row.cert_no).join('\n'));
  assert.equal(extracted.length, rows.length, category.id);
  for (const [index, row] of rows.entries()) {
    const occurrence = extracted[index];
    assert.equal(occurrence.lineNo, index + 1, row.cert_no);
    assert.equal(occurrence.cert_no, decoder.normalizeCertNo(row.cert_no), row.cert_no);
    assert.equal(occurrence.decoded.is_valid_format, true, row.cert_no);
  }
  total += rows.length;
}
assert.equal(total, manifest.total_records);
console.log('Web extraction and decoding verified for ' + total + ' ledger records.');
