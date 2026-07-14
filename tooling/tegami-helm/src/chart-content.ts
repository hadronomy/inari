import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, rm } from "node:fs/promises";
import { join, relative, sep } from "node:path";

import type { AbsolutePath } from "@inari/release-core";
import { x as extract } from "tar";

export async function chartContentDigest(
  archive: AbsolutePath,
  extractionRoot: AbsolutePath,
): Promise<string> {
  await rm(extractionRoot, { recursive: true, force: true });
  await mkdir(extractionRoot, { recursive: true });
  await extract({ cwd: extractionRoot, file: archive, strict: true });

  const files = await listFiles(extractionRoot);
  const entries = await Promise.all(
    files.map(async (file) => ({
      path: relative(extractionRoot, file).split(sep).join("/"),
      content: createHash("sha256")
        .update(await readFile(file))
        .digest("hex"),
    })),
  );
  const digest = createHash("sha256");
  for (const { path, content } of entries) {
    digest.update(`file\0${path}\0${content}\n`);
  }
  return `sha256:${digest.digest("hex")}`;
}

async function listFiles(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name));

  const files = await Promise.all(
    entries.map(async (entry): Promise<string[]> => {
      const path = join(root, entry.name);
      if (entry.isDirectory()) return listFiles(path);
      if (entry.isFile()) return [path];
      throw new Error(`Helm chart archive contains an unsupported entry: ${path}`);
    }),
  );
  return files.flat();
}
