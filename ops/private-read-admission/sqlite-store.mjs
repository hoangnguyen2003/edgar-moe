/** Synchronous SQLite Durable Object storage interface; no namespace/binding setup. */
export class SqliteAdmissionStore {
  #storage;
  constructor(storage) { this.#storage = storage; }

  // Explicit fixture setup only. Ordinary transactions never create tables/rows.
  prepareOfflineFixture() {
    this.#storage.transactionSync(() => {
      this.#storage.sql.exec("CREATE TABLE IF NOT EXISTS admission_state " +
        "(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), state TEXT NOT NULL)");
    });
  }

  transaction(fn) {
    return this.#storage.transactionSync(() => {
      const rows = [...this.#storage.sql.exec("SELECT state FROM admission_state WHERE singleton = 1")];
      if (rows.length > 1) throw new Error("Invalid admission state");
      const change = fn(rows.length === 0 ? null : rows[0].state);
      if (!change || typeof change !== "object" || !("result" in change)) {
        throw new Error("Invalid admission transaction");
      }
      if ("next" in change) {
        if (typeof change.next !== "string") throw new Error("Invalid admission transaction");
        this.#storage.sql.exec("INSERT INTO admission_state(singleton, state) VALUES(1, ?) " +
          "ON CONFLICT(singleton) DO UPDATE SET state = excluded.state", change.next);
      }
      return change.result;
    });
  }
}
