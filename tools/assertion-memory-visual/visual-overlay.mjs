import {readFile} from 'node:fs/promises';
const resources = await Promise.all(['admin-panel.html','admin-panel.js','admin-panel.css'].map(name => readFile(new URL(name, import.meta.url),'utf8')));
function once(input, from, to) {
  if (input.split(from).length !== 2) throw Error('Pinned viewer integration changed: ' + from.slice(0,60));
  return input.replace(from, () => to);
}
export function patchVisual({html,script,css,releaseTag=null}) {
  const [markup,admin,style] = resources;
  // Keep the published viewer's existing connections renderer and worker.
  // The live bundle's initializer reads graphMode from the URL and creates a
  // layout switch referenced by its startup code. Force the initial mode and
  // leave an inert compatibility element with no layout-change handler.
  script = once(script, 'graphMode=e(new URLSearchParams(location.search));', 'graphMode=`connections`;');
  script = once(script, 'let e=Z(this.graphMode===`connections`?`⬡ Обзоры`:`⬡ Связи`,()=>this.run(()=>this.toggleGraphMode()));e.id=`layout-toggle`,', 'let e=Z(`Связи`,()=>{});e.hidden=true;e.disabled=true;e.id=`layout-toggle`,');
  html = once(html, 'id="topics-toggle" aria-label=', 'id="topics-toggle" hidden aria-label=');
  const panel = markup.replace('<!--VISUAL_RELEASE-->', releaseTag === null ? '' :
    '<p class="memory-admin-version">Визуал: '+releaseTag+'</p>');
  html = once(html, '</body>', panel + '\n</body>');
  return {html,script:script + '\n' + admin,css:css + '\n' + style};
}
