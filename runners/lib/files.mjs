// File helpers shared by the runner and the backends.
import { mkdir, rename, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

/** Write a file only the operator can read (login state carries session cookies). */
export async function writePrivate(path, data) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  await writeFile(`${path}.tmp`, data, { mode: 0o600 });
  await rename(`${path}.tmp`, path);
}
