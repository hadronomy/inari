import { x } from "tinyexec";

import type { AbsolutePath } from "@inari/release-core";

export async function run(
  command: string,
  args: string[],
  workspaceRoot: AbsolutePath,
): Promise<string> {
  const result = await x(command, args, { nodeOptions: { cwd: workspaceRoot } });
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
  const result = await x(command, args, { nodeOptions: { cwd: workspaceRoot } });
  return result.exitCode === 0;
}
