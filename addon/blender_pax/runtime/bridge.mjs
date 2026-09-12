import fs from "node:fs/promises";
import { convert } from "convert-pax";
import { decode } from "./decode.mjs";
const [requestPath] = process.argv.slice(2);
try {
  const request = JSON.parse(await fs.readFile(requestPath, "utf8"));
  if (request.operation === "export") {
    const stats = await convert(
      request.input,
      request.output,
      request.settings,
    );
    await fs.writeFile(request.result, JSON.stringify(stats));
  } else if (request.operation === "import") {
    await fs.writeFile(
      request.result,
      JSON.stringify(await decode(request.input, request.output)),
    );
  } else throw new Error("Unknown PAX bridge operation");
} catch (error) {
  console.error(error.stack || error.message);
  process.exitCode = 1;
}
