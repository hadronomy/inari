import { absolutePath, type AbsolutePath } from "@inari/release-core";
import type { TegamiPlugin } from "tegami";

import { artifactNames, loadReleaseBundle } from "./bundle.ts";
import { ReleaseAssets } from "./github.ts";
import { discoverMsixPackages, MsixPackage } from "./package.ts";

export { MsixPackage } from "./package.ts";
export { toMsixVersion } from "./version.ts";

export interface MsixPluginOptions {
  repository: { owner: string; repo: string };
}

export function msix(options: MsixPluginOptions): TegamiPlugin {
  let releases: ReleaseAssets;
  let workspaceRoot: AbsolutePath;

  return {
    name: "inari-msix",
    enforce: "post",
    init() {
      workspaceRoot = absolutePath(this.cwd);
      releases = new ReleaseAssets(options.repository);
    },
    async resolve() {
      for (const pkg of await discoverMsixPackages(workspaceRoot)) this.graph.add(pkg);
    },
    publishPreflight({ pkg }) {
      if (pkg instanceof MsixPackage) return { shouldPublish: true };
    },
    resolvePlanStatus({ plan }) {
      return Array.from(plan.packages, async ([id, packagePlan]) => {
        if (!packagePlan.preflight?.shouldPublish) return;
        const pkg = this.graph.get(id);
        if (!(pkg instanceof MsixPackage)) return;
        const tag = packagePlan.git?.tag;
        if (!tag) return "pending";
        return releases.status(tag, [...artifactNames(pkg), "SHA256SUMS"]);
      });
    },
    async publish({ pkg }) {
      if (!(pkg instanceof MsixPackage)) return;
      try {
        await loadReleaseBundle(workspaceRoot, pkg);
        return { type: "published" };
      } catch (error) {
        return { type: "failed", error: error instanceof Error ? error.message : String(error) };
      }
    },
    async afterPublishAll({ plan }) {
      await Promise.all(
        Array.from(plan.packages, async ([id, packagePlan]) => {
          if (!packagePlan.preflight?.shouldPublish || packagePlan.publishResult?.type === "failed")
            return;
          const pkg = this.graph.get(id);
          if (!(pkg instanceof MsixPackage)) return;
          const tag = packagePlan.git?.tag;
          if (!tag) throw new Error(`Tegami did not assign a Git tag to ${pkg.id}.`);
          await releases.upload(tag, await loadReleaseBundle(workspaceRoot, pkg));
        }),
      );
    },
    async applyDraft(draft) {
      const writes: Promise<void>[] = [];
      for (const pkg of this.graph.getPackages()) {
        if (!(pkg instanceof MsixPackage)) continue;
        const version = draft.getPackageDraft(pkg.id)?.bumpVersion(pkg);
        if (!version || version === pkg.version) continue;
        pkg.setVersion(version);
        writes.push(pkg.write());
      }
      await Promise.all(writes);
    },
  };
}
