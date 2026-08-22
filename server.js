const http = require("http");
const fs = require("fs");
const path = require("path");

const HOST = "0.0.0.0";
const PORT = Number(process.env.PORT || 5000);
const ROOT = __dirname;
const PUBLIC_DIRECTORIES = new Set(["examples", "models", "res"]);
const VENDOR_FILES = new Map([
  ["/vendor/dicom-parser.min.js", path.join(ROOT, "node_modules", "dicom-parser", "dist", "dicomParser.min.js")],
  ["/vendor/cornerstone.min.js", path.join(ROOT, "node_modules", "cornerstone-core", "dist", "cornerstone.min.js")],
  [
    "/vendor/cornerstone-wado-image-loader.min.js",
    path.join(ROOT, "node_modules", "cornerstone-wado-image-loader", "dist", "cornerstoneWADOImageLoader.bundle.min.js"),
  ],
  [
    "/vendor/index.worker.bundle.min.worker.js",
    path.join(ROOT, "node_modules", "cornerstone-wado-image-loader", "dist", "index.worker.bundle.min.worker.js"),
  ],
  [
    "/vendor/charlswasm_decode.wasm",
    path.join(ROOT, "node_modules", "@cornerstonejs", "codec-charls", "dist", "charlswasm_decode.wasm"),
  ],
  [
    "/vendor/openjpegwasm_decode.wasm",
    path.join(ROOT, "node_modules", "@cornerstonejs", "codec-openjpeg", "dist", "openjpegwasm_decode.wasm"),
  ],
  [
    "/vendor/openjphjs.wasm",
    path.join(ROOT, "node_modules", "@cornerstonejs", "codec-openjph", "dist", "openjphjs.wasm"),
  ],
  [
    "/vendor/libjpegturbowasm_decode.wasm",
    path.join(ROOT, "node_modules", "@cornerstonejs", "codec-libjpeg-turbo-8bit", "dist", "libjpegturbowasm_decode.wasm"),
  ],
]);

const MIME_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".csv": "text/csv; charset=utf-8",
  ".gif": "image/gif",
  ".htm": "text/html; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".wasm": "application/wasm",
  ".woff2": "font/woff2",
  ".xml": "application/xml; charset=utf-8",
};

function resolveRequest(requestUrl) {
  const pathname = decodeURIComponent(new URL(requestUrl, "http://localhost").pathname);

  if (pathname === "/") {
    return path.join(ROOT, "index.htm");
  }

  if (pathname === "/index.htm") {
    return path.join(ROOT, "index.htm");
  }

  if (VENDOR_FILES.has(pathname)) {
    return VENDOR_FILES.get(pathname);
  }

  const segments = pathname.split("/").filter(Boolean);
  if (segments.some((segment) => segment.startsWith("."))) {
    return null;
  }

  const [publicDirectory, ...relativePath] = segments;
  if (!PUBLIC_DIRECTORIES.has(publicDirectory) || relativePath.length === 0) {
    return null;
  }

  return path.join(ROOT, publicDirectory, ...relativePath);
}

const server = http.createServer((request, response) => {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end("Method not allowed");
    return;
  }

  let filePath;
  try {
    filePath = resolveRequest(request.url);
  } catch {
    response.writeHead(400);
    response.end("Bad request");
    return;
  }

  if (!filePath) {
    response.writeHead(403);
    response.end("Forbidden");
    return;
  }

  fs.stat(filePath, (statError, stats) => {
    if (statError || !stats.isFile()) {
      response.writeHead(404);
      response.end("Not found");
      return;
    }

    const contentType = MIME_TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream";
    response.writeHead(200, {
      "Cache-Control": filePath.includes(`${path.sep}models${path.sep}`) ? "public, max-age=31536000, immutable" : "no-cache",
      "Content-Length": stats.size,
      "Content-Type": contentType,
    });

    if (request.method === "HEAD") {
      response.end();
      return;
    }

    fs.createReadStream(filePath).on("error", () => {
      if (!response.headersSent) {
        response.writeHead(500);
      }
      response.end("Unable to read file");
    }).pipe(response);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Chester is serving on http://${HOST}:${PORT}`);
});