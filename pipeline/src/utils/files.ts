import fs from 'node:fs';
import path from 'node:path';
import axios from 'axios';

export async function ensureDir(dir: string): Promise<void> {
  await fs.promises.mkdir(dir, { recursive: true });
}

export function slugify(input: string): string {
  const diacritics = new RegExp('[\\u0300-\\u036f]', 'g');
  const slug = input
    .toLowerCase()
    .normalize('NFKD')
    .replace(diacritics, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-+|-+$)/g, '')
    .slice(0, 60);
  return slug || 'reel';
}

export async function downloadFile(url: string, destPath: string): Promise<void> {
  await ensureDir(path.dirname(destPath));

  const response = await axios.get(url, { responseType: 'stream' });
  const writer = fs.createWriteStream(destPath);

  await new Promise<void>((resolve, reject) => {
    response.data.pipe(writer);
    writer.on('finish', () => resolve());
    writer.on('error', reject);
    response.data.on('error', reject);
  });
}
