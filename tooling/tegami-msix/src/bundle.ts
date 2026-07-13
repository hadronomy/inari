import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { childPath, type AbsolutePath } from "@inari/release-core";
import type { MsixPackage } from "./package.ts";

export interface ReleaseAsset {
  name: string;
  path: AbsolutePath;
  digest: string;
}

export interface ReleaseBundle {
  assets: ReleaseAsset[];
  checksumManifest: ReleaseAsset;
}

export function artifactNames(pkg: Pick<MsixPackage, "version">): string[] {
  const base = `Inari-Device-Center_${pkg.version}_x64`;
  return [
    `${base}.msix`,
    `${base}.spdx.json`,
    "hadronomy-code-signing-root.cer",
    "hadronomy-code-signing-root-fingerprint.txt",
    "inari-code-signing-issuer.cer",
    "inari-code-signing-issuer-fingerprint.txt",
  ];
}

export async function loadReleaseBundle(
  workspaceRoot: AbsolutePath,
  pkg: MsixPackage,
): Promise<ReleaseBundle> {
  const directory = childPath(workspaceRoot, "target", "release", "windows", pkg.version);
  const assets = await Promise.all(
    artifactNames(pkg).map(async (name): Promise<ReleaseAsset> => {
      const assetPath = childPath(directory, name);
      return { name, path: assetPath, digest: await digestFile(assetPath) };
    }),
  );
  const checksumManifest = {
    name: "SHA256SUMS",
    path: childPath(directory, "SHA256SUMS"),
    digest: "",
  };
  const checksumSource = await readFile(checksumManifest.path, "utf8");
  const checksums = parseChecksums(checksumSource);
  for (const asset of assets) {
    if (checksums.get(asset.name) !== asset.digest) {
      throw new Error(`SHA256SUMS does not match ${asset.name}.`);
    }
  }
  checksumManifest.digest = sha256(Buffer.from(checksumSource));
  return { assets, checksumManifest };
}

export function parseChecksums(source: string): Map<string, string> {
  const entries = new Map<string, string>();
  for (const line of source.split("\n")) {
    if (!line.trim()) continue;
    const match = /^([a-f0-9]{64})  ([^/]+)$/.exec(line);
    if (!match) throw new Error("SHA256SUMS contains an invalid entry.");
    entries.set(match[2]!, `sha256:${match[1]}`);
  }
  return entries;
}

async function digestFile(file: AbsolutePath): Promise<string> {
  return sha256(await readFile(file));
}

function sha256(value: Buffer): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
