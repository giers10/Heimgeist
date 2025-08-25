export function markdownToHTML(text) {
  // 0) Remove <think>...</think>/<thinking>...</thinking> blocks
  // This regex will match an an opening <think> or <thinking> tag,
  // followed by any characters (non-greedy), until either a closing
  // </think> or </thinking> tag is found, OR the end of the string ($).
  text = text.replace(/<think(?:ing)?>[\s\S]*?(?:<\/think(?:ing)?>|$)/gi, '');

  // 1) Normalize line endings
  let tmp = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // 2) Extract code blocks and replace with placeholders
  const codeblocks = [];
  const placeholder = idx => `@@CODEBLOCK${idx}@@`;
  tmp = tmp.replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    codeblocks.push({ lang: (lang || '').trim(), code });
    return placeholder(codeblocks.length - 1);
  });

  // 3) HTML-escape special characters
  let escaped = tmp
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">");

  // 4) Headings
  escaped = escaped
    .replace(/^#### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm,  "<h2>$1</h2>")
    .replace(/^# (.+)$/gm,   "<h1>$1</h1>");

  // 4.2) Remove blank empty lines immediately after headings
  escaped = escaped.replace(
    /(<h[1-4]>.*?<\/h[1-4]>)[ \t]*\n(?:[ \t]*\n)+/g,
    "$1\n"
  );

  // 4.3) Blockquotes
  escaped = escaped.replace(
    /(^|\n)([ \t]*> .+(?:\n[ \t]*> .+)*)/g,
    (_, lead, blockquoteBlock) => {
      const lines = blockquoteBlock
        .split(/\n/)
        .map(line => line.replace(/^[ \t]*>\s*/, '').trim())
        .join('\n');
      return `${lead}<blockquote>${lines}</blockquote>`;
    }
  );

  // 4.5) Unordered lists
  escaped = escaped.replace(
    /(^|\n)([ \t]*[-*] .+(?:\n[ \t]*[-*] .+)*)/g,
    (_, lead, listBlock) => {
      const items = listBlock
        .split(/\n/)
        .map(line => line.replace(/^[ \t]*[-*]\s+/, '').trim())
        .map(item => `<li>${item}</li>`)
        .join('');
      return `${lead}<ul>${items}</ul>`;
    }
  );

  // 4.6) Markdown tables (GitHub-style). Strict: requires header, separator, ≥2 cols.
const mdTableBlockRe =
  /(^\|[^\n]*\|?\s*\n\|\s*[:\-]+(?:\s*\|\s*[:\-]+)+\s*\|?\s*\n(?:\|[^\n]*\|?\s*(?:\n|$))*)/gm;

escaped = escaped.replace(mdTableBlockRe, (block) => {
  const hadTrailingNewline = /\n$/.test(block);
  const lines = block.replace(/\n$/, '').split('\n');

  const split = (line) => line.replace(/^\||\|$/g, '').split('|').map(s => s.trim());

  const headers = split(lines[0]);
  const seps    = split(lines[1]);
  if (headers.length < 2 || seps.length < 2) return block;
  if (!seps.every(s => /^[ :\-]+$/.test(s) && /-/.test(s))) return block;

  const aligns = seps.map(seg => {
    const s = seg.replace(/\s+/g,'');
    const left = s.startsWith(':');
    const right = s.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return 'left';
  });

  const bodyLines = lines.slice(2).filter(l => /^\|/.test(l.trim()));
  const cellStyle = (i) =>
    ` style="text-align:${aligns[i] || 'left'};vertical-align:top;padding:.6rem .75rem"`;

  const ths = headers.map((h,i)=>`<th${cellStyle(i)}>${h}</th>`).join('');
  const rows = bodyLines.map(line => {
    const cells = split(line);
    const tds = cells.map((c,i)=>`<td${cellStyle(i)}>${c}</td>`).join('');
    return `<tr>${tds}</tr>`;
  }).join('');

  const table = `<table class="nice" style="border-collapse:separate;border-spacing:0;width:100%;margin:1rem 0"><thead><tr>${ths}</tr></thead><tbody>${rows}</tbody></table>`;

  return table + (hadTrailingNewline ? '\n' : '');
});

  // 4.75) Horizontal rules
  escaped = escaped.replace(/^---\s*$/gm, "<hr>");

  // 5) Bold, italic, inline code
  let html = escaped
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(?<!\*)\*(.+?)\*(?!\*)/g, "<i>$1</i>")
    .replace(/`(.+?)`/g, "<code>$1</code>");

  // 5.5) Links
  html = html.replace(/\[([^\]]+?)\]\(([^)]+?)\)/g, '<a href="$2" target="_blank"><span>$1</span> <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" class="feather feather-external-link"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg><span class="tooltip">$2</span></a>');

  // 6) Restore code blocks with title bar (language)
  html = html.replace(/@@CODEBLOCK(\d+)@@/g, (_, idx) => {
    const { lang, code } = codeblocks[+idx];
    const title = (lang && lang.trim()) ? lang.trim() : 'code';
    const escapedCode = code.replace(/</g, "<").replace(/>/g, ">");
    const head = `<div class="md-code-head" style="background:var(--panel);border-bottom:1px solid var(--panel);padding:.6rem .75rem;font-weight:600">${title}</div>`;
    const body = `<pre style="margin:0;padding:.75rem;border:0;overflow:auto"><code class="language-${title}">${escapedCode}</code></pre>`;
    return `<div class="md-code" style="margin:1rem 0;border:1px solid var(--border);border-radius:12px;overflow:hidden">${head}${body}</div>`;
  });

  // 7) Convert line-breaks to <br>
  html = html.replace(/\n/g, "<br>");

  // 8) Cleanup stray <br> around lists/tables/wrappers
  html = html
    .replace(/<br>\s*(<ul>)/g, "$1")
    .replace(/(<\/ul>)\s*<br>/g, "$1")
    .replace(/<br>\s*(<div class="md-table"[^>]*>)/g, "$1")
    .replace(/(<\/div>)\s*<br>/g, "$1")
    .replace(/<br>\s*(<div class="md-code"[^>]*>)/g, "$1")
    .replace(/(<\/div>)\s*<br>/g, "$1")
    .replace(/<br>\s*(<table\b[^>]*>)/g, "$1")
    .replace(/(<\/table>)\s*<br>/g, "$1")
    .replace(/<br>\s*(<blockquote>)/g, "$1") // New: Cleanup <br> before blockquote
    .replace(/(<\/blockquote>)\s*<br>/g, "$1"); // New: Cleanup <br> after blockquote

  // 9) Trim spaces/tabs and remove empty newline(s) immediately after <hr>, blockquote, and ul
  html = html
    .replace(/(<hr>)[ \t]+/g, "$1")              // remove spaces/tabs
    .replace(/(<hr>)(?:[ \t]*<br>)+/g, "$1")    // remove one or more blank lines (now <br>) after <hr>
    .replace(/(<\/blockquote>)(?:[ \t]*<br>)+/g, "$1") // New: Remove empty lines after blockquote
    .replace(/(<\/ul>)(?:[ \t]*<br>)+/g, "$1"); // New: Remove empty lines after ul

  return html;
}
