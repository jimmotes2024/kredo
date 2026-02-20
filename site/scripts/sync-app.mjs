import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import path from 'node:path';

const siteRoot = process.cwd();
const sourceDir = path.resolve(siteRoot, '..', 'app');
const destDir = path.resolve(siteRoot, 'public', 'app');

if (!existsSync(sourceDir)) {
  console.error(`[sync:app] Source app directory not found: ${sourceDir}`);
  process.exit(1);
}

if (existsSync(destDir)) {
  rmSync(destDir, { recursive: true, force: true });
}

mkdirSync(path.dirname(destDir), { recursive: true });
cpSync(sourceDir, destDir, {
  recursive: true,
  filter: (src) => path.basename(src) !== '.DS_Store',
});

function removeDsStoreFiles(rootDir) {
  const entries = readdirSync(rootDir);
  for (const entry of entries) {
    const full = path.join(rootDir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      removeDsStoreFiles(full);
      continue;
    }
    if (entry === '.DS_Store') {
      rmSync(full, { force: true });
    }
  }
}

removeDsStoreFiles(destDir);

console.log(`[sync:app] Synced ${sourceDir} -> ${destDir}`);
