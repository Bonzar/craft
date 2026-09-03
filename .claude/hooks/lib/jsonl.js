// Чтение JSONL: по объекту на строку.
//
// eachJsonl(текст, fn) → fn(объект, строка) на каждую разобранную строку.
//
// Битая строка ПРОПУСКАЕТСЯ, а не роняет разбор: этим форматом лежат журнал
// метрик, реестр одобренного и транскрипт харнесса, и в каждом из них потерять
// одну строку дешевле, чем потерять файл целиком — оборванная запись бывает
// нормой, когда файл читают в момент дописывания.
export function eachJsonl(text, fn) {
  for (const line of String(text).split('\n')) {
    if (!line.trim()) continue;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    if (entry && typeof entry === 'object') fn(entry, line);
  }
}
