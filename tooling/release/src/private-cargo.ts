import { readFile, writeFile } from "node:fs/promises";

import { absolutePath, childPath, parentPath, type AbsolutePath } from "@inari/release-core";
import initToml, { edit, parse } from "@rainbowatcher/toml-edit-js";
import { WorkspacePackage, type TegamiPlugin } from "tegami";
import { x } from "tinyexec";
import { z } from "zod";

const manifestSchema = z.looseObject({
  package: z.looseObject({
    name: z.string().trim().min(1),
    version: z.string().trim().min(1),
    publish: z.literal(false),
  }),
});

export class CargoArtifact extends WorkspacePackage {
  readonly manager = "cargo";
  readonly path: AbsolutePath;
  readonly name: string;
  private readonly manifestPath: AbsolutePath;
  private content: string;
  private currentVersion: string;

  private constructor(manifestPath: AbsolutePath, content: string, name: string, version: string) {
    super();
    this.manifestPath = manifestPath;
    this.path = parentPath(manifestPath);
    this.content = content;
    this.name = name;
    this.currentVersion = version;
  }

  get version(): string {
    return this.currentVersion;
  }

  setVersion(version: string): void {
    this.currentVersion = version;
    this.content = edit(this.content, "package.version", version);
  }

  async write(): Promise<void> {
    await writeFile(this.manifestPath, `${this.content.trimEnd()}\n`, "utf8");
  }

  static async load(manifestPath: AbsolutePath): Promise<CargoArtifact> {
    const content = await readFile(manifestPath, "utf8");
    const manifest = manifestSchema.parse(parse(content));
    return new CargoArtifact(
      manifestPath,
      content,
      manifest.package.name,
      manifest.package.version,
    );
  }
}

export function bundledCargo(manifests: readonly string[]): TegamiPlugin {
  let packages: CargoArtifact[] = [];
  let workspaceRoot: AbsolutePath;

  return {
    name: "bundled-cargo",
    enforce: "pre",
    async init() {
      await initToml();
    },
    async resolve() {
      workspaceRoot = absolutePath(this.cwd);
      packages = await Promise.all(
        manifests.map((manifest) => CargoArtifact.load(childPath(workspaceRoot, manifest))),
      );
      for (const pkg of packages) this.graph.add(pkg);
    },
    publishPreflight({ pkg }) {
      if (pkg instanceof CargoArtifact) return { shouldPublish: false };
    },
    async applyDraft(draft) {
      const writes: Promise<void>[] = [];
      for (const pkg of packages) {
        const version = draft.getPackageDraft(pkg.id)?.bumpVersion(pkg);
        if (!version || version === pkg.version) continue;
        pkg.setVersion(version);
        writes.push(pkg.write());
      }
      await Promise.all(writes);
    },
    async applyCliDraft() {
      const result = await x("cargo", ["update", "--workspace"], {
        nodeOptions: { cwd: workspaceRoot },
      });
      if (result.exitCode !== 0) {
        throw new Error(
          `Cargo could not refresh the workspace lockfile.\n${result.stderr || result.stdout}`,
        );
      }
    },
  };
}
