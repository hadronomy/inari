import { beforeAll, expect, test } from "bun:test";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { absolutePath } from "@inari/release-core";
import initToml from "@rainbowatcher/toml-edit-js";

import { CargoArtifact } from "./private-cargo.ts";

beforeAll(async () => {
  await initToml();
});

test("versions a private Cargo artifact without disturbing its manifest", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "inari-cargo-artifact-"));
  const manifest = absolutePath(path.join(directory, "Cargo.toml"));
  await writeFile(
    manifest,
    [
      "[package]",
      'name = "inari-device-center"',
      'version = "1.20.0-alpha.6" # kept with the package',
      "publish = false",
      "",
    ].join("\n"),
  );

  const pkg = await CargoArtifact.load(manifest);
  pkg.setVersion("1.20.0-alpha.7");
  await pkg.write();

  expect(await readFile(manifest, "utf8")).toBe(
    [
      "[package]",
      'name = "inari-device-center"',
      'version = "1.20.0-alpha.7" # kept with the package',
      "publish = false",
      "",
    ].join("\n"),
  );
});

test("refuses to manage a Cargo package that can be published", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "inari-cargo-artifact-"));
  const manifest = absolutePath(path.join(directory, "Cargo.toml"));
  await writeFile(
    manifest,
    [
      "[package]",
      'name = "inari-device-center"',
      'version = "1.20.0-alpha.6"',
      "publish = true",
      "",
    ].join("\n"),
  );

  await expect(CargoArtifact.load(manifest)).rejects.toThrow();
});
