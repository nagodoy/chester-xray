"use strict";

const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const tf = require("@tensorflow/tfjs");

function emit(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function loadArtifacts(modelDirectory) {
  const modelPath = path.join(modelDirectory, "model.json");
  const descriptor = JSON.parse(fs.readFileSync(modelPath, "utf8"));
  const weightSpecs = descriptor.weightsManifest.flatMap((group) => group.weights);
  const buffers = descriptor.weightsManifest.flatMap((group) =>
    group.paths.map((filename) =>
      fs.readFileSync(path.join(modelDirectory, filename)),
    ),
  );
  const combined = Buffer.concat(buffers);
  const weightData = combined.buffer.slice(
    combined.byteOffset,
    combined.byteOffset + combined.byteLength,
  );

  return {
    modelTopology: descriptor.modelTopology,
    weightSpecs,
    weightData,
  };
}

async function main() {
  const modelDirectory = path.resolve(process.argv[2] || "");
  const config = JSON.parse(
    fs.readFileSync(path.join(modelDirectory, "config.json"), "utf8"),
  );

  tf.enableProdMode();
  await tf.setBackend("cpu");
  await tf.ready();

  const artifacts = loadArtifacts(modelDirectory);
  const model = await tf.loadGraphModel(tf.io.fromMemory(artifacts));
  const expectedPixels = config.IMAGE_SIZE * config.IMAGE_SIZE;

  emit({
    type: "ready",
    inputSize: config.IMAGE_SIZE,
    outputSize: config.LABELS.length,
    outputNode: config.OUTPUT_NODE,
  });

  const lines = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  for await (const line of lines) {
    if (!line.trim()) continue;

    let request;
    try {
      request = JSON.parse(line);
      if (!Array.isArray(request.pixels)) {
        throw new Error("pixels must be an array");
      }
      if (request.pixels.length !== expectedPixels) {
        throw new Error(
          `expected ${expectedPixels} pixels, received ${request.pixels.length}`,
        );
      }
      if (!request.pixels.every(Number.isFinite)) {
        throw new Error("pixels contain non-finite values");
      }

      const input = tf.tensor4d(request.pixels, [
        1,
        1,
        config.IMAGE_SIZE,
        config.IMAGE_SIZE,
      ]);
      const execution = model.execute(input, [config.OUTPUT_NODE]);
      const output = Array.isArray(execution) ? execution[0] : execution;

      try {
        const scores = Array.from(await output.data());
        if (
          scores.length !== config.LABELS.length ||
          !scores.every(Number.isFinite)
        ) {
          throw new Error("model returned an invalid score vector");
        }
        emit({ id: request.id, scores });
      } finally {
        input.dispose();
        if (Array.isArray(execution)) {
          execution.forEach((tensor) => tensor.dispose());
        } else {
          execution.dispose();
        }
      }
    } catch (error) {
      emit({
        id: request && request.id,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  model.dispose();
}

main().catch((error) => {
  emit({
    type: "fatal",
    error: error instanceof Error ? error.message : String(error),
  });
  process.exitCode = 1;
});