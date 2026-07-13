import { readFile } from "node:fs/promises";

import { parse } from "yaml";
import { z } from "zod";

const packageEntrySchema = z.looseObject({
  id: z.string(),
  updated: z.boolean(),
});

const publishLockSchema = z.looseObject({
  "core:packages": z.array(packageEntrySchema),
});

const edgePackages = new Set([
  "pip:inari",
  "pip:inari-tray",
  "pip:inari-brand",
  "msix:inari-device-center",
]);

export interface ReleaseTargets {
  edge: boolean;
  controllerChart: boolean;
}

function includesUpdatedPackage(updated: ReadonlySet<string>): boolean {
  for (const packageId of edgePackages) {
    if (updated.has(packageId)) return true;
  }

  return false;
}

export async function readReleaseTargets(lockPath: URL): Promise<ReleaseTargets> {
  const lock = publishLockSchema.parse(parse(await readFile(lockPath, "utf8")));
  const updated = new Set(
    lock["core:packages"].filter((entry) => entry.updated).map((entry) => entry.id),
  );
  return {
    edge: includesUpdatedPackage(updated),
    controllerChart: updated.has("helm:inari"),
  };
}
