// Installs the resolver in `hook.mjs`. Used as `node --import ./tests/register.mjs`.
import { register } from "node:module";
register("./hook.mjs", import.meta.url);
