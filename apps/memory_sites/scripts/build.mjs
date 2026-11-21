import { promises as fs } from "node:fs";
import { build } from "esbuild";

// Deployment identity comes from Sites registration, never the public source.
const file = ".openai/hosting.json";
const current = JSON.parse(await fs.readFile(file,"utf8"));
if (typeof current.project_id !== "string" || !current.project_id ||
    current.d1 !== "DB" || current.static || !current.capabilities?.includes("mcp")) {
  throw new Error("Use the owner-private Site project ID, binding DB, and mcp capability.");
}
await build({entryPoints:["worker.mjs"],bundle:true,format:"esm",platform:"browser",
  outfile:"dist/server/index.js",target:"es2022",logLevel:"warning"});
await fs.mkdir("dist/.openai",{recursive:true});
await fs.copyFile(file,"dist/.openai/hosting.json");
await fs.cp("drizzle","dist/.openai/drizzle",{recursive:true});
