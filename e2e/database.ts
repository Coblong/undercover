import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

/** The throwaway database the e2e backend runs on. */
export function databasePath(): string {
  return join(tmpdir(), `undercover-e2e-${process.env.E2E_RUN ?? ""}.db`);
}

/** Run one statement against it, for what the API cannot set up without an agent. */
export function execute(sql: string, ...values: (string | number)[]): void {
  const database = new DatabaseSync(databasePath());
  try {
    database.prepare(sql).run(...values);
  } finally {
    database.close();
  }
}
