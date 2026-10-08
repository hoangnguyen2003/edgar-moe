/** Local Node SQLite test fixture. Never open user/provider database files. */
import { DatabaseSync } from "node:sqlite";
import { constants, openSync, closeSync, fstatSync } from "node:fs";
import { AdmissionLedger } from "./ledger.mjs";
import { SqliteAdmissionStore } from "./sqlite-store.mjs";

export function fixtureStorage(database) {
  return {
    sql: { exec(sql, ...args) {
      const statement = database.prepare(sql);
      return statement.columns().length ? statement.all(...args) : (statement.run(...args), []);
    } },
    transactionSync(fn) {
      database.exec("BEGIN IMMEDIATE");
      try { const result = fn(); database.exec("COMMIT"); return result; }
      catch (error) { database.exec("ROLLBACK"); throw error; }
    },
  };
}

export function openFixture(filename = ":memory:", { create = true, policy } = {}) {
  // Persistent fixture creation refuses existing files, including symlinks.
  // Reopening requires explicit create:false and is only for previously made test fixtures.
  if (filename !== ":memory:" && create) closeSync(openSync(filename, "wx", 0o600));
  if (filename !== ":memory:" && !create) {
    const descriptor = openSync(filename, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const details = fstatSync(descriptor);
      if (!details.isFile() || (details.mode & 0o077) !== 0) {
        throw new Error("Persistent fixture must be a private regular file");
      }
    } finally { closeSync(descriptor); }
  }
  const database = new DatabaseSync(filename);
  database.exec("PRAGMA busy_timeout = 5000");
  const storage = fixtureStorage(database);
  const store = new SqliteAdmissionStore(storage);
  const ledger = new AdmissionLedger(store, policy);
  if (create) {
    store.prepareOfflineFixture();
    if (ledger.initializeOfflineFixture().status !== "initialized") {
      database.close();
      throw new Error("Offline fixture initialization refused");
    }
  }
  return { database, storage, store, ledger, close: () => database.close() };
}
