import { x } from "tinyexec";

import type { AbsolutePath } from "@inari/release-core";

const COMMAND_TIMEOUT_MS = 120_000;
const PROBE_TIMEOUT_MS = 30_000;

export async function run(
  command: string,
  args: string[],
  workspaceRoot: AbsolutePath,
): Promise<string> {
  const invocation = x(command, args, {
    timeout: COMMAND_TIMEOUT_MS,
    nodeOptions: { cwd: workspaceRoot },
  });
  const result = await invocation;
  if (invocation.killed) throw new Error(`${command} timed out.`);
  if (result.exitCode !== 0) {
    const detail = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    throw new Error(`${command} failed${detail ? `:\n${detail}` : "."}`);
  }
  return [result.stdout, result.stderr].filter(Boolean).join("\n");
}

export async function succeeds(
  command: string,
  args: string[],
  workspaceRoot: AbsolutePath,
): Promise<boolean> {
  const invocation = x(command, args, {
    timeout: PROBE_TIMEOUT_MS,
    nodeOptions: { cwd: workspaceRoot },
  });
  const result = await invocation;
  if (invocation.killed) throw new Error(`${command} verification timed out.`);
  return result.exitCode === 0;
}
