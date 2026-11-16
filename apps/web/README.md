# Web application

## TypeScript compiler and lint API

`npm run build` uses the native TypeScript 7.0.2 `tsc` from the
`@typescript/native` alias. The `typescript` alias supplies the TypeScript 6.0.2
compatibility package for the JavaScript API required by typescript-eslint. This is the
[documented side-by-side setup](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/#running-side-by-side-with-typescript-60);
use ordinary `npm ci` without peer-dependency overrides. Node types are explicit
because the application project also type-checks tests that use `process.env`.
