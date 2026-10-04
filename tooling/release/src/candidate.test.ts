import { expect, test } from "bun:test";

import { PackageGraph, WorkspacePackage } from "tegami";

import { candidateVersion, configureWindowsCandidate, setCandidateSequence } from "./candidate.ts";

class CandidatePackage extends WorkspacePackage {
  readonly path = process.cwd();
  readonly name: string;
  readonly manager: string;
  readonly version: string | undefined;

  constructor(name: string, manager: string, version: string | undefined) {
    super();
    this.name = name;
    this.manager = manager;
    this.version = version;
  }
}

test("advances alpha candidates without changing the release base", () => {
  expect(candidateVersion("1.20.0-alpha.12", "13")).toBe("1.20.0-alpha.13");
  expect(candidateVersion("1.20.0-alpha.12", "12")).toBe("1.20.0-alpha.12");
});

test("keeps independent stable packages and packages without versions", () => {
  expect(candidateVersion("0.3.2", "13")).toBe("0.3.2");
  expect(candidateVersion(undefined, "13")).toBeUndefined();
});

test("rejects an older sequence or an invalid MSIX prerelease sequence", () => {
  for (const sequence of ["11", "-1", "1.5", "1000", "alpha.13", ""]) {
    expect(() => candidateVersion("1.20.0-alpha.12", sequence)).toThrow();
  }
});

test("preserves the receiver of Tegami's real version method", () => {
  const pkg = new CandidatePackage("candidate_fixture", "fixture", "1.20.0-alpha.11");
  const draft = pkg.initDraft();
  draft.type = "patch";
  draft.prerelease = "alpha";
  setCandidateSequence(draft, "13");
  expect(draft.bumpVersion(pkg)).toBe("1.20.0-alpha.13");
});

test("rejects chart-only drafts before a sequence can manufacture a Windows bump", () => {
  const windows = new CandidatePackage("inari-device-center", "msix", "1.20.0-alpha.11");
  const chart = new CandidatePackage("inari", "helm", "0.3.2");
  const graph = new PackageGraph([windows, chart]);
  const windowsDraft = windows.initDraft();
  windowsDraft.prerelease = "alpha";
  const chartDraft = chart.initDraft();
  chartDraft.type = "patch";
  for (const sequence of ["", "15"]) {
    expect(() =>
      configureWindowsCandidate(
        graph,
        new Map([
          [windows.id, windowsDraft],
          [chart.id, chartDraft],
        ]),
        sequence,
      ),
    ).toThrow("pending Device Center version change");
  }
  expect(windowsDraft.bumpVersion(windows)).toBe(windows.version);
  expect(chartDraft.bumpVersion(chart)).toBe("0.3.3");
});

test("rejects missing Windows packages, drafts, and versions", () => {
  const windows = new CandidatePackage("inari-device-center", "msix", "1.20.0-alpha.11");
  const unversioned = new CandidatePackage("inari-device-center", "msix", undefined);
  for (const pkg of [windows, unversioned]) {
    const graph = new PackageGraph([pkg]);
    expect(() => configureWindowsCandidate(graph, new Map(), "15")).toThrow();
    expect(() =>
      configureWindowsCandidate(new PackageGraph(), new Map([[pkg.id, pkg.initDraft()]]), "15"),
    ).toThrow();
  }
  expect(() =>
    configureWindowsCandidate(
      new PackageGraph([unversioned]),
      new Map([[unversioned.id, unversioned.initDraft()]]),
      "15",
    ),
  ).toThrow();
});

test("advances a real Windows draft and preserves the independent chart version", () => {
  const windows = new CandidatePackage("inari-device-center", "msix", "1.20.0-alpha.11");
  const chart = new CandidatePackage("inari", "helm", "0.3.2");
  const windowsDraft = windows.initDraft();
  windowsDraft.type = "patch";
  windowsDraft.prerelease = "alpha";
  const chartDraft = chart.initDraft();
  chartDraft.type = "patch";
  configureWindowsCandidate(
    new PackageGraph([windows, chart]),
    new Map([
      [windows.id, windowsDraft],
      [chart.id, chartDraft],
    ]),
    "15",
  );
  expect(windowsDraft.bumpVersion(windows)).toBe("1.20.0-alpha.15");
  expect(chartDraft.bumpVersion(chart)).toBe("0.3.3");
});
