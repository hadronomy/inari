import type { TegamiPlugin } from "tegami";

export function requireSuccessfulPublish(): TegamiPlugin {
  return {
    name: "publish-integrity",
    afterPublishAll({ plan }) {
      const failures = Array.from(plan.packages, ([packageId, packagePlan]) => {
        const result = packagePlan.publishResult;
        return result?.type === "failed" ? `${packageId}: ${result.error}` : undefined;
      }).filter((failure) => failure !== undefined);

      if (failures.length === 0) return;
      throw new Error(["Package publication failed:", ...failures].join("\n"));
    },
  };
}
