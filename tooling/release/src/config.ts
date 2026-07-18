import { helm } from "@inari/tegami-helm";
import { msix } from "@inari/tegami-msix";
import { pip } from "@tegami/pip";
import { tegami } from "tegami";
import { github } from "tegami/plugins/github";

import { bundledCargo } from "./private-cargo.ts";
import { bundledPython } from "./private-python.ts";
import { requireSuccessfulPublish } from "./publish-integrity.ts";

export const release = tegami<"edge" | "controller-chart">({
  conventionalCommits: true,
  ignore: [/^npm:/],
  groups: {
    edge: { prerelease: "alpha", syncBump: true, syncGitTag: true },
    "controller-chart": { syncGitTag: true },
  },
  packages: {
    "pip:inari": { group: "edge" },
    "pip:inari-brand": { group: "edge" },
    "cargo:inari-agent-client": { group: "edge" },
    "cargo:inari-device-center": { group: "edge" },
    "msix:inari-device-center": { group: "edge" },
    "helm:inari": { group: "controller-chart" },
  },
  plugins: [
    bundledPython(),
    pip(),
    bundledCargo(["crates/inari-agent-client/Cargo.toml", "crates/inari-device-center/Cargo.toml"]),
    helm({
      registry: "ghcr.io",
      repository: "hadronomy/charts",
      certificateIdentity:
        "^https://github.com/hadronomy/inari/.github/workflows/release\\.yaml@refs/heads/main$",
      certificateIssuer: "https://token.actions.githubusercontent.com",
    }),
    requireSuccessfulPublish(),
    github({ repo: "hadronomy/inari" }),
    msix({ repository: { owner: "hadronomy", repo: "inari" } }),
  ],
});
