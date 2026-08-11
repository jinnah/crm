/** Type surface of assign-error-workflow.mjs for the vitest suite. */

export declare const ERROR_HANDLER_NAME: string;

export declare function expectedWorkflowNames(workflowsDir: string): {
  handler: string;
  targets: string[];
};

export declare function isErrorHandlerShaped(workflow: unknown): boolean;

export declare function runAssignment(options: {
  mode: string | undefined;
  baseUrl: string | undefined;
  apiKey: string | undefined;
  workflowsDir: string;
  fetchImpl?: (url: string, init?: Record<string, unknown>) => Promise<{
    ok: boolean;
    status: number;
    json(): Promise<unknown>;
  }>;
  log?: (line: string) => void;
}): Promise<number>;
