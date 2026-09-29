# src/shared/copy — user-facing text

Every user-facing string lives here as a typed constant, never inline in JSX (`FR-DS-011`,
`NFR-I18N-001`). This is also the enabler `FR-TOOL-016` (the custom ESLint rule banning inline JSX
text) checks against.

## Conventions

- One file per bounded context, e.g. `conversation.ts`, `iam.ts`. Cross-context or not-yet-owned
  copy (like the shared confirmation strings below) lives in a descriptively named file until its
  owning context's presentation layer exists.
- Plain string constants for fixed text; arrow functions for text with placeholders
  (`` `Delete "${title}"? This cannot be undone.` ``).
- No `index.ts` barrel — import directly from the file, matching the "no barrels" convention
  (`STD-001` §6).

## Copy parity with the specs (`STD-002` §6)

`legacy-strings.json` is the list of legacy strings a spec quotes verbatim that must still exist,
verbatim (a `{placeholder}` segment matches any run of non-quote characters), somewhere under this
directory. Add an entry whenever a spec quotes a legacy string your context's copy module must
preserve.

Run the check with:

```sh
npm run check:copy
```

`scripts/check-copy-parity.mjs` reads every `.ts` file under this directory and fails if any entry
in `legacy-strings.json` isn't found.
