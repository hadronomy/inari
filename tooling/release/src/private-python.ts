import { PipPackage } from "@tegami/pip";
import type { TegamiPlugin } from "tegami";

export function bundledPython(): TegamiPlugin {
  return {
    name: "bundled-python",
    enforce: "pre",
    publishPreflight({ pkg }) {
      if (pkg instanceof PipPackage) return { shouldPublish: false };
    },
  };
}
