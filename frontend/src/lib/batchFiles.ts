import { ApiError, validateUpload, type Capabilities } from "../api/client";

export type PreparedBatchFile = {
  file: File;
  relative_path: string;
  sha256: string | null;
  size_bytes: number;
  error: ApiError | null;
};

export function batchFilePath(file: File) {
  return file.webkitRelativePath || file.name;
}

async function readBytes(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () =>
      reject(new ApiError("Не удалось прочитать файл.", "file_read_error"));
    reader.readAsArrayBuffer(file);
  });
}

export async function prepareBatchFile(
  file: File,
  capabilities: Capabilities,
): Promise<PreparedBatchFile> {
  let error: ApiError | null = null;
  try {
    await validateUpload(
      file,
      capabilities.spectrum.wavelength_medium,
      capabilities,
    );
  } catch (cause) {
    error =
      cause instanceof ApiError
        ? cause
        : new ApiError("Не удалось проверить CSV.", "file_read_error");
  }
  let sha256: string | null = null;
  if (file.size <= capabilities.spectrum.max_upload_bytes) {
    try {
      const bytes = await readBytes(file);
      const hash = await crypto.subtle.digest("SHA-256", bytes);
      sha256 = Array.from(new Uint8Array(hash), (value) =>
        value.toString(16).padStart(2, "0"),
      ).join("");
    } catch {
      error = new ApiError(
        "Не удалось прочитать файл или вычислить SHA-256. Выберите файл повторно.",
        "file_read_error",
      );
    }
  }
  return {
    file,
    relative_path: batchFilePath(file),
    sha256,
    size_bytes: file.size,
    error,
  };
}

export function validateSelection(files: File[], maxFiles: number) {
  if (!files.length)
    throw new ApiError("Выберите папку или CSV-файлы.", "empty_batch");
  if (files.length > maxFiles)
    throw new ApiError(
      `В одной очереди допустимо до ${maxFiles} файлов. Выберите меньшую папку.`,
      "batch_file_limit",
    );
  const paths = files.map(batchFilePath);
  if (new Set(paths).size !== paths.length)
    throw new ApiError(
      "Есть файлы с одинаковым именем. Выберите папку, чтобы сохранить относительные пути и различить файлы.",
      "duplicate_file_path",
    );
}
