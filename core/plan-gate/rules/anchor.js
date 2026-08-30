import fs from 'node:fs';

export function anchorRule(context) {
  if (context.exemptions.anchor) return { decision: 'abstain', rule: 'anchor' };
  const file = context.anchorFile;
  if (!file) return {
    decision: 'deny',
    rule: 'anchor',
    reason: 'Заблокировано план-гейтом: состояние якоря сессии недоступно.',
  };
  try {
    if (fs.readFileSync(file, 'utf8').trim()) return { decision: 'abstain', rule: 'anchor' };
  } catch { /* absence is the condition this rule checks */ }
  return {
    decision: 'deny',
    rule: 'anchor',
    reason: 'Заблокировано план-гейтом: у сессии нет задачи-якоря. Спроси Влада, какая задача базы будет якорем сессии, и продолжай после ответа.',
  };
}
