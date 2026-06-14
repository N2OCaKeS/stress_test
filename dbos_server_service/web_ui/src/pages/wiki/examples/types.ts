export type CodeLang = "curl" | "python";

export interface ApiExample {
  id: string;
  title: string;
  method: string;
  path: string;
  auth: string;
  description: string;
  curl: string;
  python: string;
  notes?: string;
}

export interface ApiSection {
  id: string;
  title: string;
  service: "auth" | "server" | "loging" | "secret" | "basics";
  description?: string;
  examples: ApiExample[];
}

export interface FlowStep {
  title: string;
  description: string;
  curl: string;
  python: string;
}

export interface ApiFlow {
  id: string;
  title: string;
  description: string;
  steps: FlowStep[];
  notes?: string;
}
