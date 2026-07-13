import { readFile } from "node:fs/promises";

import { RequestError } from "@octokit/request-error";
import { Octokit } from "@octokit/rest";

import { parseChecksums, type ReleaseAsset, type ReleaseBundle } from "./bundle.ts";

interface RepositoryCoordinates {
  owner: string;
  repo: string;
}

export interface PublishedRelease {
  id: number;
  uploadUrl: string;
}

export interface PublishedAsset {
  id: number;
  name: string;
  downloadUrl: string;
  digest?: string;
}

export interface ReleaseRepository {
  find(tag: string): Promise<PublishedRelease | undefined>;
  list(release: PublishedRelease): Promise<PublishedAsset[]>;
  delete(asset: PublishedAsset): Promise<void>;
  downloadText(asset: PublishedAsset): Promise<string>;
  upload(release: PublishedRelease, asset: ReleaseAsset): Promise<void>;
}

export class ReleaseAssets {
  private readonly repository: ReleaseRepository;

  constructor(
    coordinates: RepositoryCoordinates,
    token = process.env.GITHUB_TOKEN,
    repository: ReleaseRepository = new OctokitReleaseRepository(coordinates, token),
  ) {
    this.repository = repository;
  }

  async status(tag: string, expectedNames: string[]): Promise<"pending" | "success"> {
    const release = await this.repository.find(tag);
    if (!release) return "pending";
    const byName = new Map(
      (await this.repository.list(release)).map((asset) => [asset.name, asset]),
    );
    const checksumAsset = byName.get("SHA256SUMS");
    if (!checksumAsset || expectedNames.some((name) => !byName.has(name))) return "pending";

    const checksums = parseChecksums(await this.repository.downloadText(checksumAsset));
    for (const name of expectedNames) {
      if (name === "SHA256SUMS") continue;
      const asset = byName.get(name);
      if (!asset || checksums.get(name) !== asset.digest) return "pending";
    }
    return "success";
  }

  async upload(tag: string, bundle: ReleaseBundle): Promise<void> {
    const release = await this.repository.find(tag);
    if (!release) throw new Error(`GitHub release ${tag} does not exist.`);
    const existing = new Map(
      (await this.repository.list(release)).map((asset) => [asset.name, asset]),
    );
    await Promise.all(
      [...bundle.assets, bundle.checksumManifest].map(async (asset) => {
        const published = existing.get(asset.name);
        if (published?.digest === asset.digest) return;
        if (published) await this.repository.delete(published);
        await this.repository.upload(release, asset);
      }),
    );
  }
}

class OctokitReleaseRepository implements ReleaseRepository {
  private readonly client: Octokit;
  private readonly coordinates: RepositoryCoordinates;
  private readonly token: string | undefined;

  constructor(coordinates: RepositoryCoordinates, token: string | undefined) {
    this.coordinates = coordinates;
    this.token = token;
    this.client = new Octokit({ auth: token });
  }

  async find(tag: string): Promise<PublishedRelease | undefined> {
    try {
      const response = await this.client.rest.repos.getReleaseByTag({
        ...this.coordinates,
        tag,
      });
      return { id: response.data.id, uploadUrl: response.data.upload_url };
    } catch (error) {
      if (isNotFound(error)) return undefined;
      throw error;
    }
  }

  async list(release: PublishedRelease): Promise<PublishedAsset[]> {
    const response = await this.client.rest.repos.listReleaseAssets({
      ...this.coordinates,
      release_id: release.id,
      per_page: 100,
    });
    return response.data.map((asset) => ({
      id: asset.id,
      name: asset.name,
      downloadUrl: asset.browser_download_url,
      digest: "digest" in asset && asset.digest ? asset.digest : undefined,
    }));
  }

  async delete(asset: PublishedAsset): Promise<void> {
    await this.client.rest.repos.deleteReleaseAsset({
      ...this.coordinates,
      asset_id: asset.id,
    });
  }

  async downloadText(asset: PublishedAsset): Promise<string> {
    const headers = new Headers();
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const response = await fetch(asset.downloadUrl, { headers });
    if (!response.ok) {
      throw new Error(`Unable to download release checksums: ${response.status}.`);
    }
    return response.text();
  }

  async upload(release: PublishedRelease, asset: ReleaseAsset): Promise<void> {
    const uploadUrl = new URL(release.uploadUrl.replace("{?name,label}", ""));
    uploadUrl.searchParams.set("name", asset.name);
    const headers = new Headers({ "content-type": "application/octet-stream" });
    if (this.token) headers.set("authorization", `Bearer ${this.token}`);
    const bytes = await readFile(asset.path);
    const response = await fetch(uploadUrl, {
      method: "POST",
      headers,
      body: new Blob([new Uint8Array(bytes)]),
    });
    if (!response.ok) throw new Error(`Unable to upload ${asset.name}: ${response.status}.`);
  }
}

function isNotFound(error: unknown): boolean {
  return error instanceof RequestError && error.status === 404;
}
