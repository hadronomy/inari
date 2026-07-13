import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, test } from "bun:test";
import { absolutePath } from "@inari/release-core";

import type { ReleaseAsset, ReleaseBundle } from "./bundle.ts";
import {
  ReleaseAssets,
  type PublishedAsset,
  type PublishedRelease,
  type ReleaseRepository,
} from "./github.ts";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { force: true, recursive: true })),
  );
});

describe("release asset publication", () => {
  test("replaces an interrupted asset and keeps matching uploads", async () => {
    const directory = await mkdtemp(join(tmpdir(), "inari-msix-"));
    temporaryDirectories.push(directory);
    const bundle = await releaseBundle(directory);
    const remote = new MemoryReleaseRepository([
      remoteAsset(1, bundle.assets[0]!.name, "sha256:stale"),
      remoteAsset(2, bundle.checksumManifest.name, bundle.checksumManifest.digest),
    ]);
    const releases = new ReleaseAssets({ owner: "inari", repo: "inari" }, undefined, remote);

    await releases.upload("edge-v1.20.0-alpha.1", bundle);

    expect(remote.deleted).toEqual([1]);
    expect(remote.uploaded).toEqual([bundle.assets[0]!.name]);
  });
});

class MemoryReleaseRepository implements ReleaseRepository {
  readonly release: PublishedRelease = { id: 42, uploadUrl: "https://uploads.example.test" };
  readonly deleted: number[] = [];
  readonly uploaded: string[] = [];
  private readonly assets: PublishedAsset[];

  constructor(assets: PublishedAsset[]) {
    this.assets = assets;
  }

  async find(): Promise<PublishedRelease> {
    return this.release;
  }

  async list(): Promise<PublishedAsset[]> {
    return this.assets;
  }

  async delete(asset: PublishedAsset): Promise<void> {
    this.deleted.push(asset.id);
  }

  async downloadText(): Promise<string> {
    return "";
  }

  async upload(_release: PublishedRelease, asset: ReleaseAsset): Promise<void> {
    this.uploaded.push(asset.name);
  }
}

async function releaseBundle(directory: string): Promise<ReleaseBundle> {
  const artifactPath = absolutePath(join(directory, "Inari-Device-Center.msix"));
  const checksumsPath = absolutePath(join(directory, "SHA256SUMS"));
  await writeFile(artifactPath, "msix");
  await writeFile(checksumsPath, "checksums");
  return {
    assets: [
      {
        name: "Inari-Device-Center.msix",
        path: artifactPath,
        digest: "sha256:current",
      },
    ],
    checksumManifest: {
      name: "SHA256SUMS",
      path: checksumsPath,
      digest: "sha256:checksums",
    },
  };
}

function remoteAsset(id: number, name: string, digest: string): PublishedAsset {
  return { id, name, digest, downloadUrl: `https://downloads.example.test/${name}` };
}
