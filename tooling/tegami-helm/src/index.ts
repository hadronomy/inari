import { absolutePath } from "@inari/release-core";
import type { TegamiPlugin } from "tegami";

import { discoverHelmCharts, HelmChart } from "./chart.ts";
import { HelmPublisher, type HelmPublisherOptions } from "./publisher.ts";

export type { HelmPublisherOptions } from "./publisher.ts";
export { HelmChart } from "./chart.ts";

export function helm(options: HelmPublisherOptions): TegamiPlugin {
  let publisher: HelmPublisher;

  return {
    name: "inari-helm",
    init() {
      publisher = new HelmPublisher(absolutePath(this.cwd), options);
    },
    async resolve() {
      for (const chart of await discoverHelmCharts(absolutePath(this.cwd))) this.graph.add(chart);
    },
    publishPreflight({ pkg }) {
      if (pkg instanceof HelmChart) return { shouldPublish: true };
    },
    resolvePlanStatus({ plan }) {
      return Array.from(plan.packages, async ([id, packagePlan]) => {
        if (!packagePlan.preflight?.shouldPublish) return;
        const pkg = this.graph.get(id);
        if (!(pkg instanceof HelmChart)) return;
        return publisher.status(pkg);
      });
    },
    async publish({ pkg }) {
      if (!(pkg instanceof HelmChart)) return;
      try {
        await publisher.publish(pkg);
        return { type: "published" };
      } catch (error) {
        return { type: "failed", error: error instanceof Error ? error.message : String(error) };
      }
    },
    async applyDraft(draft) {
      const writes: Promise<void>[] = [];
      for (const pkg of this.graph.getPackages()) {
        if (!(pkg instanceof HelmChart)) continue;
        const version = draft.getPackageDraft(pkg.id)?.bumpVersion(pkg);
        if (!version || version === pkg.version) continue;
        pkg.setVersion(version);
        writes.push(pkg.write());
      }
      await Promise.all(writes);
    },
  };
}
