(function (global) {
  "use strict";

  var TARGET_BYTES = 1536 * 1024;
  var MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
  var MAX_INPUT_BYTES = 50 * 1024 * 1024;
  var MAX_DIMENSION = 1920;
  var QUALITIES = [0.86, 0.78, 0.7, 0.62, 0.54];

  function formatBytes(value) {
    var bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1024) return bytes + "B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + "KB";
    return (bytes / (1024 * 1024)).toFixed(1) + "MB";
  }

  function readAsDataUrl(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(new Error("이미지를 읽을 수 없습니다.")); };
      reader.readAsDataURL(blob);
    });
  }

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var image = new Image();
      var url = URL.createObjectURL(file);
      image.onload = function () {
        URL.revokeObjectURL(url);
        resolve(image);
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("이미지 형식을 읽을 수 없습니다."));
      };
      image.src = url;
    });
  }

  function canvasBlob(canvas, type, quality) {
    return new Promise(function (resolve) {
      canvas.toBlob(function (blob) { resolve(blob); }, type, quality);
    });
  }

  function compressedName(name, type) {
    var stem = String(name || "image").replace(/\.[^.]+$/, "") || "image";
    var extension = type === "image/jpeg" ? ".jpg" : type === "image/png" ? ".png" : ".webp";
    return stem + "-compressed" + extension;
  }

  async function encodeCanvas(canvas, quality) {
    var webp = await canvasBlob(canvas, "image/webp", quality);
    if (webp && webp.size && webp.type === "image/webp") return webp;
    return canvasBlob(canvas, "image/jpeg", quality);
  }

  async function compress(file) {
    if (!file || !/^image\/(png|jpeg|gif|webp)$/i.test(file.type || "")) {
      throw new Error("PNG, JPG, GIF, WEBP 이미지만 첨부할 수 있습니다.");
    }
    if (file.size > MAX_INPUT_BYTES) {
      throw new Error("원본 이미지는 50MB 이하만 첨부할 수 있습니다.");
    }

    if (file.size <= TARGET_BYTES) {
      return {
        name: file.name || "image",
        type: file.type,
        data: await readAsDataUrl(file),
        size: file.size,
        originalSize: file.size,
        compressed: false,
        label: (file.name || "image") + " · " + formatBytes(file.size)
      };
    }

    var image = await loadImage(file);
    var naturalWidth = Math.max(1, image.naturalWidth || image.width || 1);
    var naturalHeight = Math.max(1, image.naturalHeight || image.height || 1);
    var initialScale = Math.min(1, MAX_DIMENSION / Math.max(naturalWidth, naturalHeight));
    var width = Math.max(1, Math.round(naturalWidth * initialScale));
    var height = Math.max(1, Math.round(naturalHeight * initialScale));
    var canvas = document.createElement("canvas");
    var context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("이 브라우저에서는 이미지 압축을 사용할 수 없습니다.");

    var result = null;
    for (var round = 0; round < 6; round += 1) {
      canvas.width = width;
      canvas.height = height;
      context.fillStyle = "#fff";
      context.fillRect(0, 0, width, height);
      context.drawImage(image, 0, 0, width, height);

      for (var qualityIndex = 0; qualityIndex < QUALITIES.length; qualityIndex += 1) {
        result = await encodeCanvas(canvas, QUALITIES[qualityIndex]);
        if (result && result.size <= TARGET_BYTES) break;
      }
      if (result && result.size <= TARGET_BYTES) break;
      width = Math.max(1, Math.round(width * 0.82));
      height = Math.max(1, Math.round(height * 0.82));
    }

    if (!result || !result.size || result.size > MAX_OUTPUT_BYTES) {
      throw new Error("이미지를 전송 가능한 크기로 압축하지 못했습니다.");
    }

    var outputName = compressedName(file.name, result.type);
    return {
      name: outputName,
      type: result.type,
      data: await readAsDataUrl(result),
      size: result.size,
      originalSize: file.size,
      compressed: true,
      width: canvas.width,
      height: canvas.height,
      label: outputName + " · " + formatBytes(file.size) + " → " + formatBytes(result.size)
    };
  }

  global.CandyCastImage = {
    compress: compress,
    formatBytes: formatBytes,
    maxInputBytes: MAX_INPUT_BYTES,
    targetBytes: TARGET_BYTES
  };
})(window);
