import { createHash } from "node:crypto";
import { mkdir, readFile, rm } from "node:fs/promises";

import { childPath, type AbsolutePath } from "@inari/release-core";
import { type HelmChart } from "./chart.ts";
import { run, succeeds } from "./process.ts";
import { type OciArtifact, type OciReference, resolveOciArtifact } from "./registry.ts";

export interface HelmPublisherOptions {
  registry: string;
  repository: string;
  certificateIdentity: string;
  certificateIssuer: string;
}

interface PackagedChart {
  archive: AbsolutePath;
  digest: string;
}

export class HelmPublisher {
  private readonly workspaceRoot: AbsolutePath;
  private readonly options: HelmPublisherOptions;

  constructor(workspaceRoot: AbsolutePath, options: HelmPublisherOptions) {
    this.workspaceRoot = workspaceRoot;
    this.options = options;
  }

  async status(chart: HelmChart): Promise<"pending" | "success"> {
    const artifact = await resolveOciArtifact(this.reference(chart));
    if (!artifact) return "pending";

    const chartExists = await succeeds(
      "helm",
      ["show", "chart", this.ociUrl(chart), "--version", chart.version],
      this.workspaceRoot,
    );
    if (!chartExists) return "pending";

    const signatureValid = await succeeds(
      "cosign",
      this.verifyArgs(this.immutableReference(chart, artifact)),
      this.workspaceRoot,
    );
    return signatureValid ? "success" : "pending";
  }

  async publish(chart: HelmChart): Promise<void> {
    const packaged = await this.package(chart);
    let artifact = await resolveOciArtifact(this.reference(chart));

    if (!artifact) {
      await run("helm", ["push", packaged.archive, this.ociUrl()], this.workspaceRoot);
      artifact = await resolveOciArtifact(this.reference(chart));
      if (!artifact) {
        throw new Error("The pushed chart could not be resolved to an immutable digest.");
      }
    }

    this.assertExpectedLayer(chart, artifact, packaged);
    const immutable = this.immutableReference(chart, artifact);
    if (!(await succeeds("cosign", this.verifyArgs(immutable), this.workspaceRoot))) {
      await run("cosign", ["sign", "--yes", immutable], this.workspaceRoot);
    }

    if ((await this.status(chart)) !== "success") {
      throw new Error(
        `Published Helm chart ${chart.name}@${chart.version} did not pass verification.`,
      );
    }
  }

  private async package(chart: HelmChart): Promise<PackagedChart> {
    const staging = childPath(
      this.workspaceRoot,
      "target",
      "release",
      "helm",
      `${chart.name}-${chart.version}`,
    );
    await rm(staging, { recursive: true, force: true });
    await mkdir(staging, { recursive: true });
    await run(
      "helm",
      ["package", chart.path, "--destination", staging, "--version", chart.version],
      this.workspaceRoot,
    );
    const archivePath = childPath(staging, `${chart.name}-${chart.version}.tgz`);
    const digest = `sha256:${createHash("sha256")
      .update(await readFile(archivePath))
      .digest("hex")}`;
    return { archive: archivePath, digest };
  }

  private assertExpectedLayer(
    chart: HelmChart,
    artifact: OciArtifact,
    packaged: PackagedChart,
  ): void {
    if (artifact.chartLayerDigest !== packaged.digest) {
      throw new Error(
        `OCI tag ${chart.version} already points to chart content that does not match this release.`,
      );
    }
  }

  private reference(chart: HelmChart): OciReference {
    return {
      registry: this.options.registry,
      repository: `${this.options.repository}/${chart.name}`,
      tag: chart.version,
    };
  }

  private image(chart: HelmChart): string {
    return `${this.options.registry}/${this.options.repository}/${chart.name}`;
  }

  private immutableReference(chart: HelmChart, artifact: OciArtifact): string {
    return `${this.image(chart)}@${artifact.digest}`;
  }

  private ociUrl(chart?: HelmChart): string {
    return `oci://${this.options.registry}/${this.options.repository}${chart ? `/${chart.name}` : ""}`;
  }

  private verifyArgs(reference: string): string[] {
    return [
      "verify",
      "--certificate-identity-regexp",
      this.options.certificateIdentity,
      "--certificate-oidc-issuer",
      this.options.certificateIssuer,
      reference,
    ];
  }
}
