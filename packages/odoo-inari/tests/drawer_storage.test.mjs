import assert from "node:assert/strict";
import { test } from "node:test";
import { IndexedDbDrawerIntentStore } from "../inari_devices/static/src/drawer_intent_store.js";

for (const operation of ["put", "delete"]) {
    test(`Drawer Intent ${operation} waits for commit and rejects a late abort`, async () => {
        let transaction;
        const database = {
            close() {},
            transaction() {
                transaction = { objectStore: () => ({ put() {}, delete() {} }) };
                return transaction;
            },
        };
        const store = new IndexedDbDrawerIntentStore({ indexedDBApi: {
            open() {
                const request = { result: database };
                queueMicrotask(() => request.onsuccess());
                return request;
            },
        } });
        let settled = false;
        const pending = (operation === "put" ? store.put({ scope_key: "scope" }) : store.delete("scope"))
            .finally(() => { settled = true; });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(settled, false);
        transaction.error = new Error("Commit failed");
        transaction.onabort();
        await assert.rejects(pending, /Commit failed/);
    });
}
