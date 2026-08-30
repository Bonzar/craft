import fs from 'node:fs';

const DESKTOP_BUNDLE = '/Applications/ChatGPT.app/Contents/Resources/codex';

export function resolveCodexCommand(
  env = process.env,
  exists = fs.existsSync,
  platform = process.platform,
) {
  if (env.CRAFT_CODEX_CMD) return env.CRAFT_CODEX_CMD;
  if (platform === 'darwin' && exists(DESKTOP_BUNDLE)) return DESKTOP_BUNDLE;
  return 'codex';
}
