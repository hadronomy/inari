import path from "node:path";

import { z } from "zod";

const absolutePathSchema = z
  .string()
  .refine(path.isAbsolute, "Expected an absolute filesystem path.")
  .brand<"AbsolutePath">();

export type AbsolutePath = z.infer<typeof absolutePathSchema>;

export function absolutePath(value: string): AbsolutePath {
  return absolutePathSchema.parse(path.resolve(value));
}

export function childPath(root: AbsolutePath, ...segments: string[]): AbsolutePath {
  return absolutePath(path.join(root, ...segments));
}

export function parentPath(value: AbsolutePath): AbsolutePath {
  return absolutePath(path.dirname(value));
}
