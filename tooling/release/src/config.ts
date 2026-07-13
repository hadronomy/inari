import { helm } from "@inari/tegami-helm";
import { msix } from "@inari/tegami-msix";
import { pip } from "@tegami/pip";
import { tegami } from "tegami";
import { github } from "tegami/plugins/github";

import { bundledPython } from "./private-python.ts";

export const release = tegami<"edge" | "controller-chart">({
  conventionalCommits: true,
  ignore: [/^npm:/],
  groups: {
    edge: { prerelease: "alpha", syncBump: true, syncGitTag: true },
    "controller-chart": { syncGitTag: true },
  },
  packages: {
    "pip:inari": { group: "edge" },
    "pip:inari-tray": { group: "edge" },
    "pip:inari-brand": { group: "edge" },
    "msix:inari-device-center": { group: "edge" },
    "helm:inari": { group: "controller-chart" },
  },
  plugins: [
    bundledPython(),
    pip(),
    helm({
      registry: "ghcr.io",
      repository: "hadronomy/charts",
      certificateIdentity:
        "^https://github.com/hadronomy/inari/.github/workflows/release\\.yaml@refs/heads/main$",
      certificateIssuer: "https://token.actions.githubusercontent.com",
    }),
    github({ repo: "hadronomy/inari" }),
    msix({ repository: { owner: "hadronomy", repo: "inari" } }),
  ],
});
