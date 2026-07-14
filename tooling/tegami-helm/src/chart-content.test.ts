import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { absolutePath } from "@inari/release-core";
import { afterEach, expect, test } from "bun:test";
import { c as createArchive } from "tar";

import { chartContentDigest } from "./chart-content.ts";

const workspaces: string[] = [];

afterEach(async () => {
  await Promise.all(workspaces.splice(0).map((path) => rm(path, { recursive: true })));
});

test("compares chart files rather than archive metadata", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "inari-helm-content-"));
  workspaces.push(workspace);
  const chart = join(workspace, "inari");
  await mkdir(join(chart, "templates"), { recursive: true });
  await writeFile(join(chart, "Chart.yaml"), "apiVersion: v2\nname: inari\nversion: 0.2.1\n");
  await writeFile(join(chart, "templates", "service.yaml"), "kind: Service\n");

  const first = absolutePath(join(workspace, "first.tgz"));
  const second = absolutePath(join(workspace, "second.tgz"));
  await createArchive(
    { cwd: workspace, file: first, gzip: true, mtime: new Date("2026-07-13T00:00:00Z") },
    ["inari"],
  );
  await createArchive(
    { cwd: workspace, file: second, gzip: true, mtime: new Date("2026-07-14T00:00:00Z") },
    ["inari"],
  );

  expect(rawDigest(await readFile(first))).not.toBe(rawDigest(await readFile(second)));
  await expect(
    chartContentDigest(first, absolutePath(join(workspace, "first-content"))),
  ).resolves.toBe(
    await chartContentDigest(second, absolutePath(join(workspace, "second-content"))),
  );
});

function rawDigest(content: Buffer): string {
  return createHash("sha256").update(content).digest("hex");
}
