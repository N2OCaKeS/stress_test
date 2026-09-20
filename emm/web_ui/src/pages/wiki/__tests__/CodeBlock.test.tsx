import { describe, it, expect } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { CodeBlock } from "@/pages/wiki/CodeBlock";
import { sanitizeShikiHtml } from "@/pages/wiki/shikiHtml";
import { applySnippet } from "@/pages/wiki/snippet";

const PAYLOADS = [
  `<script>window.__pwned = 1</script>`,
  `"><img src=x onerror="window.__pwned=1">`,
  `'; </span><svg onload=window.__pwned=1> #`,
  `</code></pre><iframe src="javascript:window.__pwned=1"></iframe>`,
];

function renderBlock(code: string, lang: "bash" | "python" | "json" = "bash") {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <CodeBlock code={code} lang={lang} />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("sanitizeShikiHtml", () => {
  it("пропускает обычную разметку Shiki", () => {
    const html =
      '<pre class="shiki github-dark" style="background-color:#24292e;color:#e1e4e8" tabindex="0">' +
      '<code><span class="line"><span style="color:#79B8FF">curl</span></span></code></pre>';
    const out = sanitizeShikiHtml(html);
    expect(out).not.toBeNull();
    expect(out).toContain('style="color:#79B8FF"');
  });

  it.each([
    ["script", "<pre><code><script>alert(1)</script></code></pre>"],
    ["img с onerror", '<pre><code><img src=x onerror="alert(1)"></code></pre>'],
    ["on*-атрибут на span", '<pre><code><span onclick="alert(1)">a</span></code></pre>'],
    ["ссылка", '<pre><code><a href="javascript:alert(1)">a</a></code></pre>'],
    ["url() в style", '<pre><code><span style="background:url(http://evil/x)">a</span></code></pre>'],
    ["комментарий", "<pre><code><!-- x --></code></pre>"],
    ["iframe", '<pre><code></code></pre><iframe src="//evil"></iframe>'],
  ])("отклоняет: %s", (_name, html) => {
    expect(sanitizeShikiHtml(html)).toBeNull();
  });

  it("отклоняет пустой ввод", () => {
    expect(sanitizeShikiHtml("")).toBeNull();
  });

  it("не выдаёт наружу ничего, кроме pre/code/span", () => {
    const out = sanitizeShikiHtml(
      '<pre class="shiki"><code><span>&lt;script&gt;alert(1)&lt;/script&gt;</span></code></pre>',
    );
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script");
  });
});

describe("CodeBlock — значения из формы wiki не превращаются в разметку", () => {
  it.each(PAYLOADS)("baseUrl/token/pat = %s", async (payload) => {
    const code = applySnippet(
      'curl -H "Authorization: Bearer {{TOKEN}}" {{BASE_URL}}/api/auth/v1/me',
      { baseUrl: payload, token: payload },
    );
    const { container } = renderBlock(code);

    await waitFor(() => expect(container.querySelector(".wiki-shiki")).not.toBeNull(), {
      timeout: 10_000,
    });

    const forbidden = container.querySelectorAll(".wiki-shiki :is(script, img, svg, iframe, a, [onerror], [onload])");
    expect(forbidden).toHaveLength(0);
    for (const el of Array.from(container.querySelectorAll(".wiki-shiki *"))) {
      expect(["PRE", "CODE", "SPAN"]).toContain(el.tagName);
      for (const attr of Array.from(el.attributes)) {
        expect(attr.name.startsWith("on")).toBe(false);
      }
    }
    // Значение осталось видимым текстом, а не разметкой.
    expect(container.querySelector(".wiki-shiki")?.textContent).toContain(payload.slice(0, 12));
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("python/json тоже экранируются", async () => {
    const { container } = renderBlock(`token = "${PAYLOADS[1]}"`, "python");
    await waitFor(() => expect(container.querySelector(".wiki-shiki")).not.toBeNull(), {
      timeout: 10_000,
    });
    expect(container.querySelectorAll(".wiki-shiki :is(img, script)")).toHaveLength(0);
  });
});
