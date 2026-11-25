// Assign every export in a snapshot the same safe stem and sequence width.
export function outputNames(sourceName, count) {
  const digits = Math.max(2, String(count).length);
  const encoder = new TextEncoder();
  const suffix = '_변환_문제' + '0'.repeat(digits) + '.png';
  const limit = 255 - encoder.encode(suffix).length;
  let stem = sourceName.replace(/\.png$/i, '')
    .replace(/[<>:"/\\|?*\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, '_')
    .replace(/^[ .]+/, '');
  if (!stem.trim()) stem = '학습지';
  if (/^(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])\s*\./i.test(stem)) stem = '_' + stem;
  let safe = '', bytes = 0;
  for (const ch of stem) {
    const size = encoder.encode(ch).length;
    if (bytes + size > limit) break;
    safe += ch;
    bytes += size;
  }
  const prefix = safe + '_변환';
  return {
    zip: prefix + '.zip',
    png: ordinal => prefix + '_문제' + String(ordinal).padStart(digits, '0') + '.png'
  };
}
