export default {
  frontMatter: "^---\\s*\\r?\\n[^]*?\\r?\\n---\\s*(\\r?\\n|$)",
  config: {
    default: true,
    MD013: false,
    MD024: { siblings_only: true },
    MD033: false,
    MD041: false,
  },
  globs: [
    "README.md",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    ".github/CONTRIBUTING.md",
    "docs/*.md",
    "deploy/helm/inari/README.md",
    "deploy/kustomize/inari/README.md",
    "packages/agent/README.md",
    "packages/agent_tray/README.md",
  ],
};
