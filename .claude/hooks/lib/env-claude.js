// Пути, которые задаёт Claude Code: корень проекта своей переменной и личный файл
// доступа в своём каталоге настроек. Имена переменной и каталога — устройство
// этого харнеса, поэтому живут здесь; загрузчик `.env` получает их значениями.
import os from 'node:os';
import path from 'node:path';

export function harnessEnvPaths() {
  return {
    root: process.env.CLAUDE_PROJECT_DIR || '',
    personalEnv: path.join(os.homedir(), '.claude', 'craft.env'),
  };
}
