import assert from "node:assert/strict";
import { test } from "node:test";

import { shortCompanyName } from "./format.ts";

test("company names lose the share-class suffix", () => {
  assert.equal(shortCompanyName("Apple Inc. Common Stock"), "Apple Inc.");
  assert.equal(shortCompanyName("Alphabet Inc. Class A Common Stock"), "Alphabet Inc.");
  assert.equal(shortCompanyName("Tesla, Inc. Common Stock"), "Tesla, Inc.");
  assert.equal(shortCompanyName("Arm Holdings plc American Depositary Shares"),
    "Arm Holdings plc American Depositary Shares");
  assert.equal(shortCompanyName(null), null);
});
