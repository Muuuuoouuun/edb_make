#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

function parseArgs(argv) {
  const args = [...argv];
  let nodeModules = process.env.EDB_RHWP_CORE_NODE_MODULES || '';
  let dpi = 160;
  let maxPages = 0;
  const positional = [];

  while (args.length) {
    const arg = args.shift();
    if (arg === '--node-modules') {
      nodeModules = args.shift() || '';
    } else if (arg === '--dpi') {
      dpi = Number(args.shift() || dpi);
    } else if (arg === '--max-pages') {
      maxPages = Number(args.shift() || 0);
    } else if (arg?.startsWith('--')) {
      throw new Error(`Unknown option: ${arg}`);
    } else if (arg) {
      positional.push(arg);
    }
  }

  if (positional.length < 2) {
    throw new Error('Usage: render_hwp_with_rhwp_core.mjs [--node-modules DIR] SOURCE OUTPUT_DIR [--dpi N]');
  }
  if (!nodeModules) {
    throw new Error('Missing --node-modules');
  }
  return {
    sourcePath: path.resolve(positional[0]),
    outputDir: path.resolve(positional[1]),
    nodeModules: path.resolve(nodeModules),
    dpi: Number.isFinite(dpi) && dpi > 0 ? dpi : 160,
    maxPages: Number.isFinite(maxPages) && maxPages > 0 ? Math.floor(maxPages) : 0,
  };
}

function safeStem(filePath) {
  return path.basename(filePath, path.extname(filePath)).replace(/[^\p{L}\p{N}._-]+/gu, '_') || 'document';
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const requireFromModules = createRequire(path.join(options.nodeModules, 'package.json'));
  const rhwpMain = requireFromModules.resolve('@rhwp/core');
  const rhwpDir = path.dirname(rhwpMain);
  const { default: init, HwpDocument, version } = await import(pathToFileURL(rhwpMain).href);
  const sharpModule = requireFromModules('sharp');
  const sharp = sharpModule.default || sharpModule;

  const widthFactor = Number(process.env.EDB_RHWP_TEXT_WIDTH_FACTOR || 8.5);
  globalThis.measureTextWidth = (_font, text) => String(text || '').length * widthFactor;

  await init({ module_or_path: fs.readFileSync(path.join(rhwpDir, 'rhwp_bg.wasm')) });

  const sourceBytes = new Uint8Array(fs.readFileSync(options.sourcePath));
  const doc = new HwpDocument(sourceBytes);
  const pageCount = doc.pageCount();
  const pagesToRender = options.maxPages ? Math.min(pageCount, options.maxPages) : pageCount;
  const stem = safeStem(options.sourcePath);
  fs.mkdirSync(options.outputDir, { recursive: true });

  let documentInfo = '';
  try {
    documentInfo = doc.getDocumentInfo();
  } catch {
    documentInfo = '';
  }

  const pages = [];
  for (let pageIndex = 0; pageIndex < pagesToRender; pageIndex += 1) {
    const svg = doc.renderPageSvg(pageIndex);
    const outputPath = path.join(options.outputDir, `${stem}_page_${String(pageIndex + 1).padStart(3, '0')}.png`);
    const info = await sharp(Buffer.from(svg), { density: options.dpi, limitInputPixels: false })
      .png()
      .toFile(outputPath);
    pages.push({
      page_id: `${stem}-page-${String(pageIndex + 1).padStart(3, '0')}`,
      source_path: options.sourcePath,
      normalized_path: outputPath,
      page_index: pageIndex,
      width_px: info.width || 0,
      height_px: info.height || 0,
      metadata: {
        source_type: 'hwp',
        document_like: true,
        hwp_renderer: 'rhwp-core',
        hwp_renderer_version: version(),
        hwp_renderer_page_count: pageCount,
        hwp_renderer_document_info: documentInfo,
        dpi: options.dpi,
      },
    });
  }
  console.log(JSON.stringify(pages));
}

main().catch((error) => {
  console.error(error?.stack || String(error));
  process.exit(1);
});
