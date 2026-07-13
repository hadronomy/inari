import { readFile, writeFile } from "node:fs/promises";

import { absolutePath, parentPath, type AbsolutePath } from "@inari/release-core";
import { WorkspacePackage } from "tegami";
import { glob } from "tinyglobby";
import { parseDocument, type Document } from "yaml";
import { z } from "zod";

const chartManifestSchema = z.looseObject({
  apiVersion: z.literal("v2", { error: "Only Helm apiVersion v2 charts are supported." }),
  name: z
    .string()
    .regex(
      /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/,
      "A Helm chart name must be a lowercase DNS-compatible name.",
    ),
  version: z.string().trim().min(1, "A Helm chart must have a version."),
});

type ChartManifest = z.infer<typeof chartManifestSchema>;

export class HelmChart extends WorkspacePackage {
  readonly manager = "helm";
  readonly name: string;
  readonly path: AbsolutePath;
  readonly manifestPath: AbsolutePath;
  private readonly document: Document;
  private currentVersion: string;

  private constructor(
    chartPath: AbsolutePath,
    manifestPath: AbsolutePath,
    document: Document,
    manifest: ChartManifest,
  ) {
    super();
    this.path = chartPath;
    this.manifestPath = manifestPath;
    this.document = document;
    this.name = manifest.name;
    this.currentVersion = manifest.version;
  }

  get version(): string {
    return this.currentVersion;
  }

  setVersion(version: string): void {
    this.currentVersion = version;
    this.document.setIn(["version"], version);
  }

  async write(): Promise<void> {
    await writeFile(this.manifestPath, this.document.toString(), "utf8");
  }

  static async load(manifestPath: AbsolutePath): Promise<HelmChart> {
    const source = await readFile(manifestPath, "utf8");
    const document = parseDocument(source);
    if (document.errors.length > 0) {
      throw new Error(`Invalid Helm manifest ${manifestPath}: ${document.errors[0]?.message}`);
    }

    const manifest = chartManifestSchema.parse(document.toJS());
    return new HelmChart(parentPath(manifestPath), manifestPath, document, manifest);
  }
}

export async function discoverHelmCharts(workspaceRoot: AbsolutePath): Promise<HelmChart[]> {
  const manifests = await glob(["deploy/**/Chart.yaml"], {
    absolute: true,
    cwd: workspaceRoot,
    onlyFiles: true,
  });
  return Promise.all(manifests.map((manifest) => HelmChart.load(absolutePath(manifest))));
}
