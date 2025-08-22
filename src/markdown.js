export function markdownToHTML(text) {
  // 0) Remove <think>...</think>/<thinking>...</thinking> blocks
  text = text.replace(
    /(^|\n)\s*<think>[\s\S]*?<\/think(?:ing)?>\s*(\n\s*\n)?/gi,
    (_, lead) => (lead ? '\n' : '')
  );

  // 1) Normalize line endings
  let tmp = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // 2) Extract code blocks and replace with placeholders
  const codeblocks = [];
  const placeholder = idx => `@@CODEBLOCK${idx}@@`;
  tmp = tmp.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    codeblocks.push({ lang, code });
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

  // 4.5) Unordered lists
  escaped = escaped.replace(
    /(^|\n)([ \t]*\* .+(?:\n[ \t]*\* .+)*)/g,
    (_, lead, listBlock) => {
      const items = listBlock
        .split(/\n/)
        .map(line => line.replace(/^[ \t]*\*\s+/, '').trim())
        .map(item => `<li>${item}</li>`)
        .join('');
      return `${lead}<ul>${items}</ul>`;
    }
  );

  // 5) Bold, italic, inline code
  let html = escaped
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(?<!\*)\*(.+?)\*(?!\*)/g, "<i>$1</i>")
    .replace(/`(.+?)`/g, "<code>$1</code>");

  // 6) Restore code blocks
  html = html.replace(/@@CODEBLOCK(\d+)@@/g, (_, idx) => {
    const { lang, code } = codeblocks[+idx];
    const escapedCode = code.replace(/</g, "<").replace(/>/g, ">");
    return `<pre><code class="language-${lang}">${escapedCode}</code></pre>`;
  });

  // 7) Convert line-breaks to <br>
  html = html.replace(/\n/g, "<br>");

  // 8) Cleanup stray <br> immediately before/after lists
  html = html
    .replace(/<br>\s*(<ul>)/g, "$1")
    .replace(/(<\/ul>)\s*<br>/g, "$1");

  return html;
}
