import { createContext, useContext } from "react";

export interface SnippetContextValue {
  baseUrl: string;
  token: string;
}

export const SnippetContext = createContext<SnippetContextValue>({
  baseUrl: "",
  token: "",
});

export function useSnippet(): SnippetContextValue {
  return useContext(SnippetContext);
}

// Подставляет {{BASE_URL}} и {{TOKEN}} в сниппет. Пустой Base URL остаётся как
// есть в плейсхолдере, пустой токен превращается в shell-переменную $TOKEN,
// чтобы скопированный curl работал без правок.
export function applySnippet(code: string, ctx: SnippetContextValue): string {
  const base = ctx.baseUrl.trim().replace(/\/+$/, "");
  const token = ctx.token.trim() || "$TOKEN";
  return code
    .replaceAll("{{BASE_URL}}", base || "{{BASE_URL}}")
    .replaceAll("{{TOKEN}}", token);
}
