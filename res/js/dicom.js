/*
 * Browser-only DICOM renderer. Local files are decoded with Cornerstone's
 * browser codecs and are never uploaded.
 */
(function (window) {
  "use strict";

  const IMPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2";
  const SUPPORTED_TRANSFER_SYNTAXES = new Set([
    IMPLICIT_VR_LITTLE_ENDIAN,
    "1.2.840.10008.1.2.1",
    "1.2.840.10008.1.2.1.99",
    "1.2.840.10008.1.2.2",
    "1.2.840.10008.1.2.5",
    "1.2.840.10008.1.2.4.50",
    "1.2.840.10008.1.2.4.51",
    "1.2.840.10008.1.2.4.57",
    "1.2.840.10008.1.2.4.70",
    "1.2.840.10008.1.2.4.80",
    "1.2.840.10008.1.2.4.81",
    "1.2.840.10008.1.2.4.90",
    "1.2.840.10008.1.2.4.91",
  ]);

  let loaderInitialized = false;

  function elementString(dataSet, tag) {
    const element = dataSet.elements[tag];
    return element ? dataSet.string(tag) || "" : "";
  }

  function firstNumber(dataSet, tag, fallback) {
    const value = elementString(dataSet, tag).split("\\")[0].trim();
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function firstUnsignedShort(dataSet, tag, fallback) {
    if (!dataSet.elements[tag]) return fallback;
    const value = dataSet.uint16(tag);
    return Number.isFinite(value) ? value : fallback;
  }

  function firstFinite(value, fallback) {
    const candidate = Array.isArray(value) ? value[0] : value;
    return Number.isFinite(candidate) ? candidate : fallback;
  }

  function isDicomFile(file) {
    return /\.dcm$/i.test(file.name || "") ||
      file.type === "application/dicom" ||
      file.type === "application/dicom+json" ||
      file.type === "application/octet-stream";
  }

  function ensureDecoder() {
    if (!window.dicomParser || !window.cornerstone || !window.cornerstoneWADOImageLoader) {
      throw new Error("The DICOM decoder is still loading. Please try again.");
    }

    if (loaderInitialized) return;

    window.cornerstoneWADOImageLoader.external.cornerstone = window.cornerstone;
    window.cornerstoneWADOImageLoader.external.dicomParser = window.dicomParser;
    window.cornerstoneWADOImageLoader.webWorkerManager.initialize({
      maxWebWorkers: Math.max(1, Math.min(window.navigator.hardwareConcurrency || 1, 2)),
      startWebWorkersOnDemand: true,
      taskConfiguration: {
        decodeTask: {
          initializeCodecsOnStartup: false,
          strict: false,
        },
      },
    });
    loaderInitialized = true;
  }

  function parseStudy(buffer) {
    try {
      return window.dicomParser.parseDicom(new Uint8Array(buffer));
    } catch (error) {
      throw new Error("This file is not a readable DICOM study.");
    }
  }

  function inspectStudy(dataSet) {
    const rows = firstUnsignedShort(dataSet, "x00280010", 0);
    const columns = firstUnsignedShort(dataSet, "x00280011", 0);
    const bitsAllocated = firstUnsignedShort(dataSet, "x00280100", 0);
    const samplesPerPixel = firstUnsignedShort(dataSet, "x00280002", 1);
    const photometric = elementString(dataSet, "x00280004").toUpperCase();
    const transferSyntax = elementString(dataSet, "x00020010") || IMPLICIT_VR_LITTLE_ENDIAN;
    const modality = elementString(dataSet, "x00080060");
    const declaredFrameCount = firstNumber(dataSet, "x00280008", 1);
    const frameCount = Math.max(1, Math.floor(declaredFrameCount));
    const pixelElement = dataSet.elements.x7fe00010 || dataSet.elements.x7fe00008;

    if (!rows || !columns || !pixelElement) {
      throw new Error("This DICOM does not contain a readable image.");
    }
    if (samplesPerPixel !== 1 || (photometric && photometric.indexOf("MONOCHROME") !== 0)) {
      throw new Error("Only monochrome DICOM images are supported for chest X-ray analysis.");
    }
    if (bitsAllocated !== 8 && bitsAllocated !== 16) {
      throw new Error(`Unsupported DICOM bit depth: ${bitsAllocated || "unknown"}. Export an 8-bit or 16-bit monochrome DICOM.`);
    }
    if (!SUPPORTED_TRANSFER_SYNTAXES.has(transferSyntax)) {
      throw new Error(
        `Unsupported DICOM transfer syntax ${transferSyntax}. Export this study as Explicit VR Little Endian, RLE, JPEG, JPEG-LS, JPEG 2000, or PNG.`
      );
    }

    return {
      rows,
      columns,
      frameCount,
      photometric,
      transferSyntax,
      modality,
    };
  }

  function installImagePlaneFallback(imageId, study) {
    if (study.modality) return null;

    const provider = function (type, requestedImageId) {
      if (type !== "imagePlaneModule" || requestedImageId !== imageId) return undefined;
      return {
        rows: study.rows,
        columns: study.columns,
      };
    };

    window.cornerstone.metaData.addProvider(provider, 1000);
    return provider;
  }

  function chooseFrame(fileName, frameCount) {
    const panel = document.getElementById("dicom-frame-selector");
    const name = document.getElementById("dicom-frame-file");
    const input = document.getElementById("dicom-frame-number");
    const total = document.getElementById("dicom-frame-total");
    const analyze = document.getElementById("dicom-frame-analyze");
    const cancel = document.getElementById("dicom-frame-cancel");
    const uploadStatus = document.getElementById("upload-status");

    if (!panel || !name || !input || !total || !analyze || !cancel) {
      throw new Error("This study contains multiple frames, but the frame selector is unavailable. Reload the page and try again.");
    }

    name.textContent = fileName;
    input.min = "1";
    input.max = String(frameCount);
    input.value = "1";
    total.textContent = `of ${frameCount}`;
    panel.hidden = false;
    if (uploadStatus) {
      uploadStatus.textContent = `Choose a frame from ${fileName} before analysis.`;
    }

    return new Promise((resolve, reject) => {
      function cleanup() {
        analyze.onclick = null;
        cancel.onclick = null;
        input.onkeydown = null;
        panel.hidden = true;
      }

      function confirmSelection() {
        const selectedFrame = Math.floor(Number(input.value));
        if (!Number.isFinite(selectedFrame) || selectedFrame < 1 || selectedFrame > frameCount) {
          input.focus();
          return;
        }
        cleanup();
        if (uploadStatus) {
          uploadStatus.textContent = `Decoding frame ${selectedFrame} of ${frameCount} from ${fileName}...`;
        }
        resolve(selectedFrame - 1);
      }

      analyze.onclick = confirmSelection;
      cancel.onclick = function () {
        cleanup();
        reject(new Error("Frame selection was cancelled."));
      };
      input.onkeydown = function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          confirmSelection();
        }
      };
      window.requestAnimationFrame(function () {
        input.focus();
        input.select();
      });
    });
  }

  function renderImage(image, study) {
    const values = image.getPixelData();
    const pixelCount = study.rows * study.columns;
    if (!values || values.length < pixelCount) {
      throw new Error("The selected DICOM frame is incomplete.");
    }

    const slope = firstFinite(image.slope, 1);
    const intercept = firstFinite(image.intercept, 0);
    const minimum = firstFinite(image.minPixelValue, 0);
    const maximum = firstFinite(image.maxPixelValue, minimum + 1);
    const center = firstFinite(image.windowCenter, NaN);
    const width = firstFinite(image.windowWidth, NaN);
    let low = minimum * slope + intercept;
    let high = maximum * slope + intercept;

    if (Number.isFinite(center) && Number.isFinite(width) && width > 1) {
      low = center - width / 2;
      high = center + width / 2;
    }
    if (!(high > low)) {
      high = low + 1;
    }

    const canvas = document.createElement("canvas");
    canvas.width = study.columns;
    canvas.height = study.rows;
    const context = canvas.getContext("2d");
    const imageData = context.createImageData(study.columns, study.rows);
    const invert = Boolean(image.invert) || study.photometric === "MONOCHROME1";

    for (let index = 0; index < pixelCount; index += 1) {
      let normalized = ((values[index] * slope + intercept) - low) / (high - low);
      normalized = Math.max(0, Math.min(1, normalized));
      if (invert) normalized = 1 - normalized;
      const intensity = Math.round(normalized * 255);
      const pixel = index * 4;
      imageData.data[pixel] = intensity;
      imageData.data[pixel + 1] = intensity;
      imageData.data[pixel + 2] = intensity;
      imageData.data[pixel + 3] = 255;
    }
    context.putImageData(imageData, 0, 0);
    return canvas.toDataURL("image/png");
  }

  function decoderError(error) {
    const cause = error && error.error ? error.error : error;
    if (cause && cause.message) return cause.message;
    if (typeof cause === "string") return cause;
    return "The selected DICOM frame could not be decoded.";
  }

  async function decodeFrame(file, frameIndex, study) {
    const loader = window.cornerstoneWADOImageLoader;
    const imageId = loader.wadouri.fileManager.add(file);
    const selectedImageId = `${imageId}?frame=${frameIndex}`;
    const fileIndex = Number(imageId.substring(imageId.indexOf(":") + 1));
    const cacheKey = String(fileIndex);
    const imagePlaneFallback = installImagePlaneFallback(selectedImageId, study);

    try {
      const image = await window.cornerstone.loadImage(selectedImageId);
      return renderImage(image, study);
    } catch (error) {
      throw new Error(`Could not decode this DICOM frame: ${decoderError(error)}`);
    } finally {
      try {
        loader.wadouri.dataSetCacheManager.unload(cacheKey);
      } catch (error) {
        // The cache entry is absent when parsing or decoding fails early.
      }
      if (imagePlaneFallback) {
        window.cornerstone.metaData.removeProvider(imagePlaneFallback);
      }
      loader.wadouri.fileManager.remove(fileIndex);
    }
  }

  window.isDicomFile = isDicomFile;
  window.decodeDicomFile = async function (file) {
    ensureDecoder();
    const buffer = await file.arrayBuffer();
    const dataSet = parseStudy(buffer);
    const study = inspectStudy(dataSet);
    const frameIndex = study.frameCount > 1
      ? await chooseFrame(file.name || "DICOM study", study.frameCount)
      : 0;
    const dataUrl = await decodeFrame(file, frameIndex, study);

    return {
      dataUrl,
      frameCount: study.frameCount,
      frameIndex,
      transferSyntax: study.transferSyntax,
      rows: study.rows,
      columns: study.columns,
    };
  };
})(window);