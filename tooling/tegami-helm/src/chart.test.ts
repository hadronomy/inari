import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { absolutePath } from "@inari/release-core";
import { expect, test } from "bun:test";

import { HelmChart } from "./chart.ts";

test("updates only the chart version while preserving comments and order", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "inari-helm-"));
  const chartDir = path.join(root, "chart");
  await mkdir(chartDir);
  const manifest = path.join(chartDir, "Chart.yaml");
  await writeFile(
    manifest,
    "# kept\napiVersion: v2\nname: inari\ndescription: Private device operations\nversion: 0.2.0\n",
  );

  const chart = await HelmChart.load(absolutePath(manifest));
  chart.setVersion("0.3.0");
  await chart.write();

  expect(await readFile(manifest, "utf8")).toBe(
    "# kept\napiVersion: v2\nname: inari\ndescription: Private device operations\nversion: 0.3.0\n",
  );
});
