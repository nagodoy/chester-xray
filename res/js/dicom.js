/*
 * Small, browser-only DICOM renderer for the uncompressed monochrome studies
 * commonly used with this demo. Pixel data never leaves the browser.
 */
(function (window) {
  "use strict";

  const COMPRESSED_TRANSFER_SYNTAX = /^1\.2\.840\.10008\.1\.2\.(4|5)/;

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

  function isDicomFile(file) {
    return /\.dcm$/i.test(file.name || "") ||
      file.type === "application/dicom" ||
      file.type === "application/dicom+json" ||
      file.type === "application/octet-stream";
  }

  function readPixel(dataView, offset, bitsAllocated, signed, littleEndian) {
    if (bitsAllocated === 8) {
      const value = dataView.getUint8(offset);
      return signed && value > 127 ? value - 256 : value;
    }

    if (bitsAllocated === 16) {
      let value = dataView.getUint16(offset, littleEndian);
      return signed && value > 32767 ? value - 65536 : value;
    }

    throw new Error(`Unsupported DICOM bit depth: ${bitsAllocated}`);
  }

  function renderDataset(dataSet) {
    const transferSyntax = elementString(dataSet, "x00020010");
    if (COMPRESSED_TRANSFER_SYNTAX.test(transferSyntax)) {
      throw new Error("This DICOM uses compressed pixel data. Please export it as an uncompressed DICOM or PNG.");
    }

    // US attributes are binary 16-bit values; only DS/IS/UI/CS fields are
    // read through dataSet.string().
    const rows = firstUnsignedShort(dataSet, "x00280010", 0);
    const columns = firstUnsignedShort(dataSet, "x00280011", 0);
    const bitsAllocated = firstUnsignedShort(dataSet, "x00280100", 0);
    const bitsStored = firstUnsignedShort(dataSet, "x00280101", bitsAllocated);
    const samplesPerPixel = firstUnsignedShort(dataSet, "x00280002", 1);
    const pixelRepresentation = firstUnsignedShort(dataSet, "x00280103", 0);
    const frameCount = firstNumber(dataSet, "x00280008", 1);
    const photometric = elementString(dataSet, "x00280004").toUpperCase();
    const pixelElement = dataSet.elements.x7fe00010;

    if (!rows || !columns || !pixelElement) {
      throw new Error("This DICOM does not contain a readable image.");
    }
    if (samplesPerPixel !== 1 || (photometric && photometric.indexOf("MONOCHROME") !== 0)) {
      throw new Error("Only monochrome DICOM images are supported for chest X-ray analysis.");
    }
    if (bitsAllocated !== 8 && bitsAllocated !== 16) {
      throw new Error(`Unsupported DICOM bit depth: ${bitsAllocated || "unknown"}.`);
    }

    const littleEndian = transferSyntax !== "1.2.840.10008.1.2.2";
    const dataView = new DataView(dataSet.byteArray.buffer, dataSet.byteArray.byteOffset, dataSet.byteArray.byteLength);
    const bytesPerSample = bitsAllocated / 8;
    const pixelCount = rows * columns;
    const frameBytes = pixelCount * bytesPerSample;
    const availableBytes = dataSet.byteArray.byteLength - pixelElement.dataOffset;
    if (availableBytes < frameBytes) {
      throw new Error("The DICOM pixel data is incomplete.");
    }

    const values = new Float64Array(pixelCount);
    const mask = bitsStored < 32 ? (2 ** bitsStored) - 1 : 0xffffffff;
    let minimum = Infinity;
    let maximum = -Infinity;
    for (let index = 0; index < pixelCount; index += 1) {
      let value = readPixel(
        dataView,
        pixelElement.dataOffset + index * bytesPerSample,
        bitsAllocated,
        false,
        littleEndian
      );
      if (bitsStored < bitsAllocated) {
        value &= mask;
        if (pixelRepresentation === 1 && value >= 2 ** (bitsStored - 1)) {
          value -= 2 ** bitsStored;
        }
      } else if (pixelRepresentation === 1) {
        value = readPixel(
          dataView,
          pixelElement.dataOffset + index * bytesPerSample,
          bitsAllocated,
          true,
          littleEndian
        );
      }

      values[index] = value;
      minimum = Math.min(minimum, value);
      maximum = Math.max(maximum, value);
    }

    const slope = firstNumber(dataSet, "x00281053", 1);
    const intercept = firstNumber(dataSet, "x00281052", 0);
    const center = firstNumber(dataSet, "x00281050", NaN);
    const width = firstNumber(dataSet, "x00281051", NaN);
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
    canvas.width = columns;
    canvas.height = rows;
    const context = canvas.getContext("2d");
    const imageData = context.createImageData(columns, rows);
    const invert = photometric === "MONOCHROME1";
    for (let index = 0; index < values.length; index += 1) {
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

    return {
      dataUrl: canvas.toDataURL("image/png"),
      frameCount,
      rows,
      columns,
    };
  }

  window.isDicomFile = isDicomFile;
  window.decodeDicomFile = async function (file) {
    if (!window.dicomParser) {
      throw new Error("The DICOM decoder is still loading. Please try again.");
    }
    const buffer = await file.arrayBuffer();
    let dataSet;
    try {
      dataSet = window.dicomParser.parseDicom(new Uint8Array(buffer));
    } catch (error) {
      throw new Error("This file is not a readable DICOM study.");
    }
    return renderDataset(dataSet);
  };
})(window);