import { readFile, writeFile } from "node:fs/promises";

import { absolutePath, parentPath, type AbsolutePath } from "@inari/release-core";
import initToml, { edit, parse } from "@rainbowatcher/toml-edit-js";
import { WorkspacePackage } from "tegami";
import { glob } from "tinyglobby";
import { z } from "zod";

import { toMsixVersion } from "./version.ts";

const msixManifestSchema = z.looseObject({
  package: z.looseObject({
    name: z.string().trim().min(1),
    version: z.string().trim().min(1),
    msix_version: z.string().regex(/^\d{1,5}(?:\.\d{1,5}){3}$/),
    identity_name: z.string().trim().min(1),
    publisher: z.string().trim().min(1),
    display_name: z.string().trim().min(1),
    publisher_display_name: z.string().trim().min(1),
    service_name: z.string().trim().min(1),
    minimum_windows_version: z.string().regex(/^\d+(?:\.\d+){3}$/),
    architecture: z.literal("x64", { error: "Only Windows x64 MSIX packages are supported." }),
  }),
});

export type MsixManifest = z.infer<typeof msixManifestSchema>;

export class MsixPackage extends WorkspacePackage {
  readonly manager = "msix";
  readonly name: string;
  readonly path: AbsolutePath;
  readonly manifestPath: AbsolutePath;
  readonly manifest: MsixManifest;
  private content: string;
  private currentVersion: string;

  private constructor(
    packagePath: AbsolutePath,
    manifestPath: AbsolutePath,
    manifest: MsixManifest,
    content: string,
  ) {
    super();
    this.path = packagePath;
    this.manifestPath = manifestPath;
    this.manifest = manifest;
    this.content = content;
    this.name = manifest.package.name;
    this.currentVersion = manifest.package.version;
  }

  get version(): string {
    return this.currentVersion;
  }

  setVersion(version: string): void {
    this.currentVersion = version;
    this.manifest.package.version = version;
    this.manifest.package.msix_version = toMsixVersion(version);
    this.content = edit(this.content, "package.version", version);
    this.content = edit(this.content, "package.msix_version", this.manifest.package.msix_version);
  }

  async write(): Promise<void> {
    await writeFile(this.manifestPath, `${this.content.trimEnd()}\n`, "utf8");
  }

  static async load(manifestPath: AbsolutePath): Promise<MsixPackage> {
    await initToml();
    const content = await readFile(manifestPath, "utf8");
    return new MsixPackage(
      parentPath(manifestPath),
      manifestPath,
      msixManifestSchema.parse(parse(content)),
      content,
    );
  }
}

export async function discoverMsixPackages(workspaceRoot: AbsolutePath): Promise<MsixPackage[]> {
  const manifests = await glob(["deploy/windows/package.toml"], {
    absolute: true,
    cwd: workspaceRoot,
    onlyFiles: true,
  });
  return Promise.all(manifests.map((manifest) => MsixPackage.load(absolutePath(manifest))));
}
